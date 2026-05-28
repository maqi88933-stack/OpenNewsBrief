# -*- coding: utf-8 -*-
import asyncio
import datetime
import json
import math
import os
import re
import subprocess
import time
from typing import Dict, List, Optional

import main


CONFIG_PATH = os.path.join(main.ROOT_DIR, "deep_series_config.json")
FEMALE_VOICE = "zh-CN-XiaoxiaoNeural"
MALE_VOICE = "zh-CN-YunxiNeural"
DEEP_TTS_RATE = os.environ.get("OPENNEWSBRIEF_DEEP_TTS_RATE", "+16%")
DEEP_TTS_ENGINE = os.environ.get("OPENNEWSBRIEF_TTS_ENGINE", "chattts").lower()
DEEP_DIALOGUE_PAUSE_SECONDS = 0.18
# 首句前留一点空白，避免视频切入后马上开口显得突兀。
DEEP_OPENING_SILENCE_SECONDS = 0.8
DEEP_FINAL_SILENCE_SECONDS = 1.0
DEEP_VISUAL_MAX_SECONDS = 4.0
DEEP_VISUAL_MIN_CHARS = 8
DEEP_SLIDE_DURATIONS_FILE = "slide_durations.json"
DEEP_PUBLISH_TITLE_MAX_CHARS = 18
DEEP_COVER_TEXT_MAX_CHARS = 10
DEEP_MIN_VALID_SOURCES = 3
DEEP_RESEARCH_MAX_ATTEMPTS = 3
DEEP_LLM_MAX_RETRIES = 3
DEEP_TARGET_MIN_SECONDS = 120
DEEP_TARGET_MAX_SECONDS = 180
DEEP_SCRIPT_SECONDS_PER_CHAR = 0.18
DEEP_OPENING_HOOK_LINES = 4
DEEP_OPENING_HOOK_MAX_ATTEMPTS = 3


# 这里保留一个最小可用的默认配置，方便首次启动时自动生成文件。
DEFAULT_CONFIG = {
    "series": [
        {
            "title": "AI未来三年系列",
            "description": "围绕 AI、搜索、Agent、记忆和内容生产的深度选题。",
            "episodes": [
                {"title": "AI 为什么会替代搜索？", "question": "AI 为什么会替代搜索？"},
                {"title": "为什么 Agent 会重构软件？", "question": "为什么 Agent 会重构软件？"},
                {"title": "AI 为什么需要记忆？", "question": "AI 为什么需要记忆？"},
            ],
        }
    ]
}


def load_config(path: str = None) -> Dict:
    # 配置文件不存在时自动补一份，避免 UI 首次打开就没有数据。
    path = path or CONFIG_PATH
    if not os.path.exists(path):
        save_config(DEFAULT_CONFIG, path)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_config(config: Dict, path: str = None) -> None:
    # 统一在这里保存，避免分散写文件导致编码不一致。
    path = path or CONFIG_PATH
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


def find_series(config: Dict, title: str) -> Dict:
    for series in config.get("series", []):
        if series.get("title") == title:
            return series
    raise ValueError(f"未找到系列：{title}")


def find_episode(series: Dict, title: str) -> Dict:
    for episode in series.get("episodes", []):
        if episode.get("title") == title:
            return episode
    raise ValueError(f"未找到主题：{title}")


def _episode_question(episode: Dict) -> str:
    # 选题优先用 question 字段，缺失时退回 title。
    return (episode.get("question") or episode.get("title") or "").strip()


def _llm_content_to_text(content) -> str:
    # LangChain 的 Responses API 会把可见文本放在 content 列表里，这里统一抽成普通字符串。
    if isinstance(content, str):
        return content.strip()

    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        text = "\n".join(part.strip() for part in parts if part and part.strip()).strip()
        if text:
            return text

    # 兜底保留原行为，避免非标准返回值直接中断流程。
    return str(content).strip()


def _is_retryable_llm_error(exc: Exception) -> bool:
    # 深度系列链路较长，524 和连接抖动这类瞬时故障允许自动重试。
    status_code = getattr(exc, "status_code", None)
    if status_code in (408, 409, 429, 500, 502, 503, 504, 524):
        return True

    message = str(exc).lower()
    retry_markers = [
        "timeout",
        "timed out",
        "connection error",
        "apiconnectionerror",
        "internalservererror",
        "error code: 524",
        "retryable",
    ]
    return any(marker in message for marker in retry_markers)


def _llm_retry_delay(exc: Exception, attempt: int) -> float:
    # 优先尊重服务端返回的等待时间，没有时再用短退避。
    retry_after = getattr(exc, "retry_after", None)
    if isinstance(retry_after, (int, float)) and retry_after > 0:
        return float(min(retry_after, 120))
    return float(min(10 * attempt, 30))


def call_llm(prompt: str, text: str = "") -> str:
    from util.llm import LLmFactory

    content = prompt
    if text:
        content += "\n\n资料：\n" + text
    llm = LLmFactory().getDeepseek()
    last_error = None
    for attempt in range(1, DEEP_LLM_MAX_RETRIES + 1):
        try:
            result = llm.invoke(content)
            return _llm_content_to_text(result.content if hasattr(result, "content") else result)
        except Exception as exc:
            last_error = exc
            if attempt >= DEEP_LLM_MAX_RETRIES or not _is_retryable_llm_error(exc):
                raise
            delay_seconds = _llm_retry_delay(exc, attempt)
            print(
                f"[深度系列] LLM 调用失败，{delay_seconds:.0f} 秒后重试（{attempt}/{DEEP_LLM_MAX_RETRIES}）：{exc}",
                flush=True,
            )
            time.sleep(delay_seconds)
    raise last_error


def build_focused_search_terms(series: Dict, episode: Dict) -> List[str]:
    # 深度选题的 question 往往是一整段自然语言，新闻搜索先用短词更容易命中。
    title = str(episode.get("title") or "")
    question = _episode_question(episode)
    company = re.split(r"[:：]", title, maxsplit=1)[0].strip()
    if not company:
        match = re.match(r"([\u4e00-\u9fffA-Za-z0-9&.\-]{2,24})", question)
        company = match.group(1).strip() if match else ""

    terms: List[str] = []
    if company:
        terms.extend([
            f"{company} ABF",
            f"{company} 半导体 材料",
            f"{company} 封装基板",
        ])
    if "味之素" in title or "味之素" in question or "ABF" in question.upper():
        terms.extend([
            "味之素 ABF 绝缘膜",
            "Ajinomoto ABF",
            "Ajinomoto Build-up Film",
            "Ajinomoto semiconductor materials",
        ])
    return [term for term in terms if term.strip()]


def build_retry_gap_search_terms(series: Dict, episode: Dict, audit_report: str = "", quality: Dict | None = None) -> List[str]:
    # 审核失败后只补“缺的资料类型”，避免第二轮继续用长问题原样搜索。
    title = str(episode.get("title") or "")
    question = _episode_question(episode)
    text = f"{audit_report or ''}\n{'；'.join((quality or {}).get('reasons', []))}"
    company = re.split(r"[:：]", title, maxsplit=1)[0].strip()
    terms: List[str] = []

    if "有效来源不足" in text or re.search(r"官方|一手|官网|产品资料", text):
        if company:
            terms.extend([
                f"{company} 官方 半导体 材料",
                f"{company} 投资者关系 年报 半导体",
            ])
        terms.extend([
            "Ajinomoto Build-up Film official",
            "Ajinomoto Fine-Techno ABF official",
            "Ajinomoto semiconductor materials annual report",
        ])

    if re.search(r"FC-?BGA|封装基板|IC载板|IC 载板|高端有机封装", text, re.I):
        terms.extend([
            "ABF substrate FC-BGA Ajinomoto",
            "FC-BGA ABF substrate industry report",
            "ABF 载板 FC-BGA 封装基板",
        ])

    if re.search(r"行业报告|市场份额|份额|供应链|载板厂", text):
        terms.extend([
            "ABF substrate market share report",
            "Ajinomoto ABF market share substrate",
            "ABF 载板 行业报告 市场份额",
        ])

    if re.search(r"认证|替代|国产|量产|客户", text):
        terms.extend([
            "ABF build-up film customer qualification",
            "封装基板 积层绝缘膜 客户认证 量产",
            "ABF 类 介质膜 国产替代 认证",
        ])

    if not terms and question:
        # 没有识别出具体缺口时，仍然用短词补官方来源，不再拼接整段问题。
        terms.extend(build_focused_search_terms(series, episode))
        terms.append(f"{company or question[:20]} 官方 资料")

    deduped: List[str] = []
    for term in terms:
        clean = term.strip()
        if clean and clean not in deduped:
            deduped.append(clean)
    return deduped


def build_search_keywords(
    series: Dict,
    episode: Dict,
    attempt: int = 1,
    audit_report: str = "",
    quality: Dict | None = None,
) -> List[str]:
    question = _episode_question(episode)
    if attempt >= 2:
        retry_terms = build_retry_gap_search_terms(series, episode, audit_report, quality)
        if retry_terms:
            return retry_terms

    base_terms = build_focused_search_terms(series, episode) + [
        question,
        f"{question} 核心机制 原因",
        f"{question} 用户需求 变化",
        f"{question} 商业模式 成本 收入",
        f"{question} 技术瓶颈 限制",
        f"{question} 反方观点 失败案例",
        f"{question} 监管 风险 版权",
        f"{question} 竞争格局 Google OpenAI 百度",
        f"{question} case study report",
        f"{question} controversy criticism debate",
        f"{question} timeline future prediction",
    ]
    if attempt >= 2:
        # 第二轮开始优先补官方和财务来源，避免只靠二手文章继续写稿。
        base_terms.extend([
            f"{question} official investor relations annual report",
            f"{question} 官方 财报 投资者关系",
            f"{question} product page semiconductor official",
        ])
    if attempt >= 3:
        # 最后一轮补行业报告和客户认证角度，尽量把事实支撑补齐。
        base_terms.extend([
            f"{question} industry report market share supplier",
            f"{question} 供应链 客户认证 行业报告",
        ])
    deduped: List[str] = []
    for term in base_terms:
        clean = term.strip()
        if clean and clean not in deduped:
            deduped.append(clean)
    return deduped


def collect_research_sources(
    series: Dict,
    episode: Dict,
    limit_per_keyword: int = 4,
    attempt: int = 1,
    audit_report: str = "",
    quality: Dict | None = None,
) -> List[Dict]:
    from crawler import news_crawler

    sources: List[Dict] = []
    seen_links = set()
    old_max_hours = news_crawler.MAX_HOURS
    news_crawler.MAX_HOURS = 24 * 365 * 5
    try:
        for keyword in build_search_keywords(series, episode, attempt=attempt, audit_report=audit_report, quality=quality):
            print(f"[深度系列] 检索关键词：{keyword}", flush=True)
            items, _expired_count, _used_query = news_crawler.collect_news_for_keyword(keyword)
            for item in (items or [])[:limit_per_keyword]:
                link = item.get("link", "")
                if not link or link in seen_links:
                    continue
                seen_links.add(link)
                sources.append(
                    {
                        "title": item.get("title", ""),
                        "link": link,
                        "content": item.get("content", ""),
                        "date": item.get("date", ""),
                    }
                )
    finally:
        news_crawler.MAX_HOURS = old_max_hours
    return sources


