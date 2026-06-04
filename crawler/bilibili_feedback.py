# -*- coding: utf-8 -*-
import datetime
import glob
import json
import os
import re
from typing import Dict, Iterable, List, Tuple


BILIBILI_ARTICLE_MANAGER_URL = "https://member.bilibili.com/platform/upload-manager/article"
BILIBILI_MEMBER_ARCHIVES_URL = "https://member.bilibili.com/x/web/archives"
_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DEFAULT_METRICS_PATH = os.path.join(_PROJECT_DIR, "deepContent", "deep_feedback_metrics.json")
_OVERVIEW_LABELS = ["播放量", "涨粉", "取关", "点赞", "弹幕", "评论", "分享", "收藏", "投币"]
_PLAY_ANALYSIS_LABELS = ["封标点击率", "封面点击率", "3秒跳出率", "三秒跳出率", "互动率", "播转粉率", "平均观看时长", "完播率"]


def _number(value, default: float = 0.0) -> float:
    # B站接口和页面里可能出现 1,024、1.2万 这类展示值，这里统一转成数字。
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "")
    if not text:
        return default
    multiplier = 1.0
    if text.endswith("万"):
        multiplier = 10000.0
        text = text[:-1]
    elif text.endswith("亿"):
        multiplier = 100000000.0
        text = text[:-1]
    text = text.rstrip("%")
    try:
        return float(text) * multiplier
    except ValueError:
        return default


def _first_value(row: Dict, keys: List[str], default=0):
    for key in keys:
        if key in row and row.get(key) not in (None, ""):
            return row.get(key)
    return default


def _has_real_value(value) -> bool:
    # B站详情页没有数据时常显示空值或短横线，这类值不能写进回流报告。
    if value is None:
        return False
    if isinstance(value, str) and value.strip() in ("", "-", "--", "暂无", "暂未更新"):
        return False
    return True


def _first_existing_value(row: Dict, keys: Iterable[str]):
    # 和 _first_value 不同，这里不提供默认值；字段不存在就明确返回“未找到”。
    not_ready = set()
    if isinstance(row.get("not_ready_field"), list):
        not_ready = {str(item) for item in row.get("not_ready_field", [])}
    for key in keys:
        if key in not_ready:
            continue
        if key in row and _has_real_value(row.get(key)) and not isinstance(row.get(key), (dict, list)):
            return True, row.get(key)
    return False, None


def _text_matches_alias(text: str, aliases: Iterable[str]) -> bool:
    # 详情页接口有时用中文标签，有时用英文 key；统一做一次小写和空白归一。
    normalized = str(text or "").strip().lower().replace(" ", "").replace("_", "")
    for alias in aliases:
        alias_text = str(alias or "").strip().lower().replace(" ", "").replace("_", "")
        if alias_text and (normalized == alias_text or alias_text in normalized):
            return True
    return False


def _value_from_labeled_dict(raw: Dict, aliases: Iterable[str]):
    # 兼容 [{name: "播放量", value: 93}] 这类卡片式返回，只认真实标签旁边的真实值。
    label_keys = ("name", "title", "label", "key", "text", "desc", "metric", "index_name")
    value_keys = ("value", "val", "num", "count", "cnt", "total", "rate", "percent", "display_value")
    label = ""
    for key in label_keys:
        value = raw.get(key)
        if isinstance(value, str) and _text_matches_alias(value, aliases):
            label = value
            break
    if not label:
        return False, None
    for key in value_keys:
        value = raw.get(key)
        if _has_real_value(value) and value != label and not isinstance(value, (dict, list)):
            return True, value
    return False, None


def _find_detail_value(payloads: List[Dict], aliases: Iterable[str]):
    # 递归扫描详情页接口响应；只返回接口里确实出现的字段，避免把缺失指标补成 0。
    for payload in payloads:
        for raw in _walk_dicts(payload):
            found, value = _first_existing_value(raw, aliases)
            if found:
                return True, value
            found, value = _value_from_labeled_dict(raw, aliases)
            if found:
                return True, value
    return False, None


