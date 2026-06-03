# -*- coding: utf-8 -*-
import datetime
import json
import os
from typing import Dict, List


BILIBILI_ARTICLE_MANAGER_URL = "https://member.bilibili.com/platform/upload-manager/article"
_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DEFAULT_METRICS_PATH = os.path.join(_PROJECT_DIR, "deepContent", "deep_feedback_metrics.json")


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
        "avg_view_seconds": 0,
        "completion_rate": 0,
        "click_rate": 0,
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


def _is_bilibili_metric_response(url: str) -> bool:
    url = url.lower()
    if "bilibili.com" not in url:
        return False
    return any(token in url for token in ("upload-manager", "archive", "article", "stat", "web/archive"))


def scrape_bilibili_article_metrics(output_path: str = None, limit: int = 50, user_data_dir: str = None) -> Dict:
    # 真实抓取只放在这个函数里，外层 deep_series 只关心落地后的 JSON 指标文件。
    from playwright.sync_api import sync_playwright

    output_path = output_path or _DEFAULT_METRICS_PATH
    user_data_dir = user_data_dir or _default_chrome_user_data_dir()
    if not user_data_dir or not os.path.exists(user_data_dir):
        raise RuntimeError("未找到 Chrome 用户数据目录，请设置 OPENNEWSBRIEF_CHROME_USER_DATA_DIR 后重试")

    collected: List[Dict] = []

    def handle_response(response):
        if not _is_bilibili_metric_response(response.url):
            return
        try:
            payload = response.json()
        except Exception:
            try:
                payload = response.text()
            except Exception:
                return
        collected.extend(extract_bilibili_metric_rows(payload))

    try:
        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(
                user_data_dir=user_data_dir,
                channel="chrome",
                headless=False,
                args=["--disable-blink-features=AutomationControlled"],
            )
            try:
                page = context.new_page()
                page.on("response", handle_response)
                page.goto(BILIBILI_ARTICLE_MANAGER_URL, wait_until="domcontentloaded", timeout=30000)
                try:
                    page.wait_for_load_state("networkidle", timeout=15000)
                except Exception:
                    pass
                page.wait_for_timeout(5000)
                try:
                    state_text = page.evaluate(
                        """() => Array.from(document.scripts)
                            .map(script => script.textContent || "")
                            .filter(text => text.includes("__INITIAL_STATE__"))
                            .slice(0, 5)
                            .join("\\n")"""
                    )
                    collected.extend(extract_bilibili_metric_rows(state_text))
                except Exception:
                    pass
            finally:
                context.close()
    except Exception as exc:
        raise RuntimeError(
            "B站创作中心抓取失败，请关闭正在运行的 Chrome 后重试，"
            "或把 OPENNEWSBRIEF_CHROME_USER_DATA_DIR 指向专用已登录 profile："
            f"{exc}"
        )

    rows = _dedupe_rows(collected)[:limit]
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    error = "" if rows else "未从 B站创作中心页面捕获到稿件指标，请确认 Chrome 已登录并进入稿件管理页"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "source": "bilibili_article_manager",
                "url": BILIBILI_ARTICLE_MANAGER_URL,
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
        "source": "bilibili_article_manager",
        "error": error,
    }