def merge_research_sources(existing: List[Dict], new_sources: List[Dict]) -> List[Dict]:
    # 重试是补资料，不是推倒重来；按链接去重后保留前几轮已找到的可用来源。
    merged: List[Dict] = []
    seen_links = set()
    for source in (existing or []) + (new_sources or []):
        link = str(source.get("link") or "").strip()
        if not link or link in seen_links:
            continue
        seen_links.add(link)
        merged.append(source)
    return merged


def sources_to_markdown(sources: List[Dict]) -> str:
    lines = []
    for index, source in enumerate(sources, 1):
        content = re.sub(r"\s+", " ", source.get("content", "")).strip()
        lines.append(
            f"### {index}. {source.get('title', '')}\n"
            f"- 链接：{source.get('link', '')}\n"
            f"- 时间：{source.get('date', '')}\n"
            f"- 摘要：{content[:1200]}"
        )
    return "\n\n".join(lines)


def generate_research_report(series: Dict, episode: Dict, sources: List[Dict]) -> str:
    # 研究报告只负责“把问题讲清楚”，不直接写脚本。
    prompt = (
        "你是深度系列的研究编辑。\n"
        f"系列：{series.get('title', '')}\n"
        f"主题：{_episode_question(episode)}\n\n"
        "请基于下面的资料写一份研究报告。\n"
        "要求：\n"
        "1. 先说明核心问题。\n"
        "2. 归纳背景、数据、争议、商业视角和技术视角。\n"
        "3. 给出后续脚本可直接使用的事实和结论。\n"
        "4. 保持中文、简洁、可读。\n"
    )
    return call_llm(prompt, sources_to_markdown(sources))


def audit_research(series: Dict, episode: Dict, research_report: str, sources: List[Dict]) -> str:
    # 用三种视角做轻量审校，避免报告只剩单一结论。
    source_text = sources_to_markdown(sources)
    roles = [
        ("事实审校", "检查事实是否完整，是否有明显遗漏或夸大。"),
        ("结构审校", "检查结构是否完整，是否适合继续写成口播脚本。"),
        ("钩子审校", "检查开头和结尾是否足够抓人，是否能支撑深度视频。"),
    ]
    results = []
    for role, task in roles:
        prompt = (
            f"你正在做{role}。\n"
            f"系列：{series.get('title', '')}\n"
            f"主题：{_episode_question(episode)}\n"
            f"任务：{task}\n\n"
            "请用要点方式指出问题，并给出可执行修改建议。\n"
        )
        results.append(f"## {role}\n" + call_llm(prompt, research_report + "\n\n资料：\n" + source_text))
    return "\n\n".join(results)


def count_valid_sources(sources: List[Dict]) -> int:
    # 只有带链接且有标题或正文的资料才算有效来源，空壳结果不能支撑深度稿。
    seen_links = set()
    count = 0
    for source in sources or []:
        link = str(source.get("link") or "").strip()
        title = str(source.get("title") or "").strip()
        content = str(source.get("content") or "").strip()
        if not link or link in seen_links or not (title or content):
            continue
        seen_links.add(link)
        count += 1
    return count


def assess_research_quality(sources: List[Dict], audit_report: str) -> Dict:
    # 来源数量是硬门槛；审稿里的事实风险只做软提醒，避免有资料时被反复卡住。
    source_count = count_valid_sources(sources)
    audit_text = re.sub(r"\s+", "", audit_report or "")
    reasons: List[str] = []
    warnings: List[str] = []
    if source_count < DEEP_MIN_VALID_SOURCES:
        reasons.append(f"有效来源不足：{source_count}/{DEEP_MIN_VALID_SOURCES}")

    warning_patterns = [
        r"事实支撑不够",
        r"事实.*不足",
        r"缺少.*(官方|来源|依据|资料|数据)",
        r"(来源|依据).*(缺失|不足|不够|缺少)",
        r"明显(遗漏|夸大)",
        r"概念推导",
        r"需要补充.*(官方|来源|依据|数据)",
    ]
    if any(re.search(pattern, audit_text) for pattern in warning_patterns):
        warnings.append("事实支撑不足")

    return {
        "blocked": bool(reasons),
        "source_count": source_count,
        "reasons": reasons,
        "warnings": warnings,
    }


def run_research_review_loop(series: Dict, episode: Dict) -> Dict:
    # 调研不过关时最多重搜三次，每轮都重新生成研究稿并重新审核。
    attempts = []
    latest_sources: List[Dict] = []
    latest_research = ""
    latest_audit = ""
    latest_quality = {}
    previous_audit = ""
    previous_quality: Dict = {}
    for attempt in range(1, DEEP_RESEARCH_MAX_ATTEMPTS + 1):
        print(f"[深度系列] 第 {attempt}/{DEEP_RESEARCH_MAX_ATTEMPTS} 轮检索资料", flush=True)
        new_sources = collect_research_sources(
            series,
            episode,
            attempt=attempt,
            audit_report=previous_audit,
            quality=previous_quality,
        )
        latest_sources = merge_research_sources(latest_sources, new_sources)
        print("[深度系列] 开始生成研究报告", flush=True)
        latest_research = generate_research_report(series, episode, latest_sources)
        print("\n========== 研究报告 ==========\n", flush=True)
        print(latest_research, flush=True)
        latest_audit = audit_research(series, episode, latest_research, latest_sources)
        print("\n========== 审校结果 ==========\n", flush=True)
        print(latest_audit, flush=True)
        latest_quality = assess_research_quality(latest_sources, latest_audit)
        latest_quality["attempt"] = attempt
        attempts.append(latest_quality)
        if not latest_quality["blocked"]:
            break
        print("[深度系列] 审核未通过：" + "；".join(latest_quality["reasons"]), flush=True)
        previous_audit = latest_audit
        previous_quality = latest_quality

    return {
        "sources": latest_sources,
        "research": latest_research,
        "audit": latest_audit,
        "quality": latest_quality,
        "attempts": attempts,
        "blocked": bool(latest_quality.get("blocked")),
    }


def build_safe_research_fallback(review: Dict) -> Dict:
    # 三轮后仍有问题时不再卡死整期，改成保守写稿：删掉缺来源的小段，换成可稳妥表达的内容。
    quality = review.get("quality", {})
    notes = quality.get("reasons", []) + quality.get("warnings", [])
    reasons = "；".join(notes) or "资料支撑不足"
    safe_note = (
        "\n\n## 保守写稿要求\n"
        f"三轮检索后仍存在问题：{reasons}。\n"
        "不要写入缺少来源支撑的具体数据、市场份额、唯一不可替代、确定短缺、客户名单等小段。\n"
        "如果某段事实没有资料支撑，就不要加入稿子中，改成更稳妥的产业链常识或解释性内容。\n"
        "允许保留主题主线，但必须使用“之一”“可能”“常见”“需要区分”等保守表达。\n"
    )
    return {
        "reason": "三轮检索后仍资料不足，已启用保守写稿",
        "audit": (review.get("audit") or "") + safe_note,
        "note": safe_note.strip(),
    }


def generate_dialogue_script(series: Dict, episode: Dict, research_report: str, audit_report: str) -> str:
    # 深度系列的视频脚本不走 PPT 纯文字，而是按对话来写。
    # 开头几句直接决定短视频留存，所以这里把冲突、损失和追问明确写进提示词。
    prompt = (
        "你是纪录片口播脚本作者。\n"
        f"系列：{series.get('title', '')}\n"
        f"主题：{episode.get('title', '')}\n"
        f"问题：{_episode_question(episode)}\n\n"
        "请写一份适合深度视频的对话脚本。\n"
        "要求：\n"
        "1. 只有女主持和男主持两个人对话，不要加入第三个发声角色，也不要写成 PPT 纯文字。\n"
        f"2. 全片目标 {DEEP_TARGET_MIN_SECONDS}-{DEEP_TARGET_MAX_SECONDS}秒；除前4句钩子外，每次发言尽量写完整，用 2 到 4 句承接一个观点，不要只写碎片短句。\n"
        "3. 前3秒第一句优先使用尖锐疑问句或反常识结论；疑问句必须包含冲突、代价或反直觉信息，不要铺垫，不要使用“你有没有想过”，不要使用“想象一下”，不要使用“今天我们探讨”。\n"
        "4. 前15秒必须完成三件事：前3秒给反常识结论，3-8秒给观众反问，8-15秒给核心答案。\n"
        "5. 前30秒必须交付核心答案框架：先说结论，再说为什么重要，再给出后面要展开的 2 到 3 个答案点。\n"
        "6. 前4句要有短视频钩子的冲突感和损失感，但不要写成 4 个口号式短句；前两轮发言要像自然对话，每次 35 到 60 个汉字，用 2 句完整口语表达。\n"
        "7. 男主持的前两次发问必须像观众刷到视频时的反问，不要温和捧哏。\n"
        "8. 开头可以尖锐，但不能编造事实，也不要为了劲爆写成谣言式标题党。\n"
        "9. 每隔一段留一个悬念，方便观众继续看下去。\n"
        "10. 结尾要落到一个站得住的问题。\n"
        "11. 每一行只使用“女：/男：”这种格式；不要连续输出同一个主持人的多行发言，同一主持人的连续表达必须合并到同一行。\n"
    )
    raw = call_llm(prompt, f"研究报告：\n{research_report}\n\n审校意见：\n{audit_report}")
    return clean_script_output(raw)


def estimate_dialogue_duration_seconds(script: str) -> float:
    # 没有真实音频前先用字符数估算时长，用来挡住明显超长的脚本。
    text = clean_script_output(script)
    text = re.sub(r"^[男女]：", "", text, flags=re.MULTILINE)
    text = re.sub(r"\s+", "", text)
    return round(len(text) * DEEP_SCRIPT_SECONDS_PER_CHAR, 1)


def assess_dialogue_duration(script: str) -> Dict:
    estimated = estimate_dialogue_duration_seconds(script)
    reasons = []
    if estimated > DEEP_TARGET_MAX_SECONDS:
        reasons.append(f"脚本预计 {estimated:.0f} 秒，超过 {DEEP_TARGET_MAX_SECONDS} 秒")
    return {
        "blocked": bool(reasons),
        "estimated_seconds": estimated,
        "reasons": reasons,
    }