def _detail_int(value) -> int:
    # 数据总览里的播放、点赞、评论等是计数，写入报告前统一转成整数。
    return int(_number(value, 0))


def _walk_dicts(value):
    # 递归扫接口 JSON，避免绑定 B站某一个不稳定的返回字段名。
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_dicts(child)


def _balanced_json_from(text: str, start: int) -> str:
    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start:index + 1]
    return ""


def _load_json_like(payload):
    if isinstance(payload, (dict, list)):
        return payload
    text = str(payload or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 页面脚本里常见 window.__INITIAL_STATE__ = {...}; 这种形式，提取其中的 JSON。
    for marker in ("window.__INITIAL_STATE__", "__INITIAL_STATE__"):
        marker_index = text.find(marker)
        if marker_index < 0:
            continue
        start = text.find("{", marker_index)
        if start < 0:
            continue
        block = _balanced_json_from(text, start)
        if not block:
            continue
        try:
            return json.loads(block)
        except json.JSONDecodeError:
            continue

    start = text.find("{")
    if start >= 0:
        block = _balanced_json_from(text, start)
        if block:
            try:
                return json.loads(block)
            except json.JSONDecodeError:
                return None
    return None


def _title_from(row: Dict) -> str:
    return str(_first_value(
        row,
        ["title", "name", "archive_title", "article_title", "publish_title", "稿件标题"],
        "",
    )).strip()


def build_bilibili_detail_metric_row(archive: Dict, overview_payloads: List[Dict], play_payloads: List[Dict]) -> Dict:
    # 只构造“点击数据按钮后详情页里实际出现”的指标行；缺失字段不补默认值。
    title = _title_from(archive)
    row = {
        "source": "bilibili_detail_page",
        "aid": archive.get("aid") or archive.get("AID") or archive.get("id"),
        "bvid": archive.get("bvid") or archive.get("BVID") or archive.get("bv_id"),
        "episode": title,
        "publish_title": title,
    }
    overview_fields: List[Tuple[str, List[str]]] = [
        ("views", ["views", "view", "vv", "play", "播放量", "播放"]),
        ("followers", ["followers", "fans", "increase_fans", "fans_inc", "涨粉", "新增粉丝"]),
        ("unfollows", ["unfollows", "unfollow", "cancel_follow", "取关", "取消关注"]),
        ("likes", ["likes", "like", "点赞"]),
        ("danmaku", ["danmaku", "dm", "弹幕"]),
        ("comments", ["comments", "comment", "reply", "replies", "评论"]),
        ("shares", ["shares", "share", "分享"]),
        ("favorites", ["favorites", "favorite", "fav", "收藏"]),
        ("coins", ["coins", "coin", "投币"]),
    ]
    play_fields: List[Tuple[str, List[str]]] = [
        ("click_rate", ["cover_click_rate", "cover_click", "click_rate", "tm_rate", "封标点击率", "封面点击率", "点击率"]),
        ("three_second_exit_rate", ["three_second_exit_rate", "three_second_bounce_rate", "3s_jump_rate", "crash_rate", "3秒跳出率", "三秒跳出率"]),
        ("interaction_rate", ["interaction_rate", "interact_rate", "互动率"]),
        ("play_follower_rate", ["play_follower_rate", "play_to_follower_rate", "play_trans_fan_rate", "播放转粉率", "播转粉率"]),
        ("avg_view_seconds", ["avg_view_seconds", "average_view_duration", "avg_watch_time", "avg_play_time", "avg_play_time_int", "平均观看秒数", "平均观看时长"]),
        ("completion_rate", ["completion_rate", "finish_rate", "full_play_ratio", "完播率"]),
    ]

    sections: List[str] = []
    overview_found = False
    for field, aliases in overview_fields:
        found, value = _find_detail_value(overview_payloads, aliases)
        if found:
            row[field] = _detail_int(value)
            overview_found = True
    if overview_found:
        sections.append("data_overview")

    play_found = False
    for field, aliases in play_fields:
        found, value = _find_detail_value(play_payloads, aliases)
        if found:
            row[field] = value
            play_found = True
    if play_found:
        sections.append("play_analysis")

    if not sections:
        return {}
    row["metric_sections"] = sections
    return row


def log_bilibili_detail_metric_row(row: Dict, index: int, total: int) -> None:
    # UI 会把子进程 stdout 直接写进日志框；每采到一条详情数据就立刻打印，方便逐条核对。
    payload = json.dumps(row, ensure_ascii=False, sort_keys=True)
    print(f"[B站数据回流] 已采集第 {index}/{total} 条详情数据：{payload}", flush=True)


def _log_bilibili_detail_metric_missing(archive: Dict, index: int, total: int) -> None:
    # 没采到详情指标也要在日志里说明是哪条视频，避免用户误以为程序静默跳过。
    payload = json.dumps(
        {
            "aid": archive.get("aid"),
            "bvid": archive.get("bvid"),
            "publish_title": archive.get("title") or "",
            "reason": "详情页没有采集到数据总览或播放分析指标",
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    print(f"[B站数据回流] 第 {index}/{total} 条未采集到详情数据：{payload}", flush=True)


def _metric_row_from(raw: Dict) -> Dict:
    stat = raw.get("stat") if isinstance(raw.get("stat"), dict) else {}
    merged = dict(raw)
    # B站部分接口会把标题放在 archive 子对象里，统计放在 stat 子对象里，需要先合并标题侧字段。
    for child_key in ("archive", "Archive", "article", "Article", "video", "Video"):
        child = raw.get(child_key)
        if isinstance(child, dict):
            merged.update({key: value for key, value in child.items() if key not in merged})
    merged.update({key: value for key, value in stat.items() if key not in merged})
    title = _title_from(merged)
    if not title:
        return {}

    views = int(_number(_first_value(merged, ["views", "view", "vv", "play", "read", "click", "播放量", "阅读量"], 0)))
    likes = int(_number(_first_value(merged, ["likes", "like", "点赞"], 0)))
    comments = int(_number(_first_value(merged, ["comments", "comment", "reply", "replies", "评论"], 0)))
    shares = int(_number(_first_value(merged, ["shares", "share", "分享"], 0)))
    coins = int(_number(_first_value(merged, ["coins", "coin", "投币"], 0)))
    favorites = int(_number(_first_value(merged, ["favorites", "favorite", "fav", "收藏"], 0)))
    if not any([views, likes, comments, shares, coins, favorites]):
        return {}

    return {
        "episode": title,
        "publish_title": title,
        "views": views,
        "likes": likes,
        "comments": comments,
        "shares": shares,
        "coins": coins,
        "favorites": favorites,
    }


def _dedupe_rows(rows: List[Dict]) -> List[Dict]:
    deduped: Dict[str, Dict] = {}
    for row in rows:
        key = row.get("publish_title") or row.get("episode")
        if not key:
            continue
        old = deduped.get(key)
        if not old or row.get("views", 0) >= old.get("views", 0):
            deduped[key] = row
    return list(deduped.values())


def extract_bilibili_metric_rows(payload_or_text) -> List[Dict]:
    # 从 B站接口 JSON 或页面初始状态里抽取现有回流报告能消费的指标字段。
    payload = _load_json_like(payload_or_text)
    rows: List[Dict] = []
    for raw in _walk_dicts(payload):
        metric_row = _metric_row_from(raw)
        if metric_row:
            rows.append(metric_row)
    return _dedupe_rows(rows)


def _default_chrome_user_data_dir() -> str:
    env_dir = os.environ.get("OPENNEWSBRIEF_CHROME_USER_DATA_DIR")
    if env_dir:
        return env_dir
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        return ""
    return os.path.join(local_app_data, "Google", "Chrome", "User Data")


def _default_bilibili_cookie_path() -> str:
    env_path = os.environ.get("OPENNEWSBRIEF_BILIBILI_COOKIE_FILE")
    if env_path:
        return env_path
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        return ""
    candidates = glob.glob(os.path.join(local_app_data, "bbup-app", "data", "*.json"))
    if not candidates:
        return ""
    return max(candidates, key=os.path.getmtime)


def _load_bilibili_cookies(cookie_path: str = None) -> tuple[Dict[str, str], str]:
    # 优先复用 bbup-app 已登录 cookie，避免 Playwright 反复占用 Chrome 用户目录。
    path = cookie_path or _default_bilibili_cookie_path()
    if not path or not os.path.exists(path):
        raise RuntimeError("未找到 B站登录 cookie 文件，请设置 OPENNEWSBRIEF_BILIBILI_COOKIE_FILE")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    cookie_items = data.get("cookie_info", {}).get("cookies", []) if isinstance(data, dict) else []
    cookies = {
        str(item.get("name")): str(item.get("value"))
        for item in cookie_items
        if isinstance(item, dict) and item.get("name") and item.get("value")
    }
    mid = str(data.get("token_info", {}).get("mid") or cookies.get("DedeUserID") or "").strip()
    if not cookies.get("SESSDATA"):
        raise RuntimeError("B站 cookie 文件缺少 SESSDATA，无法采集创作中心数据")
    return cookies, mid


def _requests_get(*args, **kwargs):
    # requests 只在接口采集时才需要，惰性导入可以避免未装依赖时影响其它单元测试。
    import requests
    return requests.get(*args, **kwargs)


def _archive_from_member_item(raw: Dict) -> Dict:
    # 稿件列表接口只用来拿详情页入口所需的身份信息，不把列表统计写入回流报表。
    merged = dict(raw)
    for child_key in ("archive", "Archive", "article", "Article", "video", "Video"):
        child = raw.get(child_key)
        if isinstance(child, dict):
            merged.update({key: value for key, value in child.items() if key not in merged})
    title = _title_from(merged)
    if not title:
        return {}
    return {
        "aid": merged.get("aid") or merged.get("AID") or merged.get("id"),
        "bvid": merged.get("bvid") or merged.get("BVID") or merged.get("bv_id"),
        "title": title,
        "duration": merged.get("duration"),
    }


def _fetch_bilibili_archive_index(limit: int = 50, cookie_path: str = None) -> Tuple[List[Dict], Dict[str, str], str]:
    # 这里拿到的是视频索引，不是分析指标；后续仍然必须进入详情页点击“数据”采集。
    cookies, mid = _load_bilibili_cookies(cookie_path)
    archives: List[Dict] = []
    page = 1
    page_size = min(max(limit, 1), 50)
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": BILIBILI_ARTICLE_MANAGER_URL,
    }
    while len(archives) < limit:
        response = _requests_get(
            BILIBILI_MEMBER_ARCHIVES_URL,
            params={"status": "pubed", "pn": page, "ps": page_size, "coop": 1},
            cookies=cookies,
            headers=headers,
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("code") != 0:
            raise RuntimeError(f"B站稿件索引接口返回异常：{payload.get('message') or payload.get('code')}")
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        page_items = data.get("arc_audits") or []
        page_archives = [_archive_from_member_item(item) for item in page_items if isinstance(item, dict)]
        archives.extend([item for item in page_archives if item])
        page_info = data.get("page") if isinstance(data.get("page"), dict) else {}
        total = int(_number(page_info.get("count"), len(archives)))
        if not page_items or page * page_size >= total:
            break
        page += 1
    return archives[:limit], cookies, mid


def _playwright_cookie_items(cookies: Dict[str, str]) -> List[Dict]:
    # 把 bbup-app cookie 转成 Playwright 可注入格式，让浏览器真实打开创作中心详情页。
    return [
        {"name": name, "value": value, "domain": ".bilibili.com", "path": "/"}
        for name, value in cookies.items()
        if name and value
    ]


def _is_bilibili_metric_response(url: str) -> bool:
    url = url.lower()
    if "bilibili.com" not in url:
        return False
    return any(token in url for token in ("upload-manager", "archive", "article", "stat", "web/archive"))


def _payload_from_response(response):
    # 网络响应能转 JSON 就转 JSON；转不了时保留文本，后续只抽其中真实存在的字段。
    try:
        return response.json()
    except Exception:
        try:
            return response.text()
        except Exception:
            return None


def _filter_detail_payload_by_bvid(value, bvid: str):
    # 详情页的对比接口会在同一个响应里带多条视频；这里按 bvid 只保留当前视频，防止串数据。
    target_bvid = str(bvid or "").strip().lower()
    if not target_bvid:
        return value
    if isinstance(value, list):
        matched = [
            item for item in value
            if isinstance(item, dict) and str(item.get("bvid") or "").strip().lower() == target_bvid
        ]
        if matched:
            return matched
        return [_filter_detail_payload_by_bvid(item, target_bvid) for item in value]
    if isinstance(value, dict):
        item_bvid = str(value.get("bvid") or "").strip().lower()
        if item_bvid and item_bvid != target_bvid:
            return {}
        return {key: _filter_detail_payload_by_bvid(child, target_bvid) for key, child in value.items()}
    return value


def _visible_metric_payload_from_text(text: str, labels: List[str]) -> Dict:
    # 页面 DOM 文本也是详情页真实展示数据，只按标签相邻值提取，找不到就不写。
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    metrics = []
    label_set = set(labels)
    for index, line in enumerate(lines):
        matched_label = ""
        value = ""
        for label in labels:
            if line == label:
                matched_label = label
                break
            if line.startswith(label):
                tail = re.sub(r"^[：:\s]+", "", line[len(label):].strip())
                if _has_real_value(tail):
                    matched_label = label
                    value = tail
                    break
        if not matched_label:
            continue
        if not value:
            for next_line in lines[index + 1:index + 4]:
                if next_line in label_set:
                    break
                if _has_real_value(next_line):
                    value = next_line
                    break
        if _has_real_value(value):
            metrics.append({"label": matched_label, "value": value})
    return {"visible_metrics": metrics} if metrics else {}


def _collect_visible_metric_payload(page, labels: List[str]) -> Dict:
    # 作为网络接口响应之外的真实页面补充，只读取当前详情页屏幕文字，不生成任何默认指标。
    try:
        text = page.evaluate("() => document.body ? document.body.innerText : ''")
    except Exception:
        return {}
    return _visible_metric_payload_from_text(text, labels)


def _click_detail_tab(page, tab_text: str):
    # B站详情页是前端路由，点击 tab 后等网络请求自然完成即可。
    try:
        page.get_by_text(tab_text, exact=True).first.click(timeout=8000)
    except Exception:
        return
    try:
        page.wait_for_load_state("networkidle", timeout=10000)
    except Exception:
        pass
    page.wait_for_timeout(1500)


def _click_data_button_from_list(context, page, index: int, archive: Dict = None):
    # 严格按用户说明从列表页点击“数据”按钮进入详情，而不是直接消费列表指标。
    archive = archive or {}
    bvid = str(archive.get("bvid") or "").strip()
    if bvid:
        # B站列表里的“数据”是带 bvid 的链接，按 bvid 点能避免误点导航里的其它“数据”文本。
        button = page.locator(f'a.bili-btn[href*="/data/{bvid}"]').first
    else:
        # 没有 bvid 时再按顺序兜住同类链接，但仍然只点详情数据链接。
        button = page.locator('a.bili-btn[href*="/data/"]').nth(index)
    try:
        with context.expect_page(timeout=3000) as page_info:
            button.click(timeout=10000)
        detail_page = page_info.value
        detail_page.wait_for_load_state("domcontentloaded", timeout=15000)
        return detail_page
    except Exception:
        try:
            button.click(timeout=10000)
        except Exception:
            pass
    try:
        page.wait_for_load_state("domcontentloaded", timeout=15000)
    except Exception:
        pass
    return page


def _detail_tabs_visible(page) -> bool:
    # 只有出现详情页的“数据总览/播放分析”tab，才允许采集，避免误读列表页。
    try:
        page.get_by_text("数据总览", exact=True).first.wait_for(timeout=8000)
        page.get_by_text("播放分析", exact=True).first.wait_for(timeout=8000)
        return True
    except Exception:
        return False


def _is_detail_data_route(page, archive: Dict) -> bool:
    # 详情页有时 DOM 白屏但接口已返回；只要 URL 已进入对应 bvid 的数据页，就继续消费接口响应。
    bvid = str((archive or {}).get("bvid") or "").strip()
    return bool(bvid and f"/data/{bvid}" in page.url)


def scrape_bilibili_api_metrics(output_path: str = None, limit: int = 50, cookie_path: str = None) -> Dict:
    # 直接调用创作中心稿件接口，拿到的播放、点赞、评论等字段都来自 B站真实返回。
    output_path = output_path or _DEFAULT_METRICS_PATH
    cookies, mid = _load_bilibili_cookies(cookie_path)
    rows: List[Dict] = []
    page = 1
    page_size = min(max(limit, 1), 50)
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": BILIBILI_ARTICLE_MANAGER_URL,
    }
    while len(rows) < limit:
        response = _requests_get(
            BILIBILI_MEMBER_ARCHIVES_URL,
            params={"status": "pubed", "pn": page, "ps": page_size, "coop": 1},
            cookies=cookies,
            headers=headers,
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("code") != 0:
            raise RuntimeError(f"B站稿件接口返回异常：{payload.get('message') or payload.get('code')}")
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        page_rows = extract_bilibili_metric_rows({"data": {"arc_audits": data.get("arc_audits") or []}})
        rows.extend(page_rows)
        page_info = data.get("page") if isinstance(data.get("page"), dict) else {}
        total = int(_number(page_info.get("count"), len(rows)))
        if not page_rows or page * page_size >= total:
            break
        page += 1

    rows = _dedupe_rows(rows)[:limit]
    if not rows:
        raise RuntimeError("未从 B站稿件接口采集到有效指标")
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "source": "bilibili_member_archives",
                "url": BILIBILI_MEMBER_ARCHIVES_URL,
                "mid": mid,
                "fetched_at": datetime.datetime.now().isoformat(timespec="seconds"),
                "videos": rows,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )
    return {
        "metrics_path": output_path,
        "metric_count": len(rows),
        "source": "bilibili_member_archives",
        "error": "",
    }


def scrape_bilibili_article_metrics(output_path: str = None, limit: int = 50, user_data_dir: str = None) -> Dict:
    # 数据回流只采集详情页字段：列表接口只拿视频索引，不能把列表统计写进报表。
    output_path = output_path or _DEFAULT_METRICS_PATH
    archives, cookies, mid = _fetch_bilibili_archive_index(limit=limit)
    if not archives:
        raise RuntimeError("未从 B站稿件列表拿到可点击详情的视频索引")

    # 页面抓取只放在这个函数里，外层 deep_series 只关心落地后的 JSON 指标文件。
    from playwright.sync_api import sync_playwright

    rows: List[Dict] = []
    browser = None
    try:
        with sync_playwright() as playwright:
            if user_data_dir:
                context = playwright.chromium.launch_persistent_context(
                    user_data_dir=user_data_dir,
                    channel="chrome",
                    headless=False,
                    locale="zh-CN",
                    args=["--disable-blink-features=AutomationControlled"],
                )
            else:
                browser = playwright.chromium.launch(
                    channel="chrome",
                    headless=True,
                    args=["--disable-blink-features=AutomationControlled"],
                )
                context = browser.new_context(locale="zh-CN")
                context.add_cookies(_playwright_cookie_items(cookies))
            try:
                page = context.new_page()
                page.goto(BILIBILI_ARTICLE_MANAGER_URL, wait_until="domcontentloaded", timeout=30000)
                try:
                    page.wait_for_load_state("networkidle", timeout=15000)
                except Exception:
                    pass
                page.wait_for_timeout(3000)

                for index, archive in enumerate(archives):
                    overview_payloads: List[Dict] = []
                    play_payloads: List[Dict] = []
                    section = {"name": "data_overview"}

                    def handle_response(response):
                        if not _is_bilibili_metric_response(response.url):
                            return
                        target_bvid = str(archive.get("bvid") or "").lower()
                        response_url = response.url.lower()
                        # 详情页会请求全量列表接口，那里不是当前视频详情；只保留带当前 bvid 的详情接口。
                        if target_bvid and target_bvid not in response_url:
                            return
                        payload = _payload_from_response(response)
                        if payload is None:
                            return
                        payload = _filter_detail_payload_by_bvid(payload, target_bvid)
                        if "archive_diagnose/compare" in response_url:
                            play_payloads.append(payload)
                        elif section["name"] == "play_analysis":
                            play_payloads.append(payload)
                        else:
                            overview_payloads.append(payload)

                    detail_page = None
                    context.on("response", handle_response)
                    try:
                        detail_page = _click_data_button_from_list(context, page, index, archive)
                        try:
                            detail_page.wait_for_load_state("networkidle", timeout=15000)
                        except Exception:
                            pass
                        detail_page.wait_for_timeout(2000)
                        if not (_detail_tabs_visible(detail_page) or _is_detail_data_route(detail_page, archive)):
                            raise RuntimeError(f"未进入视频详情数据页：{archive.get('title')}")

                        _click_detail_tab(detail_page, "数据总览")
                        visible_overview = _collect_visible_metric_payload(detail_page, _OVERVIEW_LABELS)
                        if visible_overview:
                            overview_payloads.append(visible_overview)

                        section["name"] = "play_analysis"
                        _click_detail_tab(detail_page, "播放分析")
                        visible_play = _collect_visible_metric_payload(detail_page, _PLAY_ANALYSIS_LABELS)
                        if visible_play:
                            play_payloads.append(visible_play)

                        row = build_bilibili_detail_metric_row(archive, overview_payloads, play_payloads)
                        if row:
                            rows.append(row)
                            log_bilibili_detail_metric_row(row, index + 1, len(archives))
                        else:
                            _log_bilibili_detail_metric_missing(archive, index + 1, len(archives))
                    finally:
                        try:
                            context.remove_listener("response", handle_response)
                        except Exception:
                            pass
                        if detail_page is not None and detail_page != page:
                            try:
                                detail_page.close()
                            except Exception:
                                pass
                        if index < len(archives) - 1:
                            try:
                                page.goto(BILIBILI_ARTICLE_MANAGER_URL, wait_until="domcontentloaded", timeout=30000)
                                page.wait_for_timeout(1500)
                            except Exception:
                                pass
            finally:
                context.close()
                if browser is not None:
                    browser.close()
    except Exception as exc:
        raise RuntimeError(
            "B站创作中心详情页抓取失败；请确认 bbup-app cookie 有效，"
            "或把 OPENNEWSBRIEF_CHROME_USER_DATA_DIR 指向专用已登录 profile："
            f"{exc}"
        )

    rows = _dedupe_rows(rows)[:limit]
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    error = "" if rows else "未从 B站详情页采集到数据总览或播放分析指标"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "source": "bilibili_detail_page",
                "url": BILIBILI_ARTICLE_MANAGER_URL,
                "mid": mid,
                "fetched_at": datetime.datetime.now().isoformat(timespec="seconds"),
                "videos": rows,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )
    return {
        "metrics_path": output_path,
        "metric_count": len(rows),
        "source": "bilibili_detail_page",
        "error": error,
    }