def rewrite_dialogue_script_for_duration(series: Dict, episode: Dict, script: str, report: Dict) -> str:
    # 超长脚本只做压缩重写，不重新发散，避免越改越长。
    prompt = (
        "你是短视频脚本压缩编辑。\n"
        f"系列：{series.get('title', '')}\n"
        f"主题：{episode.get('title', '')}\n\n"
        f"请把下面脚本压缩到 {DEEP_TARGET_MIN_SECONDS}-{DEEP_TARGET_MAX_SECONDS} 秒。\n"
        "必须保留“女：/男：”双主持格式，前15秒仍然要有反常识结论、观众反问和核心答案。\n"
        "删掉重复解释、课程式铺垫和不影响结论的细节，只输出可朗读脚本。\n"
        "压缩原因：" + "；".join(report.get("reasons", [])) + "\n"
    )
    return clean_script_output(call_llm(prompt, script))


def extract_opening_hook(script: str, line_count: int = DEEP_OPENING_HOOK_LINES) -> str:
    # 只抽取真正的男女主持台词，避免标题、空行或模型说明干扰开头审校。
    lines = []
    for line in clean_script_output(script).splitlines():
        if SPEAKER_LINE_RE.match(line):
            lines.append(line)
        if len(lines) >= line_count:
            break
    return "\n".join(lines)


def assess_opening_hook(script: str) -> Dict:
    # 前几秒用规则先做基础体检，LLM 审校失败时也能给出稳定反馈。
    opening = extract_opening_hook(script)
    lines = [line for line in opening.splitlines() if SPEAKER_LINE_RE.match(line)]
    reasons: List[str] = []
    if len(lines) < 3:
        reasons.append("前几秒对话不足，无法形成钩子")

    first_match = SPEAKER_LINE_RE.match(lines[0]) if lines else None
    if first_match:
        first_body = first_match.group("body").strip()
        first_length = len(re.sub(r"\s+", "", first_body))
        stiff_phrases = [
            "凭什么",
            "反常识结论是",
            "你有没有想过",
            "想象一下",
            "今天我们探讨",
            "本文",
            "本期",
        ]
        if any(phrase in first_body for phrase in stiff_phrases):
            reasons.append("前3秒第一句不自然，像标题口号而不是口播")
        if first_length < 18:
            reasons.append("前3秒第一句太短，冲突和信息量不够")
        if first_length > 58:
            reasons.append("前3秒第一句太长，观众还没抓住重点就会流失")
        has_tension = re.search(r"[？?]", first_body) or any(
            token in first_body
            for token in ("没想到", "不是", "不只", "真正", "藏在", "卡住", "漏掉", "底座", "代价")
        )
        if not has_tension:
            reasons.append("前3秒缺少反差、悬念或损失感")

    opening_text = "\n".join(lines[:DEEP_OPENING_HOOK_LINES])
    if lines and not any(token in opening_text for token in ("答案", "关键", "核心", "因为", "ABF", "封装", "基板")):
        reasons.append("前4句没有交代核心答案或后续展开方向")

    return {
        "blocked": bool(reasons),
        "scope": "前3秒/前4句",
        "reasons": reasons,
        "opening": opening,
    }


def _replace_opening_hook(script: str, revised_hook: str) -> str:
    # 只替换开头几句，后面的解释段落原样保留，避免审校智能体误改整篇稿子。
    revised_lines = [
        line for line in clean_script_output(revised_hook).splitlines()
        if SPEAKER_LINE_RE.match(line)
    ][:DEEP_OPENING_HOOK_LINES]
    if len(revised_lines) < 2:
        return clean_script_output(script)

    original_lines = clean_script_output(script).splitlines()
    output_lines: List[str] = []
    replaced = False
    skipped_dialogue = 0
    for line in original_lines:
        if SPEAKER_LINE_RE.match(line) and skipped_dialogue < DEEP_OPENING_HOOK_LINES:
            if not replaced:
                output_lines.extend(revised_lines)
                replaced = True
            skipped_dialogue += 1
            continue
        output_lines.append(line)
    return clean_script_output("\n".join(output_lines))


def polish_opening_hook_with_review(
    series: Dict,
    episode: Dict,
    script: str,
    research_report: str,
    audit_report: str,
    max_attempts: int = DEEP_OPENING_HOOK_MAX_ATTEMPTS,
) -> tuple[str, Dict]:
    # 独立的前几秒审校智能体：最多三轮，只改前4句；失败时保留原稿，不阻断后续流程。
    current_script = clean_script_output(script)
    report = {
        "attempts": 0,
        "initial": assess_opening_hook(current_script),
        "final": {},
        "error": "",
    }
    if max_attempts <= 0:
        report["final"] = report["initial"]
        return current_script, report

    for attempt in range(1, max_attempts + 1):
        opening = extract_opening_hook(current_script)
        prompt = (
            "你是深度视频“前几秒留存审校智能体”。\n"
            f"系列：{series.get('title', '')}\n"
            f"主题：{episode.get('title', '')}\n\n"
            "任务：只审核并改写脚本前3秒和前4句，让开头更自然、更抓人。\n"
            "要求：\n"
            "1. 只输出改写后的前4句脚本，不要解释，不要输出标题。\n"
            "2. 第一句要像真人口播，25到45个汉字优先，必须同时有反差和具体悬念。\n"
            "3. 不要使用“凭什么”“反常识结论是”“你有没有想过”“想象一下”“今天我们探讨”。\n"
            "4. 男主持第一句要像观众的真实反问，不能温和捧哏。\n"
            "5. 第三或第四句必须交代核心答案，让观众知道继续看会得到什么。\n"
            "6. 不新增缺来源支撑的具体数据、市场份额、客户名单或绝对化判断。\n"
            "7. 必须保持“女：/男：”双主持格式，最多4句。\n"
        )
        context = (
            f"当前前4句：\n{opening}\n\n"
            f"研究报告：\n{research_report[:4000]}\n\n"
            f"审校意见：\n{audit_report[:3000]}"
        )
        try:
            raw = call_llm(prompt, context)
        except Exception as exc:
            report["attempts"] = attempt - 1
            report["error"] = str(exc)
            report["final"] = assess_opening_hook(current_script)
            return current_script, report

        revised_script = _replace_opening_hook(current_script, raw)
        report["attempts"] = attempt
        if revised_script == current_script:
            report["final"] = assess_opening_hook(current_script)
            report["error"] = "前几秒审校没有返回可用改写"
            return current_script, report

        current_script = revised_script
        report["final"] = assess_opening_hook(current_script)
        if not report["final"].get("blocked"):
            return current_script, report
    return current_script, report


def review_script_retention(series: Dict, episode: Dict, script: str, research_report: str, audit_report: str) -> Dict:
    # 整体留存审校只判断“能不能听完”，返回结构化结果给后续重写和质量报告使用。
    prompt = (
        "你是深度视频“整体留存审校智能体”。\n"
        f"系列：{series.get('title', '')}\n"
        f"主题：{episode.get('title', '')}\n\n"
        "任务：审核整条口播脚本的整体留存，判断用户是否愿意听完。\n"
        "请只返回 JSON：{\"passed\":true/false,\"score\":0-10,\"reasons\":[\"问题\"],\"suggestions\":[\"修改建议\"]}。\n"
        "审核标准：\n"
        "1. 前30秒是否持续有悬念、冲突或明确收益。\n"
        "2. 中段是否每隔一段都有新信息、反转、案例或追问，不能像资料罗列。\n"
        "3. 结尾是否有回扣和余味，让用户觉得听完有收获。\n"
        "4. 信息必须可信，不能为了吸引人编造事实或夸大结论。\n"
    )
    raw = call_llm(
        prompt,
        f"脚本：\n{script}\n\n研究报告：\n{research_report[:3000]}\n\n审校意见：\n{audit_report[:2000]}",
    )
    data = parse_json_object(raw)
    if not data:
        return {"blocked": False, "passed": True, "score": 0, "reasons": [], "suggestions": [], "raw": raw}

    score = data.get("score", 0)
    try:
        score = float(score)
    except (TypeError, ValueError):
        score = 0
    passed = bool(data.get("passed"))
    reasons = data.get("reasons") if isinstance(data.get("reasons"), list) else []
    suggestions = data.get("suggestions") if isinstance(data.get("suggestions"), list) else []
    return {
        "blocked": (not passed) or score < 7,
        "passed": passed,
        "score": score,
        "reasons": [str(item) for item in reasons],
        "suggestions": [str(item) for item in suggestions],
    }


def rewrite_script_for_retention(series: Dict, episode: Dict, script: str, review: Dict) -> str:
    # 整体留存不够时只补节奏、悬念和听完理由，避免改掉已审校过的事实边界。
    prompt = (
        "你是深度视频留存改稿智能体。\n"
        f"系列：{series.get('title', '')}\n"
        f"主题：{episode.get('title', '')}\n\n"
        "请根据审校意见重写整条脚本，让用户更愿意听完。\n"
        "要求：\n"
        "1. 保持“女：/男：”双主持格式，不要增加旁白。\n"
        "2. 前几秒继续保留强钩子，中段每 2 到 3 轮对话给出新信息或追问。\n"
        "3. 标出真实代价、反差、产业链位置或风险，不要写成资料清单。\n"
        "4. 不新增缺少来源支撑的具体数据、市场份额、客户名单或绝对化判断。\n"
        f"审校问题：{'；'.join(review.get('reasons', []))}\n"
        f"修改建议：{'；'.join(review.get('suggestions', []))}\n"
        "只输出重写后的脚本。\n"
    )
    return clean_script_output(call_llm(prompt, script))


def polish_script_with_retention_review(
    series: Dict,
    episode: Dict,
    script: str,
    research_report: str,
    audit_report: str,
    max_attempts: int = 2,
) -> tuple[str, Dict]:
    # 先审整体留存；不合格时最多整体重写一次，再重跑前几秒钩子审校。
    current_script = clean_script_output(script)
    reviews: List[Dict] = []
    hook_after_rewrite = {}
    for attempt in range(1, max_attempts + 1):
        review = review_script_retention(series, episode, current_script, research_report, audit_report)
        review["attempt"] = attempt
        reviews.append(review)
        if not review.get("blocked"):
            return current_script, {
                "blocked": False,
                "attempts": attempt,
                "reviews": reviews,
                "final": review,
                "hook_after_rewrite": hook_after_rewrite,
            }
        if attempt >= max_attempts:
            break
        current_script = rewrite_script_for_retention(series, episode, current_script, review)
        current_script, hook_after_rewrite = polish_opening_hook_with_review(
            series,
            episode,
            current_script,
            research_report,
            audit_report,
        )

    final_review = reviews[-1] if reviews else {}
    return current_script, {
        "blocked": True,
        "attempts": len(reviews),
        "reviews": reviews,
        "final": final_review,
        "hook_after_rewrite": hook_after_rewrite,
        "reasons": final_review.get("reasons", []),
    }


def _finish_dialogue_script_review(series: Dict, episode: Dict, research_report: str, audit_report: str, script: str, report: Dict) -> tuple[str, Dict]:
    # 时长检查后再打磨开头，避免压缩重写把前几秒钩子覆盖掉。
    attempts = report.get("attempts", 1)
    script, hook_report = polish_opening_hook_with_review(series, episode, script, research_report, audit_report)
    script, retention_report = polish_script_with_retention_review(series, episode, script, research_report, audit_report)
    final_report = assess_dialogue_duration(script)
    final_report["attempts"] = attempts
    final_report["hook_review"] = hook_report
    final_report["retention_review"] = retention_report
    if retention_report.get("blocked"):
        # 整体留存不足不直接卡死生成，但会进入质量报告，方便人工复查。
        final_report["blocked"] = True
        final_report.setdefault("reasons", []).extend(retention_report.get("reasons", []))
    return script, final_report


def generate_dialogue_script_with_duration_guard(series: Dict, episode: Dict, research_report: str, audit_report: str) -> tuple[str, Dict]:
    # 脚本生成后马上估算时长，最多重写三次，避免长稿继续进入 TTS 和视频渲染。
    script = ""
    report = {}
    for attempt in range(1, DEEP_RESEARCH_MAX_ATTEMPTS + 1):
        if attempt == 1:
            script = generate_dialogue_script(series, episode, research_report, audit_report)
        else:
            script = rewrite_dialogue_script_for_duration(series, episode, script, report)
        report = assess_dialogue_duration(script)
        report["attempts"] = attempt
        if not report["blocked"]:
            return _finish_dialogue_script_review(series, episode, research_report, audit_report, script, report)
    return _finish_dialogue_script_review(series, episode, research_report, audit_report, script, report)


def generate_script_notes(series: Dict, episode: Dict, research_report: str, audit_report: str, script: str) -> str:
    # 这一步给后续视频生成补充节奏、镜头和素材提示。
    prompt = (
        "你是深度视频脚本备注作者。\n"
        f"系列：{series.get('title', '')}\n"
        f"主题：{episode.get('title', '')}\n\n"
        "请输出简短备注，内容包含：节奏、转场、镜头和可视化提示。\n"
        "要求：\n"
        "1. 不要写成 PPT 大纲。\n"
        "2. 每段都要能对应到一张画面卡片。\n"
        "3. 给出适合口播的停顿位置。\n"
    )
    return call_llm(
        prompt,
        f"研究报告：\n{research_report}\n\n审校意见：\n{audit_report}\n\n脚本：\n{script}",
    )


def generate_documentary_package(series: Dict, episode: Dict, research_report: str, audit_report: str, script: str, result: Dict) -> str:
    # 这份包裹给发布和封面设计使用，保持信息最少但够用。
    prompt = (
        "你是深度视频的发布包装助手。\n"
        f"系列：{series.get('title', '')}\n"
        f"主题：{episode.get('title', '')}\n\n"
        "请输出一份简短的制作包，包含：\n"
        "1. 适合封面的标题思路。\n"
        "2. 适合封面的文案。\n"
        "3. 适合短视频评论区互动的问题。\n"
        "4. 适合 B-roll 的镜头关键词。\n"
    )
    package = call_llm(
        prompt,
        f"研究报告：\n{research_report}\n\n审校意见：\n{audit_report}\n\n脚本：\n{script}",
    )
    package_path = result.get("documentary_package_path")
    if package_path:
        with open(package_path, "w", encoding="utf-8") as f:
            f.write(package)
    return package


SPEAKER_LINE_RE = re.compile(r"^(?P<label>[^:：]{1,20})[:：]\s*(?P<body>.+)$")


def strip_inline_markdown(text: str) -> str:
    text = re.sub(r"[*_`]+", "", text or "")
    return re.sub(r"\s+", " ", text).strip()


def _normalize_speaker_label(label: str) -> str:
    label = strip_inline_markdown(label).replace(" ", "")
    label = label.replace("（", "").replace("）", "")
    return label


def _speaker_kind(label: str) -> str:
    clean = _normalize_speaker_label(label)
    if any(token in clean for token in ("旁白", "解说", "叙述", "播报")):
        # 旧脚本可能还有旁白标签；深度系列现在只保留双主持，所以归并到男主持。
        return "male"
    if any(token in clean for token in ("女", "女士")) and "男" not in clean:
        return "female"
    if any(token in clean for token in ("男", "先生")) and "女" not in clean:
        return "male"
    if any(token in clean for token in ("主持", "主持人")):
        # 没有标明男女的主持人标签默认交给男主持，避免重新引入第三角色。
        return "male"
    return "male"


def normalize_dialogue_line(line: str) -> str:
    # 先去掉粗体、斜体等标记，再统一成“角色：内容”的格式。
    line = strip_inline_markdown(line).strip()
    match = SPEAKER_LINE_RE.match(line)
    if match:
        speaker = match.group("label").strip()
        body = match.group("body").strip()
        if speaker and body:
            # 统一输出标准男女标签，顺手把历史“旁白：”清洗成双主持对话。
            speaker_name = "女" if _speaker_kind(speaker) == "female" else "男"
            return f"{speaker_name}：{body}"
    return line


def clean_script_output(text: str) -> str:
    # 去掉标题、分割线和前置说明，只保留真正可朗读的内容。
    lines = [line.strip() for line in (text or "").splitlines()]
    cleaned: List[str] = []
    seen_dialogue = False
    for raw_line in lines:
        if not raw_line:
            if cleaned and cleaned[-1] != "":
                cleaned.append("")
            continue
        if raw_line in {"---", "***", "___"} or re.match(r"^#{1,6}\s+", raw_line):
            continue
        line = normalize_dialogue_line(raw_line)
        match = SPEAKER_LINE_RE.match(line)
        if match:
            seen_dialogue = True
            cleaned.append(line)
            continue
        if seen_dialogue:
            cleaned.append(line)
    while cleaned and cleaned[0] == "":
        cleaned.pop(0)
    while cleaned and cleaned[-1] == "":
        cleaned.pop()

    # 模型偶尔会把同一主持人的一段话拆成多行；保存脚本前直接合并，避免 script.md 变成碎片列表。
    merged: List[str] = []
    for line in cleaned:
        if not line:
            if merged and merged[-1] != "":
                merged.append("")
            continue

        match = SPEAKER_LINE_RE.match(line)
        last_index = len(merged) - 1
        while last_index >= 0 and merged[last_index] == "":
            last_index -= 1
        last_match = SPEAKER_LINE_RE.match(merged[last_index]) if last_index >= 0 else None
        if match and last_match and _speaker_kind(match.group("label")) == _speaker_kind(last_match.group("label")):
            while merged and merged[-1] == "":
                merged.pop()
            label = "女" if _speaker_kind(match.group("label")) == "female" else "男"
            old_body = last_match.group("body").strip()
            new_body = match.group("body").strip()
            merged[-1] = f"{label}：{old_body}{new_body}"
            continue
        merged.append(line)

    return "\n".join(merged).strip()


def parse_dialogue_script(script: str) -> List[Dict]:
    script = clean_script_output(script)
    segments: List[Dict] = []
    current_speaker = "narrator"
    buffer: List[str] = []
    for raw_line in script.splitlines():
        line = normalize_dialogue_line(raw_line)
        match = SPEAKER_LINE_RE.match(line)
        if match and _speaker_kind(match.group("label")):
            if buffer:
                segments.append({"speaker": current_speaker, "text": "\n".join(buffer).strip()})
            current_speaker = _speaker_kind(match.group("label"))
            buffer = [match.group("body").strip()]
        elif line:
            buffer.append(line)
    if buffer:
        segments.append({"speaker": current_speaker, "text": "\n".join(buffer).strip()})
    return [item for item in segments if item["text"]]


async def _save_tts(text: str, output_path: str, voice: str, role: str = "narrator") -> None:
    if DEEP_TTS_ENGINE == "chattts":
        from audioContent.chattts_engine import synthesize_text

        synthesize_text(text, output_path, role=role)
        return

    import edge_tts

    communicate = edge_tts.Communicate(text, voice, rate=DEEP_TTS_RATE)
    await communicate.save(output_path)


def clear_generated_files(directory: str, allowed_extensions: tuple = None) -> None:
    # 只清理我们自己生成的中间文件，避免误删用户内容。
    if not os.path.isdir(directory):
        return
    for name in os.listdir(directory):
        path = os.path.join(directory, name)
        if not os.path.isfile(path):
            continue
        if allowed_extensions and not name.lower().endswith(allowed_extensions):
            continue
        os.remove(path)


def convert_dialogue_to_audio(script_path: str, output_path: str) -> str:
    from audioContent.news_to_audio import concat_audio_files, get_audio_duration, write_timing_file

    with open(script_path, "r", encoding="utf-8") as f:
        segments = parse_dialogue_script(f.read())
    if not segments:
        raise ValueError("脚本里没有可用于生成音频的对话内容")
    # 同一主持人连续说话时合成到同一个 TTS 片段，避免短音频边界和硬停顿造成卡顿感。
    merged_segments = []
    for segment in segments:
        if merged_segments and merged_segments[-1]["speaker"] == segment["speaker"]:
            merged_segments[-1]["text"] = merged_segments[-1]["text"].rstrip() + segment["text"].lstrip()
        else:
            merged_segments.append(dict(segment))
    segments = merged_segments

    segment_dir = os.path.join(os.path.dirname(output_path), "dialogue_segments")
    os.makedirs(segment_dir, exist_ok=True)
    clear_generated_files(segment_dir, (".mp3", ".wav", ".json", ".txt"))

    audio_segments = []
    segment_paths = []
    # 开场先拼一段短静音，让画面出现后再开始读第一句脚本。
    opening_silence_path = os.path.join(segment_dir, "000_opening_silence.mp3")
    create_silence_audio(opening_silence_path, DEEP_OPENING_SILENCE_SECONDS)
    segment_paths.append(opening_silence_path)
    for index, segment in enumerate(segments):
        print(f"[深度音频] 生成第 {index + 1}/{len(segments)} 段：{segment['speaker']}", flush=True)
        voice = FEMALE_VOICE if segment["speaker"] == "female" else MALE_VOICE
        segment_path = os.path.join(segment_dir, f"{index:03d}_{segment['speaker']}.mp3")
        asyncio.run(_save_tts(segment["text"], segment_path, voice, role=segment["speaker"]))
        duration = get_audio_duration(segment_path)
        audio_segments.append(
            {
                "role": segment["speaker"],
                "slide_index": index,
                "duration": duration,
                "text": segment["text"],
                "audio_path": segment_path,
            }
        )
        if index == 0:
            # timing 没有单独的空白段，所以把开场静音并入第一段，保持画面和音频总时长一致。
            audio_segments[-1]["duration"] += DEEP_OPENING_SILENCE_SECONDS
        segment_paths.append(segment_path)
        if index < len(segments) - 1:
            silence_path = os.path.join(segment_dir, f"{index:03d}_pause.mp3")
            create_silence_audio(silence_path, DEEP_DIALOGUE_PAUSE_SECONDS)
            audio_segments[-1]["duration"] += DEEP_DIALOGUE_PAUSE_SECONDS
            segment_paths.append(silence_path)

    ending_silence_path = os.path.join(segment_dir, f"{len(segments):03d}_ending_silence.mp3")
    create_silence_audio(ending_silence_path, DEEP_FINAL_SILENCE_SECONDS)
    audio_segments[-1]["duration"] += DEEP_FINAL_SILENCE_SECONDS
    segment_paths.append(ending_silence_path)

    concat_audio_files(segment_paths, output_path)
    write_timing_file(output_path, audio_segments)
    return output_path


def create_silence_audio(output_path: str, duration: float) -> str:
    import imageio_ffmpeg

    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    cmd = [
        ffmpeg_exe,
        "-y",
        "-f",
        "lavfi",
        "-i",
        "anullsrc=r=44100:cl=mono",
        "-t",
        f"{max(duration, 0.1):.3f}",
        "-q:a",
        "9",
        "-acodec",
        "libmp3lame",
        output_path,
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True)
    return output_path


def _font(size: int, bold: bool = False):
    return main.load_cover_font(size, bold=bold)


def _measure_text_size(draw, text: str, font) -> tuple[int, int]:
    # 用像素宽度来计算换行，避免只按字数切分导致版式乱掉。
    bbox = draw.textbbox((0, 0), text or " ", font=font)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def _wrap_text_by_width(draw, text: str, font, max_width: int) -> List[str]:
    clean_text = re.sub(r"\s+", " ", text or "").strip()
    if not clean_text:
        return [""]

    lines = []
    current = ""
    for char in clean_text:
        candidate = current + char
        if current and _measure_text_size(draw, candidate, font)[0] > max_width:
            lines.append(current)
            current = char
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def _fit_text_block(
    draw,
    text: str,
    max_width: int,
    max_height: int,
    *,
    start_size: int,
    min_size: int,
    bold: bool,
    max_lines: int,
) -> tuple:
    # 逐步缩小字号，优先保证文本完整且不挤出卡片。
    for size in range(start_size, min_size - 1, -2):
        font = _font(size, bold=bold)
        lines = _wrap_text_by_width(draw, text, font, max_width)
        if len(lines) > max_lines:
            continue
        line_height = _measure_text_size(draw, "国", font)[1]
        total_height = len(lines) * line_height + max(0, len(lines) - 1) * int(line_height * 0.24)
        if total_height <= max_height:
            return font, lines

    font = _font(min_size, bold=bold)
    lines = _wrap_text_by_width(draw, text, font, max_width)[:max_lines]
    if lines and len(lines) == max_lines:
        lines[-1] = lines[-1].rstrip("，。！？!?") + "…"
    return font, lines


def _load_timing_segments(audio_path: str) -> List[Dict]:
    timing_path = audio_path + ".timing.json"
    if not os.path.exists(timing_path):
        return []
    with open(timing_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("segments", [])


def _split_text_units(text: str) -> List[str]:
    # 按中文常见停顿切成短语，优先让前30秒的画面快速变化。
    clean_text = re.sub(r"\s+", " ", text or "").strip()
    if not clean_text:
        return []
    units = re.findall(r"[^。！？!?；;，,、]+[。！？!?；;，,、]?", clean_text)
    return [unit.strip() for unit in units if unit.strip()] or [clean_text]


def _split_text_evenly(text: str, count: int) -> List[str]:
    # 标点不够时按字数兜底拆分，避免长句仍然停在一张卡片上。
    clean_text = re.sub(r"\s+", " ", text or "").strip()
    if not clean_text:
        return []
    count = max(1, min(count, len(clean_text)))
    size = int(math.ceil(len(clean_text) / count))
    return [clean_text[index:index + size] for index in range(0, len(clean_text), size)]


def _split_text_for_visual_cards(text: str, target_count: int) -> List[str]:
    # 拆卡时按自然短语均衡成组，避免“结论是，”这类短片段单独占一张卡。
    clean_text = re.sub(r"\s+", " ", text or "").strip()
    if not clean_text:
        return []
    target_count = max(1, target_count)
    if target_count == 1:
        return [clean_text]

    units = _split_text_units(clean_text)
    if len(units) < target_count:
        target_count = min(target_count, max(1, len(clean_text) // DEEP_VISUAL_MIN_CHARS))
        if target_count == 1:
            return [clean_text]
        return _split_text_evenly(clean_text, target_count)

    target_length = sum(len(unit) for unit in units) / target_count
    min_group_chars = min(DEEP_VISUAL_MIN_CHARS, max(1, int(target_length * 0.6)))
    groups: List[str] = []
    current: List[str] = []
    current_length = 0
    for index, unit in enumerate(units):
        unit_length = len(unit)
        if current and len(groups) < target_count - 1 and current_length >= min_group_chars:
            remaining_units = len(units) - index
            remaining_groups = target_count - len(groups) - 1
            current_gap = abs(current_length - target_length)
            next_gap = abs(current_length + unit_length - target_length)
            if remaining_units >= remaining_groups and next_gap > current_gap:
                groups.append("".join(current).strip())
                current = []
                current_length = 0
        current.append(unit)
        current_length += unit_length
    if current:
        groups.append("".join(current).strip())

    # 极短尾巴并回前一张卡，宁可少一张也不要出现一两个字的突兀卡片。
    for index in range(len(groups) - 1, 0, -1):
        if len(groups[index]) < min_group_chars:
            groups[index - 1] = (groups[index - 1] + groups[index]).strip()
            groups.pop(index)
    if len(groups) > 1 and len(groups[0]) < min_group_chars:
        groups[1] = (groups[0] + groups[1]).strip()
        groups.pop(0)

    # 合并短碎片后如果卡片变少，就拆最长的一张补回节奏，避免单张卡停留太久。
    while len(groups) < target_count:
        longest_index = max(range(len(groups)), key=lambda index: len(groups[index]))
        longest_text = groups[longest_index]
        longest_units = _split_text_units(longest_text)
        if len(longest_units) >= 2:
            total_length = sum(len(unit) for unit in longest_units)
            best_split = 1
            best_gap = total_length
            running_length = 0
            for split_index in range(1, len(longest_units)):
                running_length += len(longest_units[split_index - 1])
                gap = abs(running_length - total_length / 2)
                if gap < best_gap:
                    best_gap = gap
                    best_split = split_index
            pieces = [
                "".join(longest_units[:best_split]).strip(),
                "".join(longest_units[best_split:]).strip(),
            ]
            piece_min_chars = max(6, min_group_chars - 2)
        else:
            pieces = _split_text_evenly(longest_text, 2)
            piece_min_chars = min_group_chars
        if len(pieces) < 2 or any(len(piece) < piece_min_chars for piece in pieces):
            break
        groups[longest_index:longest_index + 1] = pieces
    return groups


def _split_visual_segment(segment: Dict) -> List[Dict]:
    # 音频段超过视觉上限时，拆成多张短卡，但总时长保持不变。
    duration = max(float(segment.get("duration", 0.0)), 0.1)
    speaker = segment.get("speaker", "male")
    if speaker not in ("female", "male"):
        # 兼容旧 timing/script 里的 narrator，视觉层也只显示男女主持。
        speaker = "male"
    text = re.sub(r"\s+", " ", segment.get("text", "")).strip()
    if duration <= DEEP_VISUAL_MAX_SECONDS:
        return [{"speaker": speaker, "text": text, "duration": duration}]

    target_count = int(math.ceil(duration / DEEP_VISUAL_MAX_SECONDS))
    texts = _split_text_for_visual_cards(text, target_count)
    if not texts:
        texts = [text]
    # 同一段音频内没有逐字时间戳，文本长短差异明显时再按长度分配时长。
    weights = [max(len(re.sub(r"\s+", "", item_text)), 1) for item_text in texts]
    use_even_duration = max(weights) - min(weights) <= 2
    total_weight = sum(weights)
    result = []
    elapsed = 0.0
    for index, item_text in enumerate(texts):
        if use_even_duration:
            item_duration = duration / len(texts)
        else:
            item_duration = duration * weights[index] / total_weight
        if index == len(texts) - 1:
            item_duration = duration - elapsed
        elapsed += item_duration
        result.append({"speaker": speaker, "text": item_text, "duration": item_duration})
    return result


def classify_deep_visual_card(text: str) -> str:
    # 少量信息型标签能打破纯文字轮播的疲劳，但不改变整体 iOS 视觉风格。
    clean = re.sub(r"\s+", "", text or "")
    if re.search(r"\d", clean):
        return "关键数字"
    if any(token in clean for token in ("供应链", "产业链", "设备", "材料", "基建", "后台")):
        return "产业链位置"
    if any(token in clean for token in ("风险", "问题", "限制", "瓶颈", "代价", "不确定")):
        return "风险判断"
    if any(token in clean for token in ("但", "反方", "争议", "不是", "不能", "未必")):
        return "正反观点"
    if any(token in clean for token in ("结论", "核心", "关键", "答案")):
        return "核心判断"
    return "深度观点"


def build_deep_visual_slide_plan(script_path: str, audio_path: str) -> List[Dict]:
    timing_segments = _load_timing_segments(audio_path)
    source_segments = []
    if timing_segments:
        for item in timing_segments:
            text = re.sub(r"\s+", " ", item.get("text", "")).strip()
            if not text:
                continue
            source_segments.append(
                {
                    "speaker": item.get("role") or item.get("speaker") or "male",
                    "text": text,
                    "duration": max(float(item.get("duration", 0.0)), 0.1),
                }
            )
    else:
        with open(script_path, "r", encoding="utf-8") as f:
            for item in parse_dialogue_script(f.read()):
                text = re.sub(r"\s+", " ", item.get("text", "")).strip()
                if text:
                    source_segments.append(
                        {
                            "speaker": item.get("speaker", "male"),
                            "text": text,
                            "duration": DEEP_VISUAL_MAX_SECONDS,
                        }
                    )
    slide_plan: List[Dict] = []
    for segment in source_segments:
        slide_plan.extend(_split_visual_segment(segment))
    return slide_plan


def create_text_card(
    title: str,
    subtitle: str,
    body: str,
    output_path: str,
    accent: str = "#007AFF",
    slide_index: Optional[int] = None,
    slide_total: Optional[int] = None,
) -> str:
    from PIL import Image, ImageDraw

    width = 1920
    height = 1080
    image = Image.new("RGB", (width, height), "#F5F5F7")
    draw = ImageDraw.Draw(image)

    # 外层卡片保持 iOS 风格，尽量让画面看起来像一个完整页面，而不是 PPT 截图。
    draw.rounded_rectangle((68, 68, 1852, 1012), radius=56, fill="#E9E9EE")
    draw.rounded_rectangle((84, 84, 1836, 996), radius=52, fill="#FFFFFF")

    # 左侧色带是深度系列的固定视觉锚点。
    draw.rounded_rectangle((128, 128, 156, 932), radius=14, fill=accent)

    # 顶部小标签只负责识别，不占正文空间。
    draw.rounded_rectangle((190, 128, 420, 176), radius=18, fill=accent)
    draw.text((220, 136), "OpenNewsBrief", font=_font(24, True), fill="#FFFFFF")

    panel_left = 1460
    draw.rounded_rectangle((panel_left, 210, 1740, 748), radius=36, fill="#F5F5F7")
    if slide_index is not None:
        number_font = _font(92, True)
        number_text = f"{slide_index:02d}"
        number_w, _ = _measure_text_size(draw, number_text, number_font)
        draw.text((panel_left + int((280 - number_w) / 2), 286), number_text, font=number_font, fill=accent)
        if slide_total:
            total_text = f"/{slide_total:02d}"
            total_font = _font(28, True)
            total_w, _ = _measure_text_size(draw, total_text, total_font)
            draw.text((panel_left + int((280 - total_w) / 2), 390), total_text, font=total_font, fill="#8E8E93")

    title_font, title_lines = _fit_text_block(
        draw,
        title,
        1180,
        150,
        start_size=58,
        min_size=38,
        bold=True,
        max_lines=2,
    )
    y = 228
    for line in title_lines:
        draw.text((190, y), line, font=title_font, fill="#1D1D1F")
        y += _measure_text_size(draw, line, title_font)[1] + 10

    subtitle_font, subtitle_lines = _fit_text_block(
        draw,
        subtitle,
        1180,
        80,
        start_size=28,
        min_size=22,
        bold=False,
        max_lines=2,
    )
    y += 10
    for line in subtitle_lines:
        draw.text((190, y), line, font=subtitle_font, fill="#6E6E73")
        y += _measure_text_size(draw, line, subtitle_font)[1] + 8

    body_font, body_lines = _fit_text_block(
        draw,
        body,
        1180,
        420,
        start_size=52,
        min_size=30,
        bold=False,
        max_lines=8,
    )
    body_y = max(360, y + 24)
    body_line_height = _measure_text_size(draw, "国", body_font)[1]
    for line in body_lines:
        draw.text((190, body_y), line, font=body_font, fill="#1D1D1F")
        body_y += body_line_height + 18

    progress_left = 190
    progress_right = 1740
    progress_top = 868
    progress_bottom = 884
    draw.rounded_rectangle((progress_left, progress_top, progress_right, progress_bottom), radius=8, fill="#E5E5EA")
    if slide_index is not None and slide_total:
        fill_right = progress_left + int((progress_right - progress_left) * min(max(slide_index / slide_total, 0.0), 1.0))
        draw.rounded_rectangle((progress_left, progress_top, fill_right, progress_bottom), radius=8, fill=accent)

    image.save(output_path)
    return output_path


def write_deep_slide_durations(slide_dir: str, durations: List[float]) -> str:
    # 幻灯片时长单独写在侧车文件里，后续合成视频时直接读取。
    path = os.path.join(slide_dir, DEEP_SLIDE_DURATIONS_FILE)
    with open(path, "w", encoding="utf-8") as f:
        json.dump([float(item) for item in durations], f, ensure_ascii=False, indent=2)
    return path


def load_deep_slide_durations(image_paths: List[str]) -> List[float]:
    if not image_paths:
        return []
    path = os.path.join(os.path.dirname(image_paths[0]), DEEP_SLIDE_DURATIONS_FILE)
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        durations = json.load(f)
    if not isinstance(durations, list) or len(durations) != len(image_paths):
        return []
    return [float(item) for item in durations]


def create_deep_slide_images(series: Dict, episode: Dict, script_path: str, audio_path: str) -> List[str]:
    slide_dir = os.path.join(os.path.dirname(audio_path), "deep_slides")
    os.makedirs(slide_dir, exist_ok=True)
    clear_generated_files(slide_dir, (".png", ".json"))

    image_paths = []
    # 深度视频里主播配色改成低饱和色，减少大蓝大红的冲击感，画面更稳一点。
    accents = {
        "female": "#C79AA8",   # 柔和豆沙粉，保留区分度但不刺眼。
        "male": "#8FA8C1",     # 低饱和雾蓝，保持冷静但不硬。
    }
    labels = {"female": "女主持", "male": "男主持"}
    slide_plan = build_deep_visual_slide_plan(script_path, audio_path)
    if not slide_plan:
        slide_plan = [
            {
                "speaker": "male",
                "text": episode.get("title", "") or series.get("title", ""),
                "duration": DEEP_VISUAL_MAX_SECONDS,
            }
        ]
    total = len(slide_plan)
    for index, segment in enumerate(slide_plan):
        image_path = os.path.join(slide_dir, f"slide_{index:03d}.png")
        visual_label = classify_deep_visual_card(segment["text"])
        subtitle = f"{visual_label} · {series.get('title', '')} · {labels.get(segment['speaker'], '男主持')}"
        create_text_card(
            episode.get("title", ""),
            subtitle,
            segment["text"],
            image_path,
            accent=accents.get(segment["speaker"], "#007AFF"),
            slide_index=index + 1,
            slide_total=total,
        )
        image_paths.append(image_path)
    write_deep_slide_durations(slide_dir, [item["duration"] for item in slide_plan])
    return image_paths


def safe_filename(text: str) -> str:
    clean = re.sub(r'[\\/:*?"<>|]', "", text or "").strip()
    return clean.replace(" ", "_") or "deep_episode"


def validate_deep_audio_duration(audio_path: str) -> Dict:
    # 真实音频生成后再兜底检查一次；超时只提示，不再阻断视频合成流程。
    timing_path = audio_path + ".timing.json"
    if not os.path.exists(timing_path):
        return {"blocked": False, "actual_seconds": 0.0, "reasons": []}
    with open(timing_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    actual_seconds = float(data.get("total_duration") or 0.0)
    if not actual_seconds:
        actual_seconds = sum(float(item.get("duration", 0.0)) for item in data.get("segments", []))
    reasons = []
    if actual_seconds > DEEP_TARGET_MAX_SECONDS:
        reasons.append(f"音频实际 {actual_seconds:.0f} 秒，超过 {DEEP_TARGET_MAX_SECONDS} 秒")
    if reasons:
        return {"blocked": True, "actual_seconds": round(actual_seconds, 1), "reasons": reasons}
    return {"blocked": False, "actual_seconds": round(actual_seconds, 1), "reasons": []}


def step_video(audio_path: str, video_title: str, image_paths: List[str]) -> str:
    from video.Audio2Video import create_video

    output_path = os.path.join(os.path.dirname(audio_path), f"{safe_filename(video_title)}.mp4")
    slide_durations = load_deep_slide_durations(image_paths)
    if not slide_durations:
        slide_durations = main.load_slide_durations_from_timing(audio_path, len(image_paths))
    if not slide_durations:
        slide_durations = [DEEP_VISUAL_MAX_SECONDS] * len(image_paths)
    create_video(
        audio_path,
        image_paths[0],
        output_path,
        image_paths=image_paths,
        slide_durations=slide_durations,
        transition_clicks=False,
    )
    return output_path


def write_quality_report(path: str, payload: Dict) -> str:
    # 质量报告给 UI 展示和人工复查使用，保持 JSON 结构方便后续扩展。
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return path


def run_episode_pipeline(series: Dict, episode: Dict, base_dir: str = None) -> Dict:
    today = datetime.date.today().strftime("%Y-%m-%d")
    root = base_dir or main.ROOT_DIR
    output_dir = os.path.join(
        root,
        "deepContent",
        safe_filename(series.get("title", "")),
        safe_filename(episode.get("title", "")),
        today,
    )
    os.makedirs(output_dir, exist_ok=True)

    result = {
        "series": series.get("title", ""),
        "episode": episode.get("title", ""),
        "research_path": os.path.join(output_dir, "research.md"),
        "audit_path": os.path.join(output_dir, "audit.md"),
        "script_path": os.path.join(output_dir, "script.md"),
        "script_notes_path": os.path.join(output_dir, "script_notes.md"),
        "documentary_package_path": os.path.join(output_dir, "documentary_package.md"),
        "quality_report_path": os.path.join(output_dir, "quality_report.json"),
        "audio_path": "",
        "video_path": "",
        "review_ready": False,
        "review_blocked": False,
    }

    review = run_research_review_loop(series, episode)
    sources = review["sources"]
    research = review["research"]
    audit = review["audit"]
    quality_payload = {
        "research_attempts": review["attempts"],
        "research_quality": review["quality"],
        "script_quality": {},
    }
    result["source_count"] = review["quality"].get("source_count", 0)

    if review["blocked"]:
        fallback = build_safe_research_fallback(review)
        audit = fallback["audit"]
        result["fallback_used"] = True
        result["quality_block_reason"] = fallback["reason"]
        quality_payload["fallback_used"] = True
        quality_payload["fallback_note"] = fallback["note"]

    if review["blocked"] and not result.get("fallback_used"):
        reason = "；".join(review["quality"].get("reasons", []))
        result["review_blocked"] = True
        result["quality_block_reason"] = reason
        write_quality_report(result["quality_report_path"], quality_payload)
        with open(result["research_path"], "w", encoding="utf-8") as f:
            f.write("# 研究报告\n\n")
            f.write(research)
            f.write("\n\n# 资料来源\n\n")
            f.write(sources_to_markdown(sources))
        with open(result["audit_path"], "w", encoding="utf-8") as f:
            f.write(audit)
        log_path = os.path.join(output_dir, "agent_interaction.log")
        with open(log_path, "w", encoding="utf-8") as f:
            f.write("深度系列调研日志\n")
            f.write("=" * 56 + "\n")
            f.write("[审核阻断]\n" + reason + "\n\n")
            f.write("[研究报告]\n" + research + "\n\n")
            f.write("[审校结果]\n" + audit + "\n")
        result["agent_log_path"] = log_path
        return result

    script, script_quality = generate_dialogue_script_with_duration_guard(series, episode, research, audit)
    result["estimated_seconds"] = script_quality.get("estimated_seconds", 0.0)
    quality_payload["script_quality"] = script_quality
    print("\n========== 对话脚本 ==========\n", flush=True)
    print(script, flush=True)

    if script_quality.get("blocked"):
        reason = "；".join(script_quality.get("reasons", []))
        result["quality_block_reason"] = reason
        # 审核智能的建议保留下来，但脚本时长不再卡死后续视频生成。
        quality_payload["script_warning"] = reason

    script_notes = generate_script_notes(series, episode, research, audit, script)
    print("\n========== 脚本备注 ==========\n", flush=True)
    print(script_notes, flush=True)
    documentary_package = generate_documentary_package(series, episode, research, audit, script, result)
    write_quality_report(result["quality_report_path"], quality_payload)

    with open(result["research_path"], "w", encoding="utf-8") as f:
        f.write("# 研究报告\n\n")
        f.write(research)
        f.write("\n\n# 资料来源\n\n")
        f.write(sources_to_markdown(sources))
    with open(result["audit_path"], "w", encoding="utf-8") as f:
        f.write(audit)
    with open(result["script_path"], "w", encoding="utf-8") as f:
        f.write(script)
    with open(result["script_notes_path"], "w", encoding="utf-8") as f:
        f.write(script_notes)

    log_path = os.path.join(output_dir, "agent_interaction.log")
    with open(log_path, "w", encoding="utf-8") as f:
        f.write("深度系列调研日志\n")
        f.write("=" * 56 + "\n")
        f.write("[研究报告]\n" + research + "\n\n")
        f.write("[审校结果]\n" + audit + "\n\n")
        f.write("[脚本备注]\n" + script_notes + "\n\n")
        f.write("[纪录片包]\n" + documentary_package + "\n\n")
        f.write("[脚本]\n" + script + "\n")
    result["agent_log_path"] = log_path
    result["review_ready"] = True
    return result


def generate_video_from_script(series: Dict, episode: Dict, result: Dict) -> Dict:
    today = datetime.date.today().strftime("%Y-%m-%d")
    script_path = result.get("script_path") or episode.get("script_path", "")
    if not script_path or not os.path.exists(script_path):
        raise ValueError("找不到脚本文件，无法生成视频")
    output_dir = os.path.dirname(script_path)
    audio_path = os.path.join(output_dir, "dialogue.mp3")
    print("[深度视频] 开始生成口播音频", flush=True)
    audio_path = convert_dialogue_to_audio(script_path, audio_path)
    duration_report = validate_deep_audio_duration(audio_path)
    if duration_report.get("blocked"):
        print("[深度视频] 音频时长提醒：" + "；".join(duration_report.get("reasons", [])), flush=True)
    print("[深度视频] 开始生成画面卡片", flush=True)
    image_paths = create_deep_slide_images(series, episode, script_path, audio_path)
    print("[深度视频] 开始合成视频", flush=True)
    result["audio_path"] = audio_path
    result["actual_seconds"] = duration_report.get("actual_seconds", 0.0)
    result["video_path"] = step_video(audio_path, f"{episode.get('title', '')} {today}", image_paths)
    return result


def mark_episode_generated(config: Dict, series_title: str, episode_title: str, result: Dict) -> Dict:
    series = find_series(config, series_title)
    episode = find_episode(series, episode_title)
    episode["generated"] = bool(result.get("video_path"))
    episode["generated_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    episode["published"] = False
    episode["published_at"] = ""
    for key in (
        "research_path",
        "audit_path",
        "script_path",
        "script_notes_path",
        "documentary_package_path",
        "quality_report_path",
        "audio_path",
        "video_path",
        "publish_assets_path",
        "cover_path",
        "cover_option_paths",
        "publish_title",
        "publish_desc",
        "publish_tags",
        "source_count",
        "estimated_seconds",
        "actual_seconds",
        "quality_block_reason",
        "fallback_used",
    ):
        if result.get(key):
            episode[key] = result[key]
    episode["review_blocked"] = bool(result.get("review_blocked"))
    episode["review_ready"] = bool(result.get("review_ready") or (result.get("script_path") and not result.get("review_blocked")))
    return episode


def read_text_if_exists(path: str, limit: int = 5000) -> str:
    if not path or not os.path.exists(path):
        return ""
    with open(path, "r", encoding="utf-8") as f:
        return f.read()[:limit]


def parse_json_object(text: str) -> Dict:
    match = re.search(r"\{.*\}", text or "", flags=re.DOTALL)
    if not match:
        return {}
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}


def normalize_publish_title(title: str, episode_title: str, series_title: str = "") -> str:
    clean = re.sub(r"\s+", "", title or "").strip(" -_：:")
    for prefix in (series_title, "AI未来三年系列"):
        prefix_clean = re.sub(r"\s+", "", prefix or "")
        if prefix_clean:
            clean = re.sub(rf"^{re.escape(prefix_clean)}[:：\-—_]*", "", clean)
    if not clean:
        clean = re.sub(r"\s+", "", episode_title or "深度视频")
    if len(clean) <= DEEP_PUBLISH_TITLE_MAX_CHARS:
        return clean
    for sep in ("。", "！", "?", "？", ":", "：", ",", "，", "、", "-", "—", "·", " "):
        first = clean.split(sep)[0].strip()
        if 8 <= len(first) <= DEEP_PUBLISH_TITLE_MAX_CHARS:
            return first
    return clean[:DEEP_PUBLISH_TITLE_MAX_CHARS]


def normalize_cover_text(text: str, title: str) -> str:
    clean = re.sub(r"\s+", "", text or "").strip(" -_：:")
    if not clean:
        clean = re.sub(r"\s+", "", title or "")
    if len(clean) < 6:
        clean = (clean or "AI观察视角") + "视角"
    return clean[:DEEP_COVER_TEXT_MAX_CHARS]


def normalize_comment_question(text: str) -> str:
    clean = re.sub(r"\s+", "", text or "")
    if not clean:
        clean = "你更赞同 A 还是 B？为什么"
    return clean[:60]


def append_comment_question(desc: str, question: str) -> str:
    desc = (desc or "").strip()
    question = normalize_comment_question(question)
    if not question or question in desc:
        return desc
    prefix = "\n\n" if desc else ""
    return f"{desc}{prefix}互动问题：{question}"


def build_publish_keywords(series: Dict, episode: Dict, assets: Dict) -> List[str]:
    # 简介关键词用确定性规则兜底，保证搜索能命中主题、核心公司和技术名词。
    text = " ".join([
        str(series.get("title", "")),
        str(episode.get("title", "")),
        str(episode.get("question", "")),
        str(assets.get("title", "")),
        str(assets.get("tags", "")),
    ])
    candidates = re.findall(r"[A-Za-z0-9]{2,20}|[\u4e00-\u9fff]{2,12}", text)
    stop_words = {"为什么", "成为", "公司", "系列", "深度", "内容", "视频", "这个", "一个", "什么"}
    keywords: List[str] = []
    for item in candidates:
        clean = item.strip(" ，,。；;：:")
        if not clean or clean in stop_words or clean in keywords:
            continue
        keywords.append(clean)
        if len(keywords) >= 8:
            break
    return keywords or ["AI", "深度内容", "产业链"]


def ensure_publish_desc_keywords(desc: str, keywords: List[str]) -> str:
    # B 站简介里显式追加关键词行，方便站内搜索和后续人工复查。
    clean = (desc or "").strip()
    if "关键词" in clean:
        return clean
    keyword_text = "、".join([item for item in keywords if item][:8])
    if not keyword_text:
        return clean
    prefix = "\n\n" if clean else ""
    return f"{clean}{prefix}关键词：{keyword_text}"


def normalize_publish_assets_payload(series: Dict, episode: Dict, assets: Dict) -> Dict:
    # LLM 生成和改写后的发布字段都走同一套规整，避免标题、简介、封面规则前后不一致。
    normalized = dict(assets or {})
    normalized["title"] = normalize_publish_title(str(normalized.get("title") or ""), episode.get("title", "深度视频"), series.get("title", ""))
    normalized["comment_question"] = normalize_comment_question(str(normalized.get("comment_question") or ""))
    normalized["desc"] = append_comment_question(str(normalized.get("desc") or ""), normalized["comment_question"])
    normalized["tags"] = str(normalized.get("tags") or "AI,深度内容,纪录片,口播").strip()
    normalized["cover_text"] = normalize_cover_text(str(normalized.get("cover_text") or ""), normalized["title"])
    normalized["cover_prompt"] = str(normalized.get("cover_prompt") or "iOS 风格深度系列封面，简洁，清晰，白底，高对比，科技感").strip()

    if not isinstance(normalized.get("title_options"), list):
        normalized["title_options"] = [normalized["title"]]
    normalized["title_options"] = [
        normalize_publish_title(str(item), episode.get("title", "深度视频"), series.get("title", ""))
        for item in normalized["title_options"][:3]
    ] or [normalized["title"]]

    if not isinstance(normalized.get("cover_options"), list):
        normalized["cover_options"] = [normalized["cover_text"]]
    normalized["cover_options"] = [
        normalize_cover_text(str(item), normalized["title"])
        for item in normalized["cover_options"][:3]
    ] or [normalized["cover_text"]]

    normalized["desc"] = ensure_publish_desc_keywords(normalized["desc"], build_publish_keywords(series, episode, normalized))
    return normalized


def review_publish_assets(series: Dict, episode: Dict, assets: Dict, script_text: str, research_text: str) -> Dict:
    # 发布审校重点看点击欲和搜索命中，不负责重写正文事实。
    prompt = (
        "你是 B 站深度视频发布审校智能体。\n"
        f"系列：{series.get('title', '')}\n"
        f"主题：{episode.get('title', '')}\n\n"
        "任务：审核发布标题和简介是否有标题党式点击欲、是否包含搜索关键词。\n"
        "请只返回 JSON：{\"passed\":true/false,\"score\":0-10,\"reasons\":[\"问题\"],\"suggestions\":[\"建议\"]}。\n"
        "审核标准：\n"
        "1. 标题要有悬念、反差或冲突，让人想点开，但不能虚假夸大。\n"
        "2. 简介必须包含主题公司、技术名词、产业链关键词，方便搜索到。\n"
        "3. 标题、简介和视频前几秒承诺必须一致，不能标题党骗点。\n"
        "4. tags 要覆盖核心关键词。\n"
    )
    raw = call_llm(
        prompt,
        f"发布信息：\n{json.dumps(assets, ensure_ascii=False)}\n\n脚本：\n{script_text[:2500]}\n\n研究报告：\n{research_text[:1500]}",
    )
    data = parse_json_object(raw)
    if not data:
        return {"blocked": False, "passed": True, "score": 0, "reasons": [], "suggestions": [], "raw": raw}
    score = data.get("score", 0)
    try:
        score = float(score)
    except (TypeError, ValueError):
        score = 0
    passed = bool(data.get("passed"))
    reasons = data.get("reasons") if isinstance(data.get("reasons"), list) else []
    suggestions = data.get("suggestions") if isinstance(data.get("suggestions"), list) else []
    return {
        "blocked": (not passed) or score < 7,
        "passed": passed,
        "score": score,
        "reasons": [str(item) for item in reasons],
        "suggestions": [str(item) for item in suggestions],
    }


def rewrite_publish_assets_with_review(series: Dict, episode: Dict, assets: Dict, review: Dict, script_text: str, research_text: str) -> Dict:
    # 发布信息不合格时只改标题、简介、标签和封面短文案，保持产物结构不变。
    prompt = (
        "你是 B 站深度视频发布包装改稿智能体。\n"
        f"系列：{series.get('title', '')}\n"
        f"主题：{episode.get('title', '')}\n\n"
        "请根据审校意见重写发布信息，只返回 JSON，字段保持 title、desc、tags、cover_text、cover_prompt、comment_question、title_options、cover_options。\n"
        "要求：标题要有标题党式点击欲，优先使用悬念、反差、冲突或疑问；简介必须自然包含搜索关键词；不能虚假夸大。\n"
        f"审校问题：{'；'.join(review.get('reasons', []))}\n"
        f"修改建议：{'；'.join(review.get('suggestions', []))}\n"
    )
    raw = call_llm(
        prompt,
        f"当前发布信息：\n{json.dumps(assets, ensure_ascii=False)}\n\n脚本：\n{script_text[:2500]}\n\n研究报告：\n{research_text[:1500]}",
    )
    rewritten = parse_json_object(raw)
    merged = dict(assets)
    if rewritten:
        merged.update(rewritten)
    return merged


def polish_publish_assets_with_review(
    series: Dict,
    episode: Dict,
    assets: Dict,
    script_text: str,
    research_text: str,
    max_attempts: int = 2,
) -> tuple[Dict, Dict]:
    # 发布信息生成后再审校；不合格时最多补改一次，避免无限消耗 LLM 调用。
    current_assets = normalize_publish_assets_payload(series, episode, assets)
    reviews: List[Dict] = []
    for attempt in range(1, max_attempts + 1):
        review = review_publish_assets(series, episode, current_assets, script_text, research_text)
        review["attempt"] = attempt
        reviews.append(review)
        if not review.get("blocked"):
            return current_assets, {
                "blocked": False,
                "attempts": attempt,
                "reviews": reviews,
                "final": review,
            }
        if attempt >= max_attempts:
            break
        current_assets = rewrite_publish_assets_with_review(series, episode, current_assets, review, script_text, research_text)
        current_assets = normalize_publish_assets_payload(series, episode, current_assets)

    final_review = reviews[-1] if reviews else {}
    return current_assets, {
        "blocked": True,
        "attempts": len(reviews),
        "reviews": reviews,
        "final": final_review,
        "reasons": final_review.get("reasons", []),
    }


def create_deep_cover_image(
    series: Dict,
    episode: Dict,
    assets: Dict,
    output_dir: str,
    output_name: str = "cover.png",
    cover_text: str = None,
    accent: str = "#007AFF",
) -> str:
    # 封面保持简洁：左侧主标题，右侧信息块，整体更接近 iOS 风格页面。
    from PIL import Image, ImageDraw

    output_path = os.path.join(output_dir, output_name)
    image = Image.new("RGB", (1080, 1080), "#F2F2F7")
    draw = ImageDraw.Draw(image)

    draw.rounded_rectangle((64, 72, 1016, 1008), radius=48, fill="#FFFFFF")
    draw.rounded_rectangle((96, 128, 168, 952), radius=24, fill=accent)
    draw.rounded_rectangle((618, 128, 968, 384), radius=32, fill="#F5F5F7")
    draw.rounded_rectangle((618, 420, 968, 548), radius=32, fill="#1D1D1F")

    draw.text((220, 150), "OpenNewsBrief", font=_font(28, True), fill=accent)
    main_text = normalize_cover_text(cover_text if cover_text is not None else assets.get("cover_text", ""), assets.get("title", ""))
    draw.text((220, 230), main_text, font=_font(88, True), fill="#1D1D1F")
    draw.text((220, 356), assets.get("title", episode.get("title", ""))[:24], font=_font(38, True), fill="#6E6E73")
    draw.text((646, 166), "AI Answer", font=_font(28, True), fill="#1D1D1F")
    draw.text((646, 456), "Deep Series", font=_font(34, True), fill="#FFFFFF")
    draw.text((220, 940), series.get("title", "AI未来三年系列")[:18], font=_font(26, True), fill="#8E8E93")
    image.save(output_path)
    return output_path


def create_deep_cover_options(series: Dict, episode: Dict, assets: Dict, output_dir: str) -> List[str]:
    # 把 LLM 给出的封面备选真正落成本地图片，避免只有文案没有可用封面。
    palette = ["#007AFF", "#5E5CE6", "#34C759"]
    options = list(assets.get("cover_options") or [assets.get("cover_text", "")])
    while len(options) < 3:
        options.append(assets.get("cover_text", "") or assets.get("title", "AI深度解析"))
    paths = []
    for index, text in enumerate(options[:3]):
        paths.append(
            create_deep_cover_image(
                series,
                episode,
                assets,
                output_dir,
                output_name=f"cover_option_{index + 1}.png",
                cover_text=text,
                accent=palette[index % len(palette)],
            )
        )
    return paths


def generate_publish_assets(series: Dict, episode: Dict, result: Dict) -> Dict:
    # 发布信息只生成一次，后面发视频和发文案都直接复用。
    # title 只保存“主题名称”部分，上传到 B 站时再统一拼成“系列名称：主题名称”。
    script_text = read_text_if_exists(result.get("script_path", ""))
    research_text = read_text_if_exists(result.get("research_path", ""), limit=3000)
    prompt = (
        "你是短视频发布信息生成助手。\n"
        f"系列：{series.get('title', '')}\n"
        f"主题：{episode.get('title', '')}\n\n"
        "请只返回 JSON，对象字段如下：\n"
        "{\n"
        '  "title": "发布标题",\n'
        '  "desc": "发布简介",\n'
        '  "tags": "标签1,标签2",\n'
        '  "cover_text": "封面短文案",\n'
        '  "cover_prompt": "封面图提示词",\n'
        '  "comment_question": "评论区互动问题",\n'
        '  "title_options": ["标题备选1", "标题备选2", "标题备选3"],\n'
        '  "cover_options": ["封面备选1", "封面备选2", "封面备选3"]\n'
        "}\n"
        "要求：标题短一些，封面文案控制在 6 到 10 个字，评论问题适合互动。\n"
        "原始主题标题只是参考，title 不需要完全照抄原始标题，可以根据脚本和研究报告改写成更吸引眼球的主题名称。\n"
        "B站最终标题基本格式固定为“系列名称：主题名称”，所以不要把系列名称写进 title，title 只返回主题名称部分。\n"
        "title_options 也按同样规则给 3 个主题名称备选，不要带系列名称前缀。\n"
        "标题、封面文案、视频前3秒必须围绕同一个承诺，观众点进来后马上听到同一个答案方向。\n"
    )
    raw = call_llm(prompt, f"脚本：\n{script_text}\n\n研究报告：\n{research_text}")
    assets = parse_json_object(raw)
    if not assets:
        assets = {
            "title": episode.get("title", "深度视频"),
            "desc": f"{series.get('title', '')} / {episode.get('title', '')} 的深度内容发布文案。",
            "tags": "AI,深度内容,纪录片,口播",
            "cover_text": "AI 深度解析",
            "cover_prompt": "iOS 风格深度系列封面，简洁，清晰，白底，高对比，科技感",
            "comment_question": "你更赞同 A 还是 B？为什么",
            "title_options": [episode.get("title", "深度视频")],
            "cover_options": ["AI 深度解析"],
        }

    assets, publish_review = polish_publish_assets_with_review(series, episode, assets, script_text, research_text)
    assets["publish_review"] = publish_review

    output_dir = os.path.dirname(os.path.abspath(result.get("video_path") or result.get("script_path") or CONFIG_PATH))
    os.makedirs(output_dir, exist_ok=True)
    assets["cover_path"] = create_deep_cover_image(series, episode, assets, output_dir)
    assets["cover_option_paths"] = create_deep_cover_options(series, episode, assets, output_dir)
    assets_path = os.path.join(output_dir, "publish_assets.json")
    with open(assets_path, "w", encoding="utf-8") as f:
        json.dump(assets, f, ensure_ascii=False, indent=2)
    assets["path"] = assets_path
    return assets


def run_episode_by_titles(series_title: str, episode_title: str) -> Dict:
    config = load_config()
    series = find_series(config, series_title)
    episode = find_episode(series, episode_title)
    result = run_episode_pipeline(series, episode)

    # 重新读取配置是为了保留外部同时做过的修改。
    config = load_config()
    series = find_series(config, series_title)
    episode = find_episode(series, episode_title)
    episode["generated"] = False
    episode["generated_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for key in (
        "research_path",
        "audit_path",
        "script_path",
        "script_notes_path",
        "documentary_package_path",
        "quality_report_path",
        "agent_log_path",
        "source_count",
        "estimated_seconds",
        "quality_block_reason",
        "fallback_used",
    ):
        if result.get(key):
            episode[key] = result[key]
    # 新一轮成功后要清掉上一轮遗留的阻断原因，避免 UI 继续显示旧失败状态。
    episode["quality_block_reason"] = str(result.get("quality_block_reason") or "")
    episode["fallback_used"] = bool(result.get("fallback_used"))
    episode["audio_path"] = ""
    episode["video_path"] = ""
    episode["published"] = False
    episode["published_at"] = ""
    episode["review_blocked"] = bool(result.get("review_blocked"))
    episode["review_ready"] = bool(result.get("review_ready") or (result.get("script_path") and not result.get("review_blocked")))
    save_config(config)
    return result


def generate_episode_video_by_titles(series_title: str, episode_title: str) -> Dict:
    config = load_config()
    series = find_series(config, series_title)
    episode = find_episode(series, episode_title)
    if episode.get("review_blocked"):
        script_path = episode.get("script_path", "")
        if not script_path or not os.path.exists(script_path):
            raise ValueError("当前主题被审核阻断，请先补充资料或修改脚本后再生成视频")
        # 有脚本产物时，审核建议只作为修改提醒，不再阻断视频生成。
        print("[深度视频] 检测到审核提醒，已有脚本，继续生成视频", flush=True)
        episode["review_blocked"] = False
        episode["review_ready"] = True
    result = {
        "series": series.get("title", ""),
        "episode": episode.get("title", ""),
        "research_path": episode.get("research_path", ""),
        "audit_path": episode.get("audit_path", ""),
        "script_path": episode.get("script_path", ""),
        "script_notes_path": episode.get("script_notes_path", ""),
        "documentary_package_path": episode.get("documentary_package_path", ""),
        "audio_path": episode.get("audio_path", ""),
        "video_path": episode.get("video_path", ""),
        "agent_log_path": episode.get("agent_log_path", ""),
        "quality_report_path": episode.get("quality_report_path", ""),
        "source_count": episode.get("source_count", 0),
        "estimated_seconds": episode.get("estimated_seconds", 0.0),
    }
    result = generate_video_from_script(series, episode, result)
    assets = generate_publish_assets(series, episode, result)
    result["publish_assets_path"] = assets["path"]
    result["publish_title"] = assets["title"]
    result["publish_desc"] = assets["desc"]
    result["publish_tags"] = assets["tags"]
    result["cover_path"] = assets.get("cover_path", "")
    config = load_config()
    mark_episode_generated(config, series_title, episode_title, result)
    save_config(config)
    return result
