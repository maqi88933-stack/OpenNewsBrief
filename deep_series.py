# -*- coding: utf-8 -*-
import asyncio
import csv
import datetime
import difflib
import json
import math
import os
import re
import subprocess
import time
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional

import main


CONFIG_PATH = os.path.join(main.ROOT_DIR, "deep_series_config.json")
FEMALE_VOICE = "zh-CN-XiaoxiaoNeural"
MALE_VOICE = "zh-CN-YunxiNeural"
# Edge TTS 备用路径默认不加速，和 ChatTTS 的 1.0 倍默认语速保持一致。
DEEP_TTS_RATE = os.environ.get("OPENNEWSBRIEF_DEEP_TTS_RATE", "+0%")
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
DEEP_TARGET_MAX_SECONDS = 150
# 脚本阶段按 2 分半控稿，给 ChatTTS 语速、停顿和结尾留白留出余量。
DEEP_SCRIPT_SECONDS_PER_CHAR = 0.22
# 音频和视频阶段不截断、不拦截，只保留 3 分钟提醒，方便人工复核。
DEEP_AUDIO_WARNING_MAX_SECONDS = 180
DEEP_OPENING_HOOK_LINES = 4
DEEP_OPENING_HOOK_MAX_ATTEMPTS = 3
DEEP_FEEDBACK_METRICS_FILE = os.path.join(main.ROOT_DIR, "deepContent", "deep_feedback_metrics.json")
DEEP_FEEDBACK_REPORT_FILE = "deep_feedback_report.json"
DEEP_FEEDBACK_ADVICE_FILE = "deep_optimization_advice.md"


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

    # 流式读取的异常有时只体现在异常类型里，所以类型名和错误文本一起判断。
    message = f"{exc.__class__.__name__}: {exc}".lower()
    retry_markers = [
        "timeout",
        "timed out",
        "connection error",
        "apiconnectionerror",
        "remoteprotocolerror",
        "protocol error",
        "peer closed connection",
        "incomplete chunked read",
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


def _llm_stream_chunk_to_text(chunk) -> str:
    # 流式分块通常是增量 token，不能对每块单独 strip，否则会丢掉空格和换行。
    content = chunk.content if hasattr(chunk, "content") else chunk
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "".join(parts)
    return _llm_content_to_text(content)


def _stream_llm_to_text(llm, content: str) -> str:
    # 底层用流式持续接收，函数出口仍保持完整字符串，避免影响深度系列上层流程。
    stream = getattr(llm, "stream", None)
    if not callable(stream):
        return ""
    parts = []
    for chunk in stream(content):
        text = _llm_stream_chunk_to_text(chunk)
        if text:
            parts.append(text)
    return "".join(parts).strip()


def call_llm(prompt: str, text: str = "") -> str:
    from util.llm import LLmFactory

    content = prompt
    if text:
        content += "\n\n资料：\n" + text
    llm = LLmFactory().getDeepseek()
    last_error = None
    for attempt in range(1, DEEP_LLM_MAX_RETRIES + 1):
        try:
            streamed_text = _stream_llm_to_text(llm, content)
            if streamed_text:
                return streamed_text
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
    topic_text = f"{series.get('title', '')} {title} {question}"
    company = re.split(r"[:：]", title, maxsplit=1)[0].strip() if re.search(r"[:：]", title) else ""

    terms: List[str] = []
    if company:
        # 带“公司：问题”格式的选题先查公司和产业关键词，避免长问题命中率太低。
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
    if re.search(r"机器人|具身智能|机械臂|抓取|行走|操作物体|拿起|杯子", topic_text):
        # 通用机器人选题没有公司名，直接补技术难点和英文研究词，避免误搜成 ABF 半导体材料。
        terms.extend([
            "机器人 抓取 操作 物体 难点",
            "具身智能 机器人 操作 物理世界 可靠性",
            "robot grasping manipulation real world reliability",
            "embodied AI robot manipulation report",
        ])

    deduped: List[str] = []
    for term in terms:
        clean = term.strip()
        if clean and clean not in deduped:
            deduped.append(clean)
    return deduped


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
            f"### [S{index}] {source.get('title', '')}\n"
            f"- 链接：{source.get('link', '')}\n"
            f"- 时间：{source.get('date', '')}\n"
            f"- 摘要：{content[:1200]}"
        )
    return "\n\n".join(lines)


def build_research_plan(series: Dict, episode: Dict) -> str:
    # 对齐 Deep Research 的第一步：先把研究问题、来源类型和首轮检索词写清楚。
    keywords = build_search_keywords(series, episode)[:10]
    lines = [
        "# 研究计划",
        "",
        f"- 系列：{series.get('title', '')}",
        f"- 主题：{episode.get('title', '')}",
        f"- 核心问题：{_episode_question(episode)}",
        "",
        "## 子问题",
        "1. 这个选题真正要解释的反常识或冲突是什么？",
        "2. 哪些事实、案例或数据可以支撑主线？",
        "3. 是否存在反方观点、失败案例或容易夸大的地方？",
        "4. 结论如何转成 120-150 秒的双主持人口播？",
        "",
        "## 优先来源类型",
        "- 官方资料、产品页、投资者关系、年报或白皮书。",
        "- 行业报告、研究机构、技术博客或论文摘要。",
        "- 反方观点、失败案例、监管风险或商业化复盘。",
        "",
        "## 首轮检索词",
    ]
    lines.extend(f"- {keyword}" for keyword in keywords)
    return "\n".join(lines).strip() + "\n"


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
        "5. 涉及具体事实、数据、案例或公司判断时，句末必须标注来源编号，例如 [S1] 或 [S1][S2]；不要引用资料外的信息。\n"
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


def run_research_review_loop(series: Dict, episode: Dict, research_plan: str = "") -> Dict:
    # 调研不过关时最多重搜三次，每轮都重新生成研究稿并重新审核。
    attempts = []
    trace_attempts = []
    plan = research_plan or build_research_plan(series, episode)
    latest_sources: List[Dict] = []
    latest_research = ""
    latest_audit = ""
    latest_quality = {}
    previous_audit = ""
    previous_quality: Dict = {}
    for attempt in range(1, DEEP_RESEARCH_MAX_ATTEMPTS + 1):
        print(f"[深度系列] 第 {attempt}/{DEEP_RESEARCH_MAX_ATTEMPTS} 轮检索资料", flush=True)
        keywords = build_search_keywords(series, episode, attempt=attempt, audit_report=previous_audit, quality=previous_quality)
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
        trace_attempts.append(
            {
                "attempt": attempt,
                "keywords": keywords,
                "new_source_count": count_valid_sources(new_sources),
                "source_count": latest_quality.get("source_count", 0),
                "blocked": bool(latest_quality.get("blocked")),
                "reasons": latest_quality.get("reasons", []),
                "warnings": latest_quality.get("warnings", []),
            }
        )
        if not latest_quality["blocked"]:
            break
        print("[深度系列] 审核未通过：" + "；".join(latest_quality["reasons"]), flush=True)
        previous_audit = latest_audit
        previous_quality = latest_quality

    return {
        "plan": plan,
        "trace": trace_attempts,
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
        "3. 整集必须有单一主线：开头先抛出核心判断，中段每一段都要说明“上一段推出了什么，所以这一段要讲什么”，不要把中段写成并列清单或资料点名。\n"
        "4. 前3秒第一句优先使用尖锐疑问句或反常识结论；疑问句必须包含冲突、代价或反直觉信息，不要铺垫，不要使用“你有没有想过”，不要使用“想象一下”，不要使用“今天我们探讨”。\n"
        "5. 前15秒必须完成三件事：前3秒给反常识结论，3-8秒给观众反问，8-15秒给核心答案。\n"
        "6. 前30秒必须交付核心答案框架：先说结论，再说为什么重要，再给出后面要展开的 2 到 3 个答案点。\n"
        "7. 前4句要有短视频钩子的冲突感和损失感，但不要写成 4 个口号式短句；前两轮发言要像自然对话，每次 35 到 60 个汉字，用 2 句完整口语表达。\n"
        "8. 男主持的前两次发问必须像观众刷到视频时的反问，不要温和捧哏。\n"
        "9. 开头可以尖锐，但不能编造事实，也不要为了劲爆写成谣言式标题党。\n"
        "10. 每隔一段留一个悬念，方便观众继续看下去。\n"
        "11. 中段可以加入一句克制互动埋点，但要像自然口播，不要写“把绝了打在弹幕上”这类破坏质感的弹幕口号。\n"
        "12. 结尾要落到一个站得住的问题。\n"
        "13. 每一行只使用“女：/男：”这种格式；不要连续输出同一个主持人的多行发言，同一主持人的连续表达必须合并到同一行。\n"
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


def optimize_overtime_dialogue_script_with_agent(series: Dict, episode: Dict, script: str, report: Dict) -> str:
    # 专门的脚本时长优化代理：只处理超时脚本，保留事实边界和双主持结构，不重新发散选题。
    prompt = (
        "你是深度系列视频的脚本时长优化代理。\n"
        f"系列：{series.get('title', '')}\n"
        f"主题：{episode.get('title', '')}\n\n"
        f"请把下面脚本压缩到 {DEEP_TARGET_MIN_SECONDS}-{DEEP_TARGET_MAX_SECONDS} 秒。\n"
        "必须保留“女：/男：”双主持格式，前15秒仍然要有反常识结论、观众反问和核心答案。\n"
        "删掉重复解释、课程式铺垫和不影响结论的细节，只输出可朗读脚本。\n"
        "压缩原因：" + "；".join(report.get("reasons", [])) + "\n"
    )
    return clean_script_output(call_llm(prompt, script))


def rewrite_dialogue_script_for_duration(series: Dict, episode: Dict, script: str, report: Dict) -> str:
    # 兼容旧调用名，实际统一交给“脚本时长优化代理”处理。
    return optimize_overtime_dialogue_script_with_agent(series, episode, script, report)


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
        "5. 观点连贯性是否足够：每段要从上一段自然推进，不能突然换能力点、公司点或资料点；出现观点跳跃、机械问答、资料清单式平铺时判为不通过。\n"
    )
    raw = call_llm(
        prompt,
        f"脚本：\n{script}\n\n研究报告：\n{research_report[:3000]}\n\n审校意见：\n{audit_report[:2000]}",
    )
    data = parse_json_object(raw)
    if not data:
        # 审校结果解析失败时不能静默放行，否则模型已经发现的观点跳跃会被吞掉。
        return {
            "blocked": True,
            "passed": False,
            "score": 0,
            "reasons": ["留存审校 JSON解析失败，按保守策略重写脚本"],
            "suggestions": ["补足观点承接、过渡句和具体场景，避免中段像资料清单或并列清单"],
            "raw": raw,
        }

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
        "5. 重写时必须补上观点承接：每段先接住上一段的结论，再推进下一层原因或例子，不要写成并列清单。\n"
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
    final_report["duration_agent_optimized"] = bool(report.get("duration_agent_optimized"))
    final_report["duration_agent_attempts"] = int(report.get("duration_agent_attempts") or 0)
    final_report["duration_agent_initial_seconds"] = float(report.get("duration_agent_initial_seconds") or 0.0)
    if retention_report.get("blocked"):
        # 整体留存不足不直接卡死生成，但会进入质量报告，方便人工复查。
        final_report["blocked"] = True
        final_report.setdefault("reasons", []).extend(retention_report.get("reasons", []))
    return script, final_report


def generate_dialogue_script_with_duration_guard(series: Dict, episode: Dict, research_report: str, audit_report: str) -> tuple[str, Dict]:
    # 脚本生成后马上估算时长，最多重写三次，避免长稿继续进入 TTS 和视频渲染。
    script = ""
    report = {}
    duration_agent_attempts = 0
    duration_agent_initial_seconds = 0.0
    for attempt in range(1, DEEP_RESEARCH_MAX_ATTEMPTS + 1):
        if attempt == 1:
            script = generate_dialogue_script(series, episode, research_report, audit_report)
        else:
            duration_agent_attempts += 1
            script = optimize_overtime_dialogue_script_with_agent(series, episode, script, report)
        report = assess_dialogue_duration(script)
        report["attempts"] = attempt
        if attempt == 1:
            duration_agent_initial_seconds = report.get("estimated_seconds", 0.0)
        report["duration_agent_optimized"] = duration_agent_attempts > 0
        report["duration_agent_attempts"] = duration_agent_attempts
        report["duration_agent_initial_seconds"] = duration_agent_initial_seconds
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


def _strip_svg_namespace(tag: str) -> str:
    # ElementTree 读取 SVG 时会带命名空间，这里只保留真实标签名方便白名单判断。
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _clean_svg_text_label(text: str, fallback: str = "", max_len: int = 14) -> str:
    # SVG 文本只能放短标签，先去掉 XML 敏感字符，避免兜底图再次被解析失败。
    clean = re.sub(r"[<>&]", "", str(text or "").strip())
    clean = re.sub(r"\s+", " ", clean).strip(" ：:，,。；;、")
    return clean[:max_len] or fallback


def _clean_svg_prompt_label(text: str) -> str:
    # prompt 里常带“生成/适合短视频”这类任务描述，画到 SVG 上会显得偏题，这里只保留产业或系统短词。
    clean = re.sub(r"[<>&]", "", str(text or "").strip())
    clean = re.sub(r"\s+", "", clean).strip(" ：:，,。；;、")
    if not clean:
        return ""
    if any(token in clean for token in ("适合短视频", "短视频封面", "保持iOS", "因果关系", "关键词")):
        return ""
    clean = re.sub(r"^(生成|连接|展示|呈现|突出)+", "", clean)
    clean = re.sub(r"(的)?(简洁科技)?SVG.*$", "", clean, flags=re.I)
    clean = re.sub(r"(的)?(短视频场景|关键环节|主题图|信息图).*$", "", clean)
    clean = clean.strip(" ：:，,。；;、")
    return _clean_svg_text_label(clean, "", 10)


def _extract_prompt_labels(prompt: str, limit: int = 3) -> List[str]:
    # 从资产 prompt 里提取中文短语，给未知资产名兜底，避免直接展示英文 key。
    labels = []
    for part in re.split(r"[：:，,。；;、\n]+", str(prompt or "")):
        clean = _clean_svg_prompt_label(part)
        if clean and re.search(r"[\u4e00-\u9fff]", clean) and clean not in labels:
            labels.append(clean)
        if len(labels) >= limit:
            break
    return labels


def _fallback_svg_labels(name: str, prompt: str = "", design: Dict = None) -> tuple[str, List[str]]:
    # 常见深度系列资产名先映射成观众能看懂的中文主题，未知资产再从 prompt/main_elements 里补足。
    key = re.sub(r"[^a-z0-9_]+", "_", str(name or "").lower())
    presets = {
        "data_center_cooling": ("数据中心冷却", ["服务器机柜", "冷却回路", "PUE"]),
        "fluorochemical_materials": ("氟化工材料", ["氟树脂", "制冷剂", "耐腐蚀"]),
        "semiconductor_process": ("半导体制程", ["高纯管路", "密封件", "良率"]),
        "risk_competition": ("竞争风险图谱", ["客户议价", "竞品压力", "依赖风险"]),
        "cost_stack": ("价值分布", ["GPU成本", "整机集成", "运维服务"]),
        "ai_infrastructure_stack": ("AI基础设施", ["算力", "存储网络", "散热供电"]),
        "private_ai_datacenter": ("私有AI机房", ["数据安全", "本地部署", "合规"]),
        "hybrid_cloud_bridge": ("混合AI架构", ["本地机房", "云服务", "数据流"]),
        "cio_procurement_dashboard": ("企业采购面板", ["预算", "交付", "运维"]),
        "gpu_vs_system": ("GPU不是系统", ["GPU", "服务器", "存储网络"]),
        "old_pc_tree": ("PC企业入口", ["客户基础", "渠道", "工程能力"]),
        "server_rack_glow": ("AI服务器整机", ["机柜", "GPU节点", "交付"]),
        "storage_data_pipeline": ("数据供给瓶颈", ["数据供给", "GPU等待", "存储瓶颈"]),
    }
    if key in presets:
        return presets[key]
    combined = f"{key} {str(prompt or '').lower()}"
    if key in ("hero", "bridge") and any(token in combined for token in ("机器人", "robot", "具身智能")):
        # 顶层主视觉如果是机器人主题，不能被“仓库/配送”等子场景抢走，先生成通用机器人作业图。
        return "机器人作业", ["机械臂", "传感器", "任务闭环"]
    # LLM 有时会把 scene asset 写成英文短语或截断短语，这里先按场景词映射回中文视觉主题。
    keyword_presets = [
        (("warehouse", "picking", "仓库", "搬运"), ("仓库搬运", ["货架", "拣选车", "标准路线"])),
        (("delivery", "locker", "campus", "外卖", "配送"), ("外卖配送", ["配送路线", "取餐柜", "到达节点"])),
        (("cleaning", "scrubber", "清洁", "洗地"), ("商用清洁", ["洗地机", "清洁路径", "夜间巡航"])),
        (("hotel", "room_service", "酒店", "送物"), ("酒店送物", ["客房门", "送物车", "电梯路线"])),
        (("home", "apartment", "drawer", "clothes", "cables", "家庭", "公寓", "抽屉", "衣服", "线缆"), ("家庭复杂度", ["多样房间", "杂物线缆", "售后成本"])),
        (("labor", "staff", "roi", "用工", "成本"), ("用工成本", ["人力班次", "替代成本", "多点复制"])),
        (("route", "bounded", "standard", "场景边界", "标准路线"), ("场景边界", ["固定路线", "充电点", "任务闭环"])),
        (("repetitive", "loop", "重复"), ("重复劳动", ["高频任务", "固定动作", "批量复制"])),
        (("remote", "control_room", "monitor", "运维", "监控"), ("远程运维", ["监控面板", "故障告警", "批量升级"])),
        (("robot", "grasp", "机器人", "具身智能"), ("机器人作业", ["机械臂", "传感器", "任务闭环"])),
    ]
    for tokens, labels in keyword_presets:
        if any(token in combined for token in tokens):
            return labels
    prompt_labels = _extract_prompt_labels(prompt, 4)
    main_elements = []
    if isinstance(design, dict) and isinstance(design.get("main_elements"), list):
        main_elements = [_clean_svg_text_label(item, "", 10) for item in design.get("main_elements", [])]
        main_elements = [item for item in main_elements if item]
    label = (prompt_labels + main_elements + [_clean_svg_text_label(name, "主题资产", 10)])[0]
    details = []
    # 详情标签去重并跳过主标签，避免同一个词在 SVG 内重复出现。
    for item in prompt_labels[1:] + main_elements:
        clean_item = _clean_svg_text_label(item, "", 10)
        if clean_item and clean_item != label and clean_item not in details:
            details.append(clean_item)
        if len(details) >= 3:
            break
    return label, details or ["主题对象", "关键环节", "产业影响"]


def _fallback_svg(label: str, palette: List[str] = None, detail_labels: List[str] = None) -> str:
    # 大模型生成失败时，也生成带主题标签的信息图兜底，不能退化成“视觉元素”通用图。
    colors = _normalize_palette(palette)
    safe_label = _clean_svg_text_label(label, "主题资产", 14)
    details = [_clean_svg_text_label(item, "", 10) for item in (detail_labels or [])]
    details = [item for item in details if item][:3]
    while len(details) < 3:
        details.append(["主题对象", "关键环节", "产业影响"][len(details)])
    variant_source = f"{safe_label}{''.join(details)}"
    if any(token in variant_source for token in ("仓库搬运", "货架", "拣选", "仓库")):
        # 仓库机器人场景画货架、拣选车和固定路线，隐藏文字后也能和普通流程图区分。
        body = (
            f'<rect x="58" y="92" width="82" height="120" rx="16" fill="#FFFFFF" stroke="{colors[2]}" stroke-width="5"/>'
            f'<rect x="72" y="112" width="54" height="14" rx="6" fill="{colors[2]}"/>'
            f'<rect x="72" y="142" width="54" height="14" rx="6" fill="{colors[3]}"/>'
            f'<rect x="72" y="172" width="54" height="14" rx="6" fill="{colors[1]}"/>'
            f'<rect x="190" y="126" width="82" height="54" rx="20" fill="{colors[2]}"/>'
            f'<circle cx="210" cy="188" r="10" fill="{colors[1]}"/>'
            f'<circle cx="252" cy="188" r="10" fill="{colors[1]}"/>'
            f'<rect x="304" y="106" width="54" height="92" rx="18" fill="#FFFFFF" stroke="{colors[3]}" stroke-width="5"/>'
            f'<polyline points="148,210 188,190 246,210 306,186 360,204" fill="transparent" stroke="{colors[2]}" stroke-width="6"/>'
        )
    elif any(token in variant_source for token in ("外卖配送", "配送路线", "取餐柜", "到达节点", "配送")):
        # 配送场景用路线地图和取餐柜，避免和仓库货架图同构。
        body = (
            f'<rect x="58" y="108" width="76" height="92" rx="18" fill="#FFFFFF" stroke="{colors[3]}" stroke-width="5"/>'
            f'<rect x="74" y="126" width="44" height="14" rx="6" fill="{colors[3]}"/>'
            f'<rect x="74" y="154" width="44" height="14" rx="6" fill="{colors[2]}"/>'
            f'<circle cx="96" cy="192" r="9" fill="{colors[1]}"/>'
            f'<circle cx="208" cy="116" r="28" fill="{colors[2]}"/>'
            f'<circle cx="324" cy="178" r="28" fill="{colors[3]}"/>'
            f'<polyline points="96,192 142,160 208,116 268,146 324,178" fill="transparent" stroke="{colors[1]}" stroke-width="7"/>'
            f'<circle cx="142" cy="160" r="8" fill="#FFFFFF" stroke="{colors[2]}" stroke-width="4"/>'
            f'<circle cx="268" cy="146" r="8" fill="#FFFFFF" stroke="{colors[3]}" stroke-width="4"/>'
        )
    elif any(token in variant_source for token in ("商用清洁", "洗地机", "清洁路径", "夜间巡航", "清洁")):
        # 商用清洁场景画洗地机和清扫轨迹，保证非文字层也能看出主题。
        body = (
            f'<rect x="86" y="132" width="176" height="58" rx="28" fill="{colors[2]}"/>'
            f'<rect x="130" y="104" width="78" height="42" rx="20" fill="#FFFFFF" stroke="{colors[2]}" stroke-width="5"/>'
            f'<circle cx="116" cy="194" r="14" fill="{colors[1]}"/>'
            f'<circle cx="226" cy="194" r="14" fill="{colors[1]}"/>'
            f'<ellipse cx="302" cy="176" rx="44" ry="24" fill="#FFFFFF" stroke="{colors[3]}" stroke-width="5"/>'
            f'<polyline points="72,112 132,92 194,112 254,92 338,116" fill="transparent" stroke="{colors[3]}" stroke-width="6"/>'
            f'<polyline points="70,218 128,206 190,220 254,206 338,218" fill="transparent" stroke="{colors[2]}" stroke-width="5"/>'
        )
    elif any(token in variant_source for token in ("酒店送物", "客房门", "送物车", "电梯路线", "酒店")):
        # 酒店送物场景用走廊门牌和送物车，不和配送地图混在一起。
        body = (
            f'<rect x="64" y="92" width="72" height="116" rx="12" fill="#FFFFFF" stroke="{colors[2]}" stroke-width="5"/>'
            f'<circle cx="120" cy="152" r="5" fill="{colors[3]}"/>'
            f'<rect x="174" y="102" width="72" height="106" rx="12" fill="#FFFFFF" stroke="#D1D1D6" stroke-width="4"/>'
            f'<circle cx="230" cy="154" r="5" fill="{colors[3]}"/>'
            f'<rect x="284" y="126" width="66" height="56" rx="20" fill="{colors[3]}"/>'
            f'<rect x="302" y="96" width="30" height="34" rx="12" fill="#FFFFFF" stroke="{colors[3]}" stroke-width="4"/>'
            f'<circle cx="302" cy="190" r="10" fill="{colors[1]}"/>'
            f'<circle cx="338" cy="190" r="10" fill="{colors[1]}"/>'
            f'<line x1="136" y1="212" x2="354" y2="212" stroke="{colors[2]}" stroke-width="6"/>'
        )
    elif any(token in variant_source for token in ("家庭复杂度", "多样房间", "杂物线缆", "售后成本", "家庭")):
        # 家庭机器人难点用不规则房间和杂物线缆，表达非标准环境。
        body = (
            f'<rect x="58" y="88" width="112" height="84" rx="18" fill="#FFFFFF" stroke="{colors[2]}" stroke-width="5"/>'
            f'<rect x="196" y="100" width="78" height="104" rx="18" fill="#FFFFFF" stroke="{colors[3]}" stroke-width="5"/>'
            f'<rect x="298" y="126" width="62" height="60" rx="16" fill="#FFFFFF" stroke="#D1D1D6" stroke-width="4"/>'
            f'<circle cx="96" cy="142" r="16" fill="{colors[3]}"/>'
            f'<ellipse cx="234" cy="156" rx="26" ry="14" fill="{colors[2]}"/>'
            f'<polyline points="72,206 112,186 146,210 198,190 246,214 312,192 360,210" fill="transparent" stroke="{colors[1]}" stroke-width="6"/>'
            f'<circle cx="340" cy="108" r="12" fill="{colors[3]}"/>'
        )
    elif any(token in variant_source for token in ("机器人作业", "机械臂", "传感器", "任务闭环", "重复劳动", "场景边界", "固定路线")):
        # 通用机器人作业画机械臂、传感器和闭环路线，给新主题一个默认但不空泛的图形。
        body = (
            f'<circle cx="104" cy="178" r="30" fill="{colors[2]}"/>'
            f'<line x1="126" y1="156" x2="184" y2="116" stroke="{colors[1]}" stroke-width="12"/>'
            f'<line x1="184" y1="116" x2="248" y2="146" stroke="{colors[3]}" stroke-width="12"/>'
            f'<circle cx="184" cy="116" r="16" fill="#FFFFFF" stroke="{colors[2]}" stroke-width="5"/>'
            f'<polygon points="250,132 292,146 252,162" fill="{colors[1]}"/>'
            f'<rect x="300" y="102" width="54" height="76" rx="18" fill="#FFFFFF" stroke="{colors[2]}" stroke-width="5"/>'
            f'<polyline points="72,214 138,198 208,216 282,198 358,212" fill="transparent" stroke="{colors[2]}" stroke-width="5"/>'
        )
    elif any(token in variant_source for token in ("工程团队", "稳定运行", "集群调度", "网络排障", "安全治理", "系统跑稳", "运维", "故障")):
        # 系统跑稳主题用运行状态面板，表达监控、调度曲线和多节点健康状态，而不是单纯画芯片或机柜。
        body = (
            f'<rect x="58" y="104" width="304" height="92" rx="24" fill="#FFFFFF" stroke="{colors[2]}" stroke-width="5"/>'
            f'<circle cx="78" cy="146" r="18" fill="{colors[3]}"/>'
            f'<rect x="58" y="166" width="42" height="30" rx="14" fill="{colors[2]}"/>'
            f'<rect x="82" y="124" width="74" height="18" rx="9" fill="{colors[2]}"/>'
            f'<rect x="82" y="152" width="44" height="14" rx="7" fill="{colors[3]}"/>'
            f'<rect x="82" y="174" width="62" height="10" rx="5" fill="#D1D1D6"/>'
            f'<polyline points="88,170 136,146 184,158 232,124 292,142 340,112" fill="transparent" stroke="{colors[3]}" stroke-width="6"/>'
            f'<circle cx="136" cy="146" r="8" fill="{colors[2]}"/>'
            f'<circle cx="232" cy="124" r="8" fill="{colors[3]}"/>'
            f'<circle cx="340" cy="112" r="8" fill="{colors[2]}"/>'
            f'<rect x="188" y="166" width="44" height="18" rx="9" fill="{colors[1]}"/>'
            f'<rect x="252" y="166" width="44" height="18" rx="9" fill="{colors[2]}"/>'
            f'<rect x="316" y="166" width="24" height="18" rx="9" fill="{colors[3]}"/>'
        )
    elif any(token in variant_source for token in ("服务器", "机柜", "基础设施", "冷却", "散热", "供电")):
        # 服务器/基础设施场景用分层机柜，表达算力、存储、网络和散热的堆栈感。
        body = (
            f'<rect x="88" y="104" width="244" height="110" rx="22" fill="#FFFFFF" stroke="{colors[2]}" stroke-width="5"/>'
            f'<rect x="112" y="124" width="196" height="20" rx="8" fill="{colors[2]}"/>'
            f'<rect x="112" y="154" width="196" height="20" rx="8" fill="{colors[3]}"/>'
            f'<rect x="112" y="184" width="196" height="20" rx="8" fill="{colors[1]}"/>'
            f'<circle cx="336" cy="136" r="10" fill="{colors[3]}"/>'
            f'<circle cx="336" cy="180" r="10" fill="{colors[2]}"/>'
        )
    elif any(token in variant_source for token in ("采购", "预算", "运维", "交付", "CIO")):
        # 采购/运维场景用仪表盘结构，隐藏文字后仍能区别于普通系统图。
        body = (
            f'<rect x="44" y="112" width="332" height="94" rx="22" fill="#FFFFFF" stroke="{colors[2]}" stroke-width="5"/>'
            f'<circle cx="84" cy="144" r="14" fill="{colors[3]}"/>'
            f'<rect x="114" y="132" width="112" height="18" rx="9" fill="{colors[2]}"/>'
            f'<rect x="114" y="164" width="206" height="14" rx="7" fill="#D1D1D6"/>'
            f'<polyline points="260,170 292,146 326,158 354,130" fill="transparent" stroke="{colors[3]}" stroke-width="6"/>'
        )
    elif any(token in variant_source for token in ("数据", "管道", "混合", "云", "安全", "合规")):
        # 数据/云/合规场景用节点网络，避免和机柜、采购面板同构。
        body = (
            f'<circle cx="92" cy="144" r="34" fill="{colors[2]}"/>'
            f'<circle cx="210" cy="122" r="42" fill="#FFFFFF" stroke="{colors[3]}" stroke-width="6"/>'
            f'<circle cx="322" cy="166" r="34" fill="{colors[3]}"/>'
            f'<line x1="126" y1="138" x2="168" y2="128" stroke="{colors[1]}" stroke-width="6"/>'
            f'<line x1="252" y1="136" x2="288" y2="154" stroke="{colors[1]}" stroke-width="6"/>'
            f'<polyline points="64,214 150,202 232,216 326,198 364,208" fill="transparent" stroke="{colors[2]}" stroke-width="5"/>'
        )
    else:
        # 其他场景保留桥接结构，表达多个环节被集成为一个可交付系统。
        body = (
            f'<rect x="46" y="116" width="96" height="72" rx="18" fill="{colors[2]}"/>'
            f'<rect x="162" y="116" width="96" height="72" rx="18" fill="{colors[3]}"/>'
            f'<rect x="278" y="116" width="96" height="72" rx="18" fill="#FFFFFF" stroke="{colors[2]}" stroke-width="5"/>'
            f'<line x1="142" y1="152" x2="162" y2="152" stroke="{colors[1]}" stroke-width="6"/>'
            f'<line x1="258" y1="152" x2="278" y2="152" stroke="{colors[1]}" stroke-width="6"/>'
            f'<polyline points="64,214 142,202 210,214 286,196 356,210" fill="transparent" stroke="{colors[2]}" stroke-width="5"/>'
        )
    system_labels = {"工程团队", "稳定运行", "集群调度", "供电冷却", "网络排障", "安全治理"}
    detail_source = details if safe_label in system_labels else [safe_label, *details]
    detail_labels = []
    for item in detail_source:
        clean_item = _clean_svg_text_label(item, "", 7)
        if clean_item and clean_item not in detail_labels:
            detail_labels.append(clean_item)
        if len(detail_labels) >= 3:
            break
    detail_chips = "".join(
        [
            f'<rect x="{46 + index * 116}" y="214" width="98" height="26" rx="13" fill="#FFFFFF" stroke="#D1D1D6" stroke-width="1"/>'
            f'<text x="{58 + index * 116}" y="232" fill="{colors[1]}" font-size="13" font-weight="700">{label}</text>'
            for index, label in enumerate(detail_labels[:3])
        ]
    )
    return (
        f'<svg width="420" height="260" viewBox="0 0 420 260" xmlns="http://www.w3.org/2000/svg">'
        f'<rect x="12" y="12" width="396" height="236" rx="32" fill="{colors[0]}"/>'
        f'{body}'
        f'{detail_chips}'
        f'</svg>'
    )


def _is_stale_generic_bridge_svg(path: str) -> bool:
    # 旧兜底图的非文字部分是固定三方块流程图；命中后允许重渲染刷新成主题图。
    if not path or not os.path.exists(path):
        return False
    try:
        svg = read_text_if_exists(path, limit=3000)
    except OSError:
        return False
    return (
        'x="46" y="116" width="96" height="72"' in svg
        and 'x="162" y="116" width="96" height="72"' in svg
        and 'x="278" y="116" width="96" height="72"' in svg
        and 'points="64,214 142,202 210,214 286,196 356,210"' in svg
    )


def sanitize_svg(svg_text: str, fallback_label: str = "主题资产", palette: List[str] = None, fallback_details: List[str] = None) -> str:
    # SVG 来自大模型，必须只保留基础图形，禁止脚本、外链、事件属性和 foreignObject。
    raw = (svg_text or "").strip()
    match = re.search(r"<svg[\s\S]*?</svg>", raw, flags=re.I)
    if match:
        raw = match.group(0)
    allowed_tags = {"svg", "g", "rect", "circle", "ellipse", "line", "polyline", "polygon", "text"}
    blocked_tags = {"script", "foreignObject", "image", "iframe", "style", "use"}
    allowed_attrs = {
        "x", "y", "x1", "y1", "x2", "y2", "cx", "cy", "r", "rx", "ry",
        "width", "height", "viewBox", "points", "d", "fill", "stroke", "stroke-width",
        "opacity", "font-size", "font-weight", "text-anchor", "dominant-baseline",
    }

    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return _fallback_svg(fallback_label, palette, fallback_details)

    def clean_node(node):
        tag = _strip_svg_namespace(node.tag)
        if tag in blocked_tags or tag not in allowed_tags:
            return None
        clean = ET.Element(tag)
        for key, value in node.attrib.items():
            attr = _strip_svg_namespace(key)
            lower_value = str(value).lower()
            if attr.startswith("on") or attr not in allowed_attrs:
                continue
            if "url(" in lower_value or "javascript:" in lower_value or "http://" in lower_value or "https://" in lower_value:
                continue
            clean.set(attr, str(value))
        if tag == "svg":
            clean.set("xmlns", "http://www.w3.org/2000/svg")
            clean.set("width", clean.get("width") or "420")
            clean.set("height", clean.get("height") or "260")
            clean.set("viewBox", clean.get("viewBox") or f"0 0 {clean.get('width')} {clean.get('height')}")
        if node.text and tag == "text":
            clean.text = re.sub(r"[<>]", "", node.text)[:80]
        for child in list(node):
            child_clean = clean_node(child)
            if child_clean is not None:
                clean.append(child_clean)
        return clean

    cleaned = clean_node(root)
    if cleaned is None:
        return _fallback_svg(fallback_label, palette, fallback_details)
    return ET.tostring(cleaned, encoding="unicode", short_empty_elements=True)


def _normalize_palette(palette: List[str] = None) -> List[str]:
    # 调色板不足时补默认色，避免 LLM 少返回颜色导致合成中断。
    defaults = ["#F5F5F7", "#1D1D1F", "#007AFF", "#FF9500", "#8E8E93"]
    result = []
    for item in palette or []:
        clean = str(item).strip()
        if re.fullmatch(r"#[0-9A-Fa-f]{6}", clean):
            result.append(clean.upper())
    for item in defaults:
        if len(result) >= 5:
            break
        if item not in result:
            result.append(item)
    return result[:5]


def _svg_float(value: str, default: float = 0.0) -> float:
    try:
        return float(re.sub(r"[^0-9.\-]", "", str(value)))
    except (TypeError, ValueError):
        return default


def _svg_color(value: str, default: str = "#1D1D1F") -> str:
    clean = str(value or "").strip()
    if re.fullmatch(r"#[0-9A-Fa-f]{6}", clean):
        return clean
    if clean.lower() in ("none", "transparent"):
        return "transparent"
    return default


def _svg_opacity(value: str, default: float = 1.0) -> float:
    # SVG 兜底渲染也要尊重 opacity，背景纹理常靠低透明度保持不抢正文。
    try:
        opacity = float(value)
    except (TypeError, ValueError):
        opacity = default
    return max(0.0, min(1.0, opacity))


def _svg_rgba(value: str, opacity: float = 1.0, default: str = "#1D1D1F"):
    color = _svg_color(value, default)
    if color == "transparent":
        return None
    return tuple(int(color[index:index + 2], 16) for index in (1, 3, 5)) + (int(255 * opacity),)


def _remove_svg_text_nodes(node) -> None:
    # 背景和舞台 SVG 作为纹理使用时隐藏模型内文字，但保留其它图形和透明度。
    for child in list(node):
        if _strip_svg_namespace(child.tag) == "text":
            node.remove(child)
            continue
        _remove_svg_text_nodes(child)


def render_svg_to_image(svg_path: str, size: tuple[int, int], include_text: bool = True) -> Optional[object]:
    # 优先使用 cairosvg；没有依赖时解析基础 SVG 图形，保证本地环境也能出图。
    if not svg_path or not os.path.exists(svg_path):
        return None
    try:
        from io import BytesIO
        from PIL import Image
        import cairosvg

        if include_text:
            png_bytes = cairosvg.svg2png(url=svg_path, output_width=size[0], output_height=size[1])
        else:
            with open(svg_path, "r", encoding="utf-8") as f:
                root = ET.fromstring(sanitize_svg(f.read()))
            _remove_svg_text_nodes(root)
            svg_bytes = ET.tostring(root, encoding="utf-8", short_empty_elements=True)
            png_bytes = cairosvg.svg2png(bytestring=svg_bytes, output_width=size[0], output_height=size[1])
        if png_bytes:
            return Image.open(BytesIO(png_bytes)).convert("RGBA")
    except Exception:
        pass

    try:
        from PIL import Image, ImageDraw

        with open(svg_path, "r", encoding="utf-8") as f:
            root = ET.fromstring(sanitize_svg(f.read()))
        view_box = root.get("viewBox", f"0 0 {root.get('width', 420)} {root.get('height', 260)}").split()
        source_w = _svg_float(view_box[2], 420) if len(view_box) >= 4 else _svg_float(root.get("width"), 420)
        source_h = _svg_float(view_box[3], 260) if len(view_box) >= 4 else _svg_float(root.get("height"), 260)
        sx = size[0] / max(source_w, 1)
        sy = size[1] / max(source_h, 1)
        image = Image.new("RGBA", size, (255, 255, 255, 0))

        def xy(x, y):
            return x * sx, y * sy

        def draw_on_overlay(callback):
            overlay = Image.new("RGBA", size, (255, 255, 255, 0))
            overlay_draw = ImageDraw.Draw(overlay)
            callback(overlay_draw)
            image.alpha_composite(overlay)

        def draw_node(node, inherited_opacity: float = 1.0):
            tag = _strip_svg_namespace(node.tag)
            node_opacity = inherited_opacity * _svg_opacity(node.get("opacity"), 1.0)
            if node_opacity <= 0:
                return
            fill = _svg_color(node.get("fill"), "#1D1D1F")
            stroke = _svg_color(node.get("stroke"), fill)
            stroke_width = max(1, int(_svg_float(node.get("stroke-width"), 2) * max(sx, sy)))
            fill_value = _svg_rgba(fill, node_opacity)
            stroke_value = _svg_rgba(stroke, node_opacity)
            if tag == "rect":
                x, y = xy(_svg_float(node.get("x")), _svg_float(node.get("y")))
                w, h = _svg_float(node.get("width")) * sx, _svg_float(node.get("height")) * sy
                radius = int(_svg_float(node.get("rx"), 0) * sx)
                draw_on_overlay(lambda d: d.rounded_rectangle((x, y, x + w, y + h), radius=radius, fill=fill_value, outline=stroke_value, width=stroke_width))
            elif tag == "circle":
                cx, cy = xy(_svg_float(node.get("cx")), _svg_float(node.get("cy")))
                r = _svg_float(node.get("r")) * min(sx, sy)
                draw_on_overlay(lambda d: d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=fill_value, outline=stroke_value, width=stroke_width))
            elif tag == "ellipse":
                cx, cy = xy(_svg_float(node.get("cx")), _svg_float(node.get("cy")))
                rx, ry = _svg_float(node.get("rx")) * sx, _svg_float(node.get("ry")) * sy
                draw_on_overlay(lambda d: d.ellipse((cx - rx, cy - ry, cx + rx, cy + ry), fill=fill_value, outline=stroke_value, width=stroke_width))
            elif tag == "line":
                draw_on_overlay(lambda d: d.line((xy(_svg_float(node.get("x1")), _svg_float(node.get("y1"))), xy(_svg_float(node.get("x2")), _svg_float(node.get("y2")))), fill=stroke_value, width=stroke_width))
            elif tag in ("polyline", "polygon"):
                pairs = re.findall(r"(-?\d+(?:\.\d+)?),?\s*(-?\d+(?:\.\d+)?)", node.get("points", ""))
                points = [xy(float(x), float(y)) for x, y in pairs]
                if len(points) >= 2 and tag == "polyline":
                    draw_on_overlay(lambda d: d.line(points, fill=stroke_value, width=stroke_width))
                elif len(points) >= 3:
                    draw_on_overlay(lambda d: d.polygon(points, fill=fill_value, outline=stroke_value))
            elif tag == "text":
                if not include_text:
                    return
                x, y = xy(_svg_float(node.get("x")), _svg_float(node.get("y")))
                font_size = int(max(14, _svg_float(node.get("font-size"), 24) * min(sx, sy)))
                text_fill = _svg_rgba(fill, node_opacity, "#1D1D1F") or _svg_rgba("#1D1D1F", node_opacity)
                draw_on_overlay(lambda d: d.text((x, y), node.text or "", font=_font(font_size, "700" in str(node.get("font-weight", ""))), fill=text_fill))
            for child in list(node):
                draw_node(child, node_opacity)

        draw_node(root)
        return image
    except Exception:
        return None


def paste_svg_asset(base_image, svg_path: str, box: tuple[int, int, int, int], include_text: bool = False) -> bool:
    # 把 SVG 渲染到指定区域；失败时返回 False，由调用方继续走文字/几何兜底。
    asset = render_svg_to_image(svg_path, (box[2] - box[0], box[3] - box[1]), include_text=include_text)
    if asset is None:
        return False
    base_image.paste(asset, (box[0], box[1]), asset)
    return True


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


def match_visual_scene(text: str, visual_design: Dict = None) -> Dict:
    # 根据本段台词命中视觉元素；命中不到时用 hero 兜底，保持每张卡都有同一套视觉语言。
    if not isinstance(visual_design, dict):
        return {}
    clean = re.sub(r"\s+", "", text or "")
    clean_lower = re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", clean).lower()
    scene_aliases = {
        # 常见产业资产名补充自然台词别名，避免必须逐字写出 scene_cards.keyword 才能换图。
        "old_pc_tree": ["卖电脑", "pc老牌", "老牌公司", "pc客户", "客户基础", "企业入口", "企业it能力"],
        "gpu_vs_system": ["不只是买gpu", "不只买gpu", "不是买gpu", "单买gpu", "gpu不是系统", "gpu不是完整系统", "不是一块显卡", "不是几块显卡", "gpu本身", "gpu都不造", "核心部件", "完整系统"],
        "ai_infrastructure_stack": ["ai基础设施", "基础设施", "算力存储网络", "cpu内存", "高速网络", "存储机柜", "电力散热", "网络和服务", "工程系统", "可用系统", "散热", "供电", "液冷"],
        "server_rack_glow": ["poweredge", "ai服务器", "服务器整机", "服务器机柜", "单台服务器", "gpu节点"],
        "cio_procurement_dashboard": ["企业采购", "cio", "可采购", "采购方案", "能买的方案", "企业能买", "进机房", "过流程", "稳定跑", "部署服务", "售后支持", "预算", "交付", "运维"],
        "private_ai_datacenter": ["私有ai", "本地数据中心", "数据安全", "本地部署", "合规", "金融", "医疗", "制造", "政府", "自建"],
        "hybrid_cloud_bridge": ["混合ai", "混合云", "本地加云", "本地数据中心和云", "公有云"],
        "storage_data_pipeline": ["存储瓶颈", "数据供不上", "gpu等待", "gpu等", "数据管道", "存储网络"],
        "isometric warehouse aisle with": ["仓库机器人", "仓库搬运", "仓库", "搬运", "拣选", "货从a点送到b点", "货架通道"],
        "sidewalk or campus delivery ro": ["外卖配送", "园区配送", "配送", "取餐", "取餐柜", "交接点", "送外卖"],
        "autonomous floor scrubber clea": ["清洁机器人", "商用清洁", "清洁", "闭店", "打扫", "洗地", "沿固定路线打扫"],
        "hotel service robot delivering": ["酒店机器人", "酒店送物", "酒店", "房号", "送水", "客房", "电梯"],
        "dashboard comparing staff shif": ["用工成本", "少跑多少腿", "少排多少班", "成本表", "排班", "吞吐量", "岗位替代"],
        "loop animation of robot moving": ["重复劳动", "任务重复", "高频任务", "固定动作", "重复", "能上班"],
        "warehouse map with marked rout": ["场景边界", "标准路线", "固定路线", "路线", "充电点", "交接点", "变量能被管理", "管理"],
        "control room dashboard monitor": ["远程运维", "监控", "故障告警", "批量升级", "维护", "运营面板"],
    }
    for scene in visual_design.get("scene_cards", []) or []:
        if not isinstance(scene, dict):
            continue
        keyword = re.sub(r"\s+", "", str(scene.get("keyword") or ""))
        if keyword and keyword in clean:
            cleaned_scene = dict(scene)
            cleaned_scene["label"] = visual_display_label(cleaned_scene.get("label") or keyword, keyword)
            return cleaned_scene
    best_scene = {}
    best_score = 0
    for scene in visual_design.get("scene_cards", []) or []:
        if not isinstance(scene, dict):
            continue
        asset_name = str(scene.get("asset") or "").strip()
        tokens = [
            scene.get("keyword", ""),
            scene.get("label", ""),
            *scene_aliases.get(asset_name, []),
        ]
        fallback_label, fallback_details = _fallback_svg_labels(asset_name, "", visual_design)
        tokens.extend([fallback_label, *fallback_details])
        score = 0
        for token in tokens:
            token_clean = re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", str(token or "")).lower()
            if len(token_clean) < 2 or token_clean in ("ai", "pc"):
                continue
            if token_clean in clean_lower:
                score += min(max(len(token_clean), 2), 12)
        if score > best_score:
            best_score = score
            best_scene = dict(scene)
    if best_scene and best_score >= 3:
        keyword = re.sub(r"\s+", "", str(best_scene.get("keyword") or ""))
        best_scene["label"] = visual_display_label(best_scene.get("label") or keyword, keyword)
        return best_scene
    elements = visual_design.get("main_elements", []) if isinstance(visual_design.get("main_elements"), list) else []
    if elements:
        element = visual_display_label(elements[0], "本期主视觉")
        return {"keyword": element, "asset": "hero", "label": element[:24]}
    return {"keyword": "主题", "asset": "hero", "label": "本期主视觉"}


def visual_element_label(item, fallback: str = "") -> str:
    # 大模型有时返回 {"name": "..."}，也可能被旧版本保存成 "{'name': '...'" 残片；这里统一转成可展示中文名。
    if isinstance(item, dict):
        for key in ("name", "label", "title", "keyword", "text"):
            value = item.get(key)
            if value:
                return visual_element_label(value, fallback)
        return fallback
    text = str(item or "").strip()
    if not text:
        return fallback
    for key in ("name", "label", "title", "keyword", "text", "visual_tone", "design_language"):
        match = re.search(rf"['\"]{key}['\"]\s*:\s*['\"]([^'\"]+)", text)
        if match:
            return match.group(1).strip()[:24]
    text = re.sub(r"^[{\[]+", "", text)
    text = re.sub(r"[}\]]+$", "", text)
    text = re.sub(r"^['\"]?(name|label|title|keyword|text)['\"]?\s*:\s*['\"]?", "", text, flags=re.I)
    text = text.split("',")[0].split('",')[0].strip(" '\"")
    return text[:24] or fallback


def visual_display_label(item, fallback: str = "") -> str:
    # 面向观众的标签必须是产业词，不展示“左侧/右侧/老树/根系”这类模型构图提示和隐喻。
    raw = visual_element_label(item, fallback).strip()
    if not raw:
        return fallback
    text = raw
    for word in ("画面左侧", "画面右侧", "左侧", "右侧", "中央", "中间", "左边", "右边", "上方", "下方", "前景", "背景"):
        text = text.replace(word, "")
    text = text.strip(" ：:·-—,，、")
    compact = re.sub(r"\s+", "", text)
    if "PC" in compact and "企业入口" in compact:
        return "PC企业入口"
    if "PC" in compact and ("老树" in compact or "根" in compact):
        return "PC客户基础"
    if "工程桥" in compact:
        return "整机集成交付"
    if "AI" in compact and "数据中心" in compact and compact in ("AI数据中心", "AI数据中心群"):
        return "AI数据中心"
    text = text.replace("老树", "老牌公司").replace("根系", "客户基础").replace("工程桥", "工程交付")
    text = text.strip(" ：:·-—,，、")
    return text[:24] or fallback


def cover_style_label(style: str) -> str:
    # 封面胶囊只显示频道或内容风格，不暴露 iOS/View 这类内部模板词。
    structured = visual_element_label(style)
    if structured and structured != str(style or "").strip():
        style = structured
    parts = [item.strip() for item in re.split(r"[、,，/|]+", style or "") if item.strip()]
    for item in parts:
        lowered = item.lower().replace(" ", "")
        if "ios" in lowered or "view" in lowered:
            continue
        return item[:10]
    return "科技财经"


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
    visual_design: Dict = None,
    scene: Dict = None,
    current_speaker: Optional[str] = None,
) -> str:
    from PIL import Image, ImageDraw

    width = 1920
    height = 1080
    image = Image.new("RGB", (width, height), "#F5F5F7")
    asset_paths = visual_design.get("asset_paths", {}) if isinstance(visual_design, dict) and isinstance(visual_design.get("asset_paths"), dict) else {}
    # 每期专属 background.svg 先铺满整张视频卡，后续 iOS 内容层再叠上去，形成统一的大背景。
    if asset_paths.get("background"):
        paste_svg_asset(image, asset_paths.get("background", ""), (0, 0, width, height))
    draw = ImageDraw.Draw(image)

    # 视频页只保留一层内容底板：有结构但不再套内层描边框，减少无效边距。
    draw.rounded_rectangle((64, 72, 1856, 1008), radius=44, fill="#FFFFFF")

    # 顶部不再绘制固定品牌文字，避免生成视频里出现模板感很强的 OpenNewsBrief 标识。
    if slide_index is not None and slide_total:
        page_text = f"{slide_index:02d} / {slide_total:02d}"
        page_font = _font(24, True)
        page_w, _ = _measure_text_size(draw, page_text, page_font)
        draw.rounded_rectangle((1600, 132, 1788, 184), radius=22, fill="#F2F2F7")
        draw.text((1600 + int((188 - page_w) / 2), 144), page_text, font=page_font, fill="#6E6E73")

    # 左侧视觉舞台承载 SVG 元素，并叠加芯片封装线索；即使台词很短，画面也不会只剩一块空白。
    stage = (96, 156, 832, 932)
    draw.rounded_rectangle(stage, radius=46, fill="#F2F2F7")
    draw.rounded_rectangle((stage[0] + 26, stage[1] + 26, stage[2] - 26, stage[3] - 26), radius=36, fill="#FFFFFF")
    for x in range(stage[0] + 86, stage[2] - 70, 78):
        for y_dot in range(stage[1] + 88, stage[3] - 110, 78):
            draw.ellipse((x, y_dot, x + 5, y_dot + 5), fill="#D1D1D6")
    visual_box = (stage[0] + 58, stage[1] + 58, stage[2] - 58, stage[3] - 156)
    draw.rounded_rectangle(visual_box, radius=30, fill="#F7F7FA", outline="#E5E5EA", width=2)
    draw.line(
        (stage[0] + 126, visual_box[3], stage[0] + 126, visual_box[3] + 54, stage[2] - 126, visual_box[3] + 54, stage[2] - 126, visual_box[3]),
        fill="#D1D1D6",
        width=4,
    )
    scene_label = visual_display_label(scene.get("label", "")) if isinstance(scene, dict) else ""
    if visual_design and scene:
        # 左侧舞台只贴一张前景 SVG，并隐藏 SVG 内文字；文字统一由卡片层绘制，避免模型文本错位。
        scene_asset_path = asset_paths.get(scene.get("asset", ""))
        foreground_path = scene_asset_path or asset_paths.get("hero", "")
        if foreground_path:
            paste_svg_asset(image, foreground_path, (stage[0] + 88, stage[1] + 98, stage[2] - 88, stage[1] + 502), include_text=False)
        label = scene.get("label", "")
        if label:
            label_font = _font(30, True)
            label_w, _ = _measure_text_size(draw, label, label_font)
            chip_left = stage[0] + 58
            draw.rounded_rectangle((chip_left, stage[1] + 32, chip_left + label_w + 56, stage[1] + 86), radius=24, fill="#1D1D1F")
            draw.text((chip_left + 28, stage[1] + 45), label, font=label_font, fill="#FFFFFF")
    else:
        draw.rounded_rectangle((stage[0] + 156, stage[1] + 188, stage[0] + 438, stage[1] + 344), radius=34, fill=accent)
        draw.rounded_rectangle((stage[0] + 366, stage[1] + 322, stage[0] + 626, stage[1] + 478), radius=34, fill="#1D1D1F")
        draw.line((stage[0] + 438, stage[1] + 344, stage[0] + 366, stage[1] + 322), fill="#8E8E93", width=10)
    elements = visual_design.get("main_elements", []) if isinstance(visual_design, dict) and isinstance(visual_design.get("main_elements"), list) else []
    # 产业线索只取清洗后的业务标签，避免把模型的构图方位词直接画到视频里。
    clue_words = []
    for item in elements:
        word = visual_display_label(item)[:10]
        if word and word not in clue_words:
            clue_words.append(word)
        if len(clue_words) >= 5:
            break
    while len(clue_words) < 3:
        clue_words.append(["材料", "基板", "算力"][len(clue_words)])
    # 左侧舞台顶部已经展示当前场景标签，底部节点跳过同名词，避免“工程团队”重复出现。
    stage_clue_words = [word for word in clue_words if word != scene_label][:3]
    while len(stage_clue_words) < 3:
        stage_clue_words.append(clue_words[len(stage_clue_words) % len(clue_words)])
    node_y = stage[3] - 114
    node_x = [stage[0] + 88, stage[0] + 314, stage[0] + 540]
    for index, word in enumerate(stage_clue_words[:3]):
        x = node_x[index]
        node_font = _font(20, True)
        node_w, _ = _measure_text_size(draw, word, node_font)
        node_width = min(max(node_w + 42, 128), 176)
        draw.rounded_rectangle((x, node_y, x + node_width, node_y + 48), radius=22, fill="#F2F2F7")
        draw.text((x + 20, node_y + 12), word, font=node_font, fill="#3A3A3C")
        if index < 2:
            draw.line((x + node_width + 12, node_y + 24, node_x[index + 1] - 18, node_y + 24), fill=accent, width=5)
            draw.ellipse((node_x[index + 1] - 24, node_y + 18, node_x[index + 1] - 12, node_y + 30), fill=accent)

    # 右侧正文区域加入轻量信息面板，填补短台词下的留白，同时不再用任何播放进度条。
    content_left = 884
    content_right = 1788
    title_font, title_lines = _fit_text_block(
        draw,
        title,
        content_right - content_left,
        150,
        start_size=54,
        min_size=36,
        bold=True,
        max_lines=2,
    )
    y = 194
    for line in title_lines:
        draw.text((content_left, y), line, font=title_font, fill="#1D1D1F")
        y += _measure_text_size(draw, line, title_font)[1] + 10

    y += 18
    subtitle_font, subtitle_lines = _fit_text_block(
        draw,
        subtitle,
        content_right - content_left,
        80,
        start_size=26,
        min_size=22,
        bold=False,
        max_lines=2,
    )
    for line in subtitle_lines:
        draw.text((content_left, y), line, font=subtitle_font, fill="#6E6E73")
        y += _measure_text_size(draw, line, subtitle_font)[1] + 8

    draw.rounded_rectangle((content_left, y + 26, content_left + 138, y + 74), radius=20, fill=accent)
    draw.text((content_left + 24, y + 36), "正在讲", font=_font(22, True), fill="#FFFFFF")
    y += 116

    body_font, body_lines = _fit_text_block(
        draw,
        body,
        content_right - content_left,
        360,
        start_size=58,
        min_size=34,
        bold=False,
        max_lines=6,
    )
    body_y = y
    body_line_height = _measure_text_size(draw, "国", body_font)[1]
    for line in body_lines:
        draw.text((content_left, body_y), line, font=body_font, fill="#1D1D1F")
        body_y += body_line_height + 22

    if current_speaker in ("female", "male"):
        # 有主持人条时给右侧底部预留独立空间，避免主持人和信息面板互相压住。
        insight_top = min(max(body_y + 24, 664), 724)
    else:
        insight_top = min(max(body_y + 34, 704), 786)
    draw.rounded_rectangle((content_left, insight_top, content_right, insight_top + 118), radius=30, fill="#F7F7FA")
    draw.text((content_left + 30, insight_top + 24), "产业线索", font=_font(24, True), fill="#6E6E73")
    insight_font = _font(24, True)
    for index, word in enumerate(clue_words[:3]):
        x = content_left + 30 + index * 270
        draw.rounded_rectangle((x, insight_top + 66, x + 224, insight_top + 104), radius=18, fill="#FFFFFF")
        draw.text((x + 20, insight_top + 73), word, font=insight_font, fill="#1D1D1F")

    if current_speaker in ("female", "male"):
        # 深度系列视频右侧底部只放轻量主持人头像队列，避免大面板破坏 iOS 图卡质感。
        host_specs = [
            ("female", "女主持", "#C79AA8", (1030, 918), 1088),
            ("male", "男主持", "#8FA8C1", (1392, 918), 1450),
        ]
        for role, label, host_accent, avatar_center, label_x in host_specs:
            is_active = current_speaker == role

            # 头像用更克制的圆形剪影，不画表情和大卡片，避免画面变成卡通后台界面。
            cx, cy = avatar_center
            ring_color = host_accent if is_active else "#D1D1D6"
            draw.ellipse((cx - 42, cy - 42, cx + 42, cy + 42), fill=ring_color)
            draw.ellipse((cx - 36, cy - 36, cx + 36, cy + 36), fill="#FFFFFF")
            if role == "female":
                draw.ellipse((cx - 24, cy - 24, cx + 24, cy + 12), fill="#4A2C3D")
                draw.ellipse((cx - 18, cy - 10, cx + 18, cy + 24), fill="#E8B8AA")
            else:
                draw.rounded_rectangle((cx - 22, cy - 24, cx + 22, cy + 2), radius=10, fill="#2D3A45")
                draw.ellipse((cx - 18, cy - 12, cx + 18, cy + 24), fill="#E9B997")
            draw.rounded_rectangle((cx - 26, cy + 22, cx + 26, cy + 52), radius=16, fill=host_accent if is_active else "#C7C7CC")

            label_color = "#1D1D1F" if is_active else "#8E8E93"
            draw.text((label_x, 894), label, font=_font(24, True), fill=label_color)
            if is_active:
                # 用三个小声波点表达“正在发言”，比文字按钮更轻，不抢正文注意力。
                for dot_index in range(3):
                    dot_x = label_x + dot_index * 18
                    draw.ellipse((dot_x, 934, dot_x + 8, 942), fill=host_accent)

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


def create_deep_slide_images(series: Dict, episode: Dict, script_path: str, audio_path: str, visual_design: Dict = None) -> List[str]:
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
        scene = match_visual_scene(segment["text"], visual_design)
        subtitle = f"{visual_label} · {series.get('title', '')} · {labels.get(segment['speaker'], '男主持')}"
        create_text_card(
            episode.get("title", ""),
            subtitle,
            segment["text"],
            image_path,
            accent=accents.get(segment["speaker"], "#007AFF"),
            slide_index=index + 1,
            slide_total=total,
            visual_design=visual_design,
            scene=scene,
            current_speaker=segment["speaker"],
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
    if actual_seconds > DEEP_AUDIO_WARNING_MAX_SECONDS:
        reasons.append(f"音频实际 {actual_seconds:.0f} 秒，超过 {DEEP_AUDIO_WARNING_MAX_SECONDS} 秒")
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
        "research_plan_path": os.path.join(output_dir, "research_plan.md"),
        "research_path": os.path.join(output_dir, "research.md"),
        "research_trace_path": os.path.join(output_dir, "research_trace.json"),
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

    print("[深度系列] 开始生成研究计划", flush=True)
    research_plan = build_research_plan(series, episode)
    print("\n========== 研究计划 ==========\n", flush=True)
    print(research_plan, flush=True)
    with open(result["research_plan_path"], "w", encoding="utf-8") as f:
        # 研究计划先于检索落盘，长调研时也能先检查方向和关键词。
        f.write(research_plan)

    review = run_research_review_loop(series, episode, research_plan=research_plan)
    sources = review["sources"]
    research = review["research"]
    audit = review["audit"]
    research_trace = {
        # trace 用机器可读结构保存每轮检索和审校结果，方便回看为什么需要重搜或兜底。
        "plan": {
            "series": series.get("title", ""),
            "episode": episode.get("title", ""),
            "question": _episode_question(episode),
        },
        "attempts": review.get("trace", []),
        "final_quality": review["quality"],
    }
    quality_payload = {
        "research_attempts": review["attempts"],
        "research_quality": review["quality"],
        "script_quality": {},
    }
    result["source_count"] = review["quality"].get("source_count", 0)
    with open(result["research_trace_path"], "w", encoding="utf-8") as f:
        json.dump(research_trace, f, ensure_ascii=False, indent=2)

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
            f.write("[研究计划]\n" + research_plan + "\n\n")
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
        f.write("[研究计划]\n" + research_plan + "\n\n")
        f.write("[研究报告]\n" + research + "\n\n")
        f.write("[审校结果]\n" + audit + "\n\n")
        f.write("[脚本备注]\n" + script_notes + "\n\n")
        f.write("[纪录片包]\n" + documentary_package + "\n\n")
        f.write("[脚本]\n" + script + "\n")
    result["agent_log_path"] = log_path
    result["review_ready"] = True
    return result


def generate_tts_from_script(series: Dict, episode: Dict, result: Dict) -> Dict:
    script_path = result.get("script_path") or episode.get("script_path", "")
    if not script_path or not os.path.exists(script_path):
        raise ValueError("找不到脚本文件，无法合成TTS")
    output_dir = os.path.dirname(script_path)
    audio_path = os.path.join(output_dir, "dialogue.mp3")
    print("[深度TTS] 开始生成口播音频", flush=True)
    audio_path = convert_dialogue_to_audio(script_path, audio_path)
    duration_report = validate_deep_audio_duration(audio_path)
    result["audio_path"] = audio_path
    result["actual_seconds"] = duration_report.get("actual_seconds", 0.0)
    if duration_report.get("blocked"):
        # TTS 阶段要保留完整音频，不能直接截断或丢弃；超时只写回状态，交给用户缩稿后重合成。
        reason = "；".join(duration_report.get("reasons", []))
        print("[深度TTS] 音频时长提醒：" + reason, flush=True)
        result["quality_block_reason"] = reason
    return result


def generate_video_from_audio(series: Dict, episode: Dict, result: Dict) -> Dict:
    today = datetime.date.today().strftime("%Y-%m-%d")
    script_path = result.get("script_path") or episode.get("script_path", "")
    if not script_path or not os.path.exists(script_path):
        raise ValueError("找不到脚本文件，无法生成视频")
    audio_path = result.get("audio_path") or episode.get("audio_path", "")
    if not audio_path or not os.path.exists(audio_path):
        raise ValueError("未找到TTS音频，请先合成TTS")
    duration_report = validate_deep_audio_duration(audio_path)
    if duration_report.get("blocked"):
        # 超过 3 分钟只做状态提醒，视频阶段仍继续合成完整成片，避免硬拦截导致用户拿不到结果。
        reason = "；".join(duration_report.get("reasons", []))
        print("[深度视频] 音频时长提醒：" + reason + "，将继续合成完整视频", flush=True)
        result["quality_block_reason"] = reason
    output_dir = os.path.dirname(audio_path)
    print("[深度视频] 开始生成视觉设计和本地 SVG 元素", flush=True)
    visual_design = result.get("visual_design") or load_visual_design(result.get("visual_design_path", ""))
    if not visual_design:
        visual_design = build_visual_design(series, episode, result, use_llm=True)
    elif should_rebuild_visual_design(series, episode, visual_design, result):
        # 旧版视觉设计如果已经明显偏离主题，重生成视频时直接刷新，避免继续复用错误图片。
        print("[深度视频] 检测到旧视觉设计与系统跑稳主题不匹配，重新生成视觉设计", flush=True)
        visual_design = build_visual_design(series, episode, result, use_llm=False)
    else:
        # 复用旧视觉设计时也补齐场景 SVG，避免老产物继续缺资产并回退到同一张 hero。
        visual_design = ensure_scene_card_svg_assets(visual_design, output_dir, use_llm=False)
        result["visual_design"] = visual_design
        result["visual_asset_paths"] = visual_design.get("asset_paths", {})
        visual_design_path = result.get("visual_design_path") or os.path.join(output_dir, "visual_design.json")
        with open(visual_design_path, "w", encoding="utf-8") as f:
            json.dump(visual_design, f, ensure_ascii=False, indent=2)
        result["visual_design_path"] = visual_design_path
    print("[深度视频] 开始生成画面卡片", flush=True)
    image_paths = create_deep_slide_images(series, episode, script_path, audio_path, visual_design=visual_design)
    print("[深度视频] 开始合成视频", flush=True)
    result["audio_path"] = audio_path
    result["actual_seconds"] = duration_report.get("actual_seconds", 0.0)
    result["video_path"] = step_video(audio_path, f"{episode.get('title', '')} {today}", image_paths)
    return result


def generate_video_from_script(series: Dict, episode: Dict, result: Dict) -> Dict:
    # 兼容旧调用：老入口仍可一口气先合成 TTS 再合成视频，新 UI 不再直接使用这个组合函数。
    result = generate_tts_from_script(series, episode, result)
    return generate_video_from_audio(series, episode, result)


def mark_episode_tts_generated(config: Dict, series_title: str, episode_title: str, result: Dict) -> Dict:
    series = find_series(config, series_title)
    episode = find_episode(series, episode_title)
    episode["generated"] = False
    episode["generated_at"] = ""
    episode["audio_generated_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    episode["published"] = False
    episode["published_at"] = ""
    for key in (
        "research_plan_path",
        "research_path",
        "research_trace_path",
        "audit_path",
        "script_path",
        "script_notes_path",
        "documentary_package_path",
        "quality_report_path",
        "audio_path",
        "agent_log_path",
        "source_count",
        "estimated_seconds",
        "actual_seconds",
        "quality_block_reason",
        "fallback_used",
    ):
        if result.get(key):
            episode[key] = result[key]
    # 重新合成 TTS 后旧视频和发布素材都可能过期，直接清空，避免误发布旧 MP4。
    for key in (
        "video_path",
        "publish_assets_path",
        "cover_path",
        "cover_option_paths",
        "publish_title",
        "publish_desc",
        "publish_tags",
        "cover_quality",
        "publish_gate",
    ):
        episode[key] = [] if key == "cover_option_paths" else ""
    episode["review_blocked"] = bool(result.get("review_blocked"))
    episode["review_ready"] = bool(result.get("review_ready") or (result.get("script_path") and not result.get("review_blocked")))
    return episode


def mark_episode_generated(config: Dict, series_title: str, episode_title: str, result: Dict) -> Dict:
    series = find_series(config, series_title)
    episode = find_episode(series, episode_title)
    episode["generated"] = bool(result.get("video_path"))
    episode["generated_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    episode["published"] = False
    episode["published_at"] = ""
    for key in (
        "research_plan_path",
        "research_path",
        "research_trace_path",
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
        "visual_design_path",
        "visual_asset_paths",
        "publish_title",
        "publish_desc",
        "publish_tags",
        "cover_quality",
        "publish_gate",
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


def _episode_output_dir(result: Dict) -> str:
    # 视觉设计和 SVG 资产跟随脚本/视频产物放在同一目录，方便复用和人工检查。
    for key in ("video_path", "script_path", "research_path", "audit_path"):
        path = result.get(key, "")
        if path:
            return os.path.dirname(os.path.abspath(path))
    return os.path.dirname(os.path.abspath(CONFIG_PATH))


def _fallback_visual_design(series: Dict, episode: Dict, text: str = "") -> Dict:
    # 没有 LLM 或 LLM 返回不可用时，按关键词生成一套可用视觉方案，不把封面绑死到某个系列模板。
    source = f"{series.get('title', '')} {episode.get('title', '')} {episode.get('question', '')} {text}"
    source_compact = re.sub(r"\s+", "", source)
    source_lower = source_compact.lower()
    elements = []
    system_theme_tokens = ("跑稳", "稳定运行", "稳定在线", "系统稳定", "系统跑稳", "运维", "集群调度", "网络故障", "安全治理", "工程人才", "跨领域工程", "复杂系统")
    is_system_theme = any(token.lower() in source_lower for token in system_theme_tokens)
    if is_system_theme:
        # “系统跑稳”主题优先展示运维和工程能力，避免被背景里的芯片、HBM、封装等历史段落抢走主视觉。
        system_rules = [
            (("工程人才", "工程团队", "跨领域工程", "背责任", "闭环", "跑稳的人", "系统跑稳的人"), "工程团队"),
            (("稳定运行", "稳定在线", "系统稳定", "系统跑稳", "跑稳", "可用系统", "可用算力"), "稳定运行"),
            (("集群调度", "调度", "任务优先级", "利用率", "gpu利用率"), "集群调度"),
            (("供电冷却", "供电", "冷却", "散热", "液冷", "电力"), "供电冷却"),
            (("网络故障", "网络", "拥塞", "丢包", "通信库", "光模块", "网卡"), "网络排障"),
            (("安全治理", "安全", "权限", "审计", "合规", "隔离"), "安全治理"),
        ]
        for keywords, element in system_rules:
            if any(str(keyword).lower() in source_lower for keyword in keywords) and element not in elements:
                elements.append(element)
        for element in ("稳定运行", "集群调度", "供电冷却"):
            if len(elements) >= 3:
                break
            if element not in elements:
                elements.append(element)
    else:
        element_rules = [
            ("味之素", "味精颗粒"), ("味精", "味精颗粒"), ("调味", "调味品包装"),
            ("胶带", "精密胶带"), ("空调", "冷却管路"), ("马桶", "精密陶瓷"),
            ("眼镜", "光学镜片"), ("电力", "变压器"), ("hbm", "高带宽内存"),
            ("gpu", "GPU芯片"), ("abf", "ABF薄膜"), ("封装", "封装基板"),
            ("数据中心", "服务器机柜"), ("agent", "智能体节点"), ("公司", "组织网络"),
        ]
        for keyword, element in element_rules:
            if keyword in source_lower and element not in elements:
                elements.append(element)
    if not elements:
        elements = ["核心对象", "关键机制", "影响结果"]
    composition = "system_diagram" if is_system_theme else "network_map" if any(token in source for token in ("Agent", "公司", "组织")) else "center_bridge"
    if not is_system_theme and any(token in source for token in ("电力", "HBM", "GPU集群", "数据中心")):
        composition = "system_diagram"
    cover_title = normalize_publish_title(episode.get("title", ""), episode.get("title", ""), series.get("title", ""))
    return {
        "cover_title": cover_title,
        "subtitle": " · ".join(elements[:3]),
        "style": "iOS干净排版、科技财经、强信息反差",
        "composition": composition,
        "palette": ["#F5F5F7", "#1D1D1F", "#007AFF", "#FF9500", "#8E8E93"],
        "main_elements": elements[:5],
        "svg_prompts": {
            "hero": f"生成{'、'.join(elements[:3])}的简洁科技SVG，适合短视频封面。",
            "bridge": f"生成连接{'、'.join(elements[:2])}的关键环节SVG，突出因果关系。",
            "background": "生成浅色科技网格和信息节点SVG，保持iOS风格。",
        },
        "scene_cards": [
            {"keyword": item[:4], "asset": "hero" if index == 0 else "bridge", "label": item}
            for index, item in enumerate(elements[:3])
        ],
    }


def normalize_visual_design(series: Dict, episode: Dict, design: Dict, context_text: str = "") -> Dict:
    # 统一视觉设计结构，避免不同模型返回字段不一致影响后续合成。
    fallback = _fallback_visual_design(series, episode, context_text)
    normalized = dict(fallback)
    if isinstance(design, dict):
        normalized.update({key: value for key, value in design.items() if value not in ("", None, [])})
    normalized["cover_title"] = normalize_publish_title(
        str(normalized.get("cover_title") or normalized.get("title") or ""),
        episode.get("title", "深度视频"),
        series.get("title", ""),
    )
    normalized["subtitle"] = str(normalized.get("subtitle") or fallback["subtitle"])[:42]
    normalized["style"] = str(normalized.get("style") or fallback["style"])[:80]
    normalized["composition"] = str(normalized.get("composition") or fallback["composition"])
    normalized["palette"] = _normalize_palette(normalized.get("palette") if isinstance(normalized.get("palette"), list) else [])
    if not isinstance(normalized.get("main_elements"), list):
        normalized["main_elements"] = fallback["main_elements"]
    normalized["main_elements"] = [
        visual_display_label(item)[:20]
        for item in normalized["main_elements"][:6]
        if visual_display_label(item)
    ] or fallback["main_elements"]
    if not isinstance(normalized.get("svg_prompts"), dict):
        normalized["svg_prompts"] = fallback["svg_prompts"]
    for key in ("hero", "bridge", "background"):
        normalized["svg_prompts"].setdefault(key, fallback["svg_prompts"][key])
    if not isinstance(normalized.get("scene_cards"), list):
        normalized["scene_cards"] = fallback["scene_cards"]
    scene_cards = []
    for item in normalized["scene_cards"][:8]:
        if not isinstance(item, dict):
            continue
        keyword = visual_element_label(item.get("keyword") or item.get("label") or "")
        if not keyword:
            continue
        scene_cards.append({
            "keyword": keyword[:20],
            "asset": str(item.get("asset") or "hero")[:30],
            "label": visual_display_label(item.get("label") or keyword)[:24],
        })
    normalized["scene_cards"] = scene_cards or fallback["scene_cards"]
    return normalized


def generate_visual_svg_asset(name: str, prompt: str, design: Dict, output_dir: str, use_llm: bool = True) -> str:
    # 每个视觉元素单独生成 SVG，封面和视频卡片都复用同一份资产。
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, f"{safe_filename(name)}.svg")
    fallback_label, fallback_details = _fallback_svg_labels(name, prompt, design)
    svg_text = ""
    if use_llm:
        llm_prompt = (
            "你是短视频视觉元素 SVG 生成器。\n"
            "只输出一个完整 SVG，不要 Markdown，不要解释。\n"
            "要求：只能使用 svg、g、rect、circle、ellipse、line、polyline、polygon、text；不要 path、外链图片、script、foreignObject。\n"
            f"整体风格：{design.get('style', '')}\n"
            f"调色板：{'、'.join(design.get('palette', []))}\n"
            f"元素任务：{prompt}\n"
        )
        try:
            svg_text = call_llm(llm_prompt)
        except Exception:
            svg_text = ""
    cleaned = sanitize_svg(
        svg_text or _fallback_svg(fallback_label, design.get("palette"), fallback_details),
        fallback_label=fallback_label,
        palette=design.get("palette"),
        fallback_details=fallback_details,
    )
    with open(path, "w", encoding="utf-8") as f:
        f.write(cleaned)
    return path


def ensure_scene_card_svg_assets(design: Dict, output_dir: str, use_llm: bool = False) -> Dict:
    # 场景卡片必须有真实 SVG 文件；默认用本地兜底图，避免视频阶段为每张 SVG 反复调大模型。
    if not isinstance(design, dict):
        return {}
    asset_dir = os.path.join(output_dir, "visual_assets")
    asset_paths = dict(design.get("asset_paths") if isinstance(design.get("asset_paths"), dict) else {})
    svg_prompts = design.get("svg_prompts") if isinstance(design.get("svg_prompts"), dict) else {}
    svg_prompts.setdefault("background", "生成适合作为整张深度视频卡片背景的主题 SVG，使用低对比科技纹理、网格和产业节点，不能喧宾夺主。")
    background_path = str(asset_paths.get("background") or "").strip()
    background_check_path = background_path if os.path.isabs(background_path) else os.path.join(os.getcwd(), background_path)
    if not background_path or not os.path.exists(background_check_path):
        # 所有深度主题都必须有 background.svg，视频卡片会把它铺满整张 1920x1080 画布。
        asset_paths["background"] = generate_visual_svg_asset("background", str(svg_prompts["background"]), design, asset_dir, use_llm=use_llm)
    for asset_name in ("hero", "bridge"):
        current_path = str(asset_paths.get(asset_name) or "").strip()
        check_path = current_path if os.path.isabs(current_path) else os.path.join(os.getcwd(), current_path)
        if current_path and os.path.exists(check_path) and _is_stale_generic_bridge_svg(check_path):
            # 旧版本会把主视觉也写成通用三方块图；这些资产存在时也要刷新，否则多数图卡仍会回退旧图。
            prompt = str(svg_prompts.get(asset_name) or f"生成{asset_name}主题 SVG。")
            asset_paths[asset_name] = generate_visual_svg_asset(asset_name, prompt, design, asset_dir, use_llm=use_llm)
    for scene in (design.get("scene_cards") or [])[:8]:
        if not isinstance(scene, dict):
            continue
        asset_name = str(scene.get("asset") or "").strip()
        if not asset_name:
            continue
        current_path = asset_paths.get(asset_name, "")
        check_path = current_path if os.path.isabs(current_path) else os.path.join(os.getcwd(), current_path)
        if current_path and os.path.exists(check_path) and not _is_stale_generic_bridge_svg(check_path):
            continue
        label = visual_display_label(scene.get("label") or scene.get("keyword") or asset_name, asset_name)
        keyword = visual_element_label(scene.get("keyword") or label, label)
        # 缺少专属 prompt 时，用场景标签补一个明确任务，让兜底 SVG 也贴合产业线索。
        prompt = str(svg_prompts.get(asset_name) or f"生成{label}的短视频场景 SVG，突出关键词：{keyword}。")
        asset_paths[asset_name] = generate_visual_svg_asset(asset_name, prompt, design, asset_dir, use_llm=use_llm)
        if asset_name not in svg_prompts:
            svg_prompts[asset_name] = prompt
    design["asset_paths"] = asset_paths
    design["svg_prompts"] = svg_prompts
    return design


def build_visual_design(series: Dict, episode: Dict, result: Dict, use_llm: bool = True) -> Dict:
    # 通用视觉规划：先理解本期元素和构图，再生成可复用 SVG，而不是套固定封面模板。
    output_dir = _episode_output_dir(result)
    script_text = read_text_if_exists(result.get("script_path", ""), limit=2500)
    research_text = read_text_if_exists(result.get("research_path", ""), limit=2500)
    context_text = f"{script_text}\n{research_text}"
    raw_design = {}
    if use_llm:
        prompt = (
            "你是深度视频视觉总监。请为本期生成通用封面和视频元素设计，只返回 JSON。\n"
            "不要套固定模板；根据系列、主题、脚本和研究资料选择适合本期的构图。\n"
            "JSON 字段：cover_title、subtitle、style、composition、palette、main_elements、svg_prompts、scene_cards。\n"
            "composition 可用 center_bridge、single_subject、system_diagram、network_map、timeline、contrast_split，但要按内容选择。\n"
            "svg_prompts 至少包含 hero、bridge、background；scene_cards 每项包含 keyword、asset、label，用于视频卡片匹配。\n"
            "main_elements 和 scene_cards.label 必须是观众能直接理解的产业环节短词，不要写左侧、右侧、中央、老树、根系、桥这类构图或隐喻词。\n"
            f"系列：{series.get('title', '')}\n主题：{episode.get('title', '')}\n问题：{_episode_question(episode)}\n"
        )
        try:
            raw_design = parse_json_object(call_llm(prompt, context_text))
        except Exception:
            raw_design = {}
    design = normalize_visual_design(series, episode, raw_design, context_text)
    asset_dir = os.path.join(output_dir, "visual_assets")
    asset_paths = {}
    for name, asset_prompt in list(design.get("svg_prompts", {}).items())[:6]:
        # SVG 只作为视频里的简洁图形素材，本地生成更稳定，也能减少多张图逐一调模型。
        asset_paths[name] = generate_visual_svg_asset(name, str(asset_prompt), design, asset_dir, use_llm=False)
    design["asset_paths"] = asset_paths
    design = ensure_scene_card_svg_assets(design, output_dir, use_llm=False)
    asset_paths = design["asset_paths"]

    design_path = os.path.join(output_dir, "visual_design.json")
    with open(design_path, "w", encoding="utf-8") as f:
        json.dump(design, f, ensure_ascii=False, indent=2)
    result["visual_design"] = design
    result["visual_design_path"] = design_path
    result["visual_asset_paths"] = asset_paths
    return design


def load_visual_design(path: str) -> Dict:
    # 老产物重渲染时优先复用已经生成过的视觉方案，避免同一期反复变风格。
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def should_rebuild_visual_design(series: Dict, episode: Dict, visual_design: Dict, result: Dict) -> bool:
    # 只处理旧版兜底规则造成的明显偏题，不覆盖模型正常生成出的可用视觉设计。
    if not isinstance(visual_design, dict) or not visual_design:
        return False
    script_text = read_text_if_exists(result.get("script_path", ""), limit=2500)
    research_text = read_text_if_exists(result.get("research_path", ""), limit=2500)
    fallback = _fallback_visual_design(series, episode, f"{script_text}\n{research_text}")
    expected_elements = [visual_display_label(item) for item in fallback.get("main_elements", [])]
    if "稳定运行" not in expected_elements:
        return False
    current_elements = [
        visual_display_label(item)
        for item in visual_design.get("main_elements", [])
        if visual_display_label(item)
    ]
    wrong_elements = {"味精颗粒", "高带宽内存", "GPU芯片", "封装基板", "ABF薄膜"}
    has_wrong_old_element = any(item in wrong_elements for item in current_elements)
    has_expected_theme = any(item in current_elements for item in expected_elements[:3])
    return has_wrong_old_element and not has_expected_theme


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
    # 发布审校重点看点击欲和搜索命中，平标题要拦下来，但不负责重写正文事实。
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
        "4. 如果标题平铺直叙、没有明确反差/悬念/冲突/疑问句，或你判断点击欲不足，必须判为不通过，score 不超过 6.5。\n"
        "5. tags 要覆盖核心关键词。\n"
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
    # 封面使用视觉设计 JSON 驱动构图；没有设计时才走规则兜底，避免绑定某个固定系列模板。
    from PIL import Image, ImageDraw

    output_path = os.path.join(output_dir, output_name)
    design = assets.get("visual_design") if isinstance(assets.get("visual_design"), dict) else {}
    if not design:
        design = normalize_visual_design(series, episode, {}, assets.get("desc", ""))
    colors = _normalize_palette(design.get("palette"))
    composition = str(design.get("composition") or "center_bridge")
    image = Image.new("RGB", (1080, 1080), colors[0])
    draw = ImageDraw.Draw(image)

    asset_paths = design.get("asset_paths", {}) if isinstance(design.get("asset_paths"), dict) else {}

    # 封面走信息流优先的 iOS 白卡版式：先让大字钩子可读，再保留少量产业线索。
    for x, y, r, color in ((248, 170, 92, "#ECECF0"), (884, 178, 126, "#F0F0F3"), (886, 850, 96, "#ECECF0")):
        draw.ellipse((x - r, y - r, x + r, y + r), fill=color)
    draw.rounded_rectangle((58, 66, 1022, 1014), radius=54, fill="#FFFFFF")
    for x in range(112, 968, 72):
        draw.ellipse((x, 902, x + 4, 906), fill="#D1D1D6")
    # 模型背景只作为卡片内部纹理，不能铺满整张封面破坏 iOS 留白。
    paste_svg_asset(image, asset_paths.get("background", ""), (604, 150, 974, 760))
    # 封面不再绘制固定品牌字样，避免生成物带模板感。
    # 系列名降级到底部浅灰，避免封面第一眼先看到栏目名。
    draw.text((112, 928), series.get("title", "Deep Series")[:22], font=_font(22, True), fill="#C7C7CC")

    if composition == "network_map":
        for x, y, r in ((738, 230, 54), (882, 372, 72), (704, 520, 58), (862, 690, 48)):
            draw.line((738, 230, x, y), fill="#D1D1D6", width=5)
            draw.ellipse((x - r, y - r, x + r, y + r), fill="#F2F2F7", outline=colors[2], width=5)
        visual_box = (640, 202, 940, 726)
    elif composition == "system_diagram":
        for y in (240, 370, 500, 630):
            draw.rounded_rectangle((648, y, 944, y + 76), radius=24, fill="#F5F5F7", outline="#E5E5EA", width=3)
            draw.line((796, y + 76, 796, y + 114), fill=colors[2], width=6)
        visual_box = (656, 206, 936, 706)
    elif composition == "single_subject":
        draw.rounded_rectangle((620, 178, 962, 746), radius=44, fill="#F5F5F7")
        visual_box = (650, 238, 932, 650)
    else:
        draw.rounded_rectangle((594, 162, 974, 762), radius=46, fill="#F5F5F7")
        draw.rounded_rectangle((632, 204, 936, 720), radius=34, fill="#FFFFFF")
        draw.rounded_rectangle((676, 440, 910, 508), radius=28, fill=colors[3])
        visual_box = (640, 218, 934, 670)

    pasted = paste_svg_asset(image, asset_paths.get("hero", ""), visual_box)
    if not pasted:
        elements = design.get("main_elements", []) if isinstance(design.get("main_elements"), list) else []
        for index, label in enumerate(elements[:3]):
            clean_label = visual_display_label(label)
            x = 660 + (index % 2) * 145
            y = 250 + index * 120
            draw.rounded_rectangle((x, y, x + 190, y + 76), radius=24, fill=colors[2 if index == 0 else 3])
            draw.text((x + 20, y + 22), clean_label[:8], font=_font(24, True), fill="#FFFFFF")
    paste_svg_asset(image, asset_paths.get("bridge", ""), (650, 470, 930, 650))
    elements = design.get("main_elements", []) if isinstance(design.get("main_elements"), list) else []
    # 封面底部节点和视频内“产业线索”共用清洗规则，避免同一坏标签在封面继续出现。
    cover_nodes = [visual_display_label(item)[:8] for item in elements[:3] if visual_display_label(item)]
    while len(cover_nodes) < 3:
        cover_nodes.append(["味之素", "ABF薄膜", "GPU封装"][len(cover_nodes)])
    # 底部节点展示“公司到算力底座”的关系，使用离散节点，避免看起来像播放进度条。
    node_positions = [(600, 792), (738, 832), (850, 792)]
    for index, (x, y_node) in enumerate(node_positions):
        if index:
            prev_x, prev_y = node_positions[index - 1]
            draw.line((prev_x + 56, prev_y + 26, x - 16, y_node + 26), fill="#D1D1D6", width=5)
        draw.ellipse((x - 10, y_node + 16, x + 10, y_node + 36), fill=colors[2 if index == 0 else 3])
        draw.rounded_rectangle((x + 18, y_node, x + 136, y_node + 52), radius=22, fill="#F7F7FA")
        draw.text((x + 34, y_node + 13), cover_nodes[index], font=_font(20, True), fill="#6E6E73")

    title_text = normalize_cover_text(cover_text if cover_text is not None else design.get("cover_title") or assets.get("cover_text", ""), assets.get("title", ""))
    if cover_text is None and design.get("cover_title"):
        title_text = str(design.get("cover_title"))[:16]
    title_font, title_lines = _fit_text_block(draw, title_text, 520, 330, start_size=108, min_size=58, bold=True, max_lines=3)
    y = 198
    for line in title_lines:
        draw.text((112, y), line, font=title_font, fill=colors[1])
        y += _measure_text_size(draw, line, title_font)[1] + 16
    subtitle = str(design.get("subtitle") or assets.get("title") or episode.get("title", ""))[:36]
    subtitle_font, subtitle_lines = _fit_text_block(draw, subtitle, 500, 98, start_size=30, min_size=22, bold=True, max_lines=2)
    y += 18
    for line in subtitle_lines:
        draw.text((112, y), line, font=subtitle_font, fill="#6E6E73")
        y += _measure_text_size(draw, line, subtitle_font)[1] + 8

    style_label = cover_style_label(str(design.get("style") or "科技财经"))
    draw.rounded_rectangle((112, 796, 402, 856), radius=26, fill=colors[1])
    draw.text((138, 812), style_label, font=_font(22, True), fill="#FFFFFF")
    for index, word in enumerate(cover_nodes[:3]):
        x = 112 + (index % 2) * 176
        y_chip = 652 + (index // 2) * 58
        draw.rounded_rectangle((x, y_chip, x + 156, y_chip + 44), radius=20, fill="#F2F2F7")
        draw.text((x + 20, y_chip + 10), word, font=_font(20, True), fill="#3A3A3C")
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


def assess_cover_quality(assets: Dict, cover_path: str = "") -> Dict:
    # 封面质量先做可稳定自动化的硬检查：文字长度、基础尺寸和明显英文残片。
    reasons: List[str] = []
    warnings: List[str] = []
    design = assets.get("visual_design") if isinstance(assets.get("visual_design"), dict) else {}
    cover_text = re.sub(r"\s+", "", str(assets.get("cover_text") or ""))
    cover_title = re.sub(r"\s+", "", str(design.get("cover_title") or assets.get("title") or ""))
    if len(cover_text) > DEEP_COVER_TEXT_MAX_CHARS:
        reasons.append(f"封面短文案超过{DEEP_COVER_TEXT_MAX_CHARS}字，移动端缩略图不稳定")
    if len(cover_title) > 16:
        reasons.append("封面主标题过长，容易裁切")

    labels = []
    for item in design.get("main_elements", []) or []:
        labels.append(visual_display_label(item))
    for scene in design.get("scene_cards", []) or []:
        if isinstance(scene, dict):
            labels.append(visual_display_label(scene.get("label") or scene.get("keyword")))
    english_fragments = [
        label for label in labels
        if re.search(r"[A-Za-z]", label or "") and not re.fullmatch(r"[A-Z0-9]{2,8}", label or "")
    ]
    if english_fragments:
        warnings.append("封面/画面标签含英文残片：" + "、".join(english_fragments[:3]))

    if cover_path:
        if not os.path.exists(cover_path):
            reasons.append("封面图片文件不存在")
        else:
            try:
                from PIL import Image
                with Image.open(cover_path) as image:
                    width, height = image.size
                if width < 720 or height < 720:
                    reasons.append("封面图片分辨率过低")
            except Exception as exc:
                warnings.append(f"封面图片无法读取：{exc}")
    return {"blocked": bool(reasons), "reasons": reasons, "warnings": warnings}


def assess_publish_gate(series: Dict, episode: Dict) -> Dict:
    # 这里现在只做质量风险评估，结果给数据回流使用，不再阻断 UI 待发布和上传流程。
    reasons: List[str] = []
    warnings: List[str] = []
    try:
        actual_seconds = float(episode.get("actual_seconds") or 0)
    except (TypeError, ValueError):
        actual_seconds = 0.0
    if actual_seconds > DEEP_TARGET_MAX_SECONDS:
        reasons.append(f"视频实际{actual_seconds:.0f}秒，超过150秒")

    if "source_count" in episode:
        try:
            source_count = int(episode.get("source_count") or 0)
        except (TypeError, ValueError):
            source_count = 0
        if source_count < DEEP_MIN_VALID_SOURCES:
            reasons.append(f"有效来源不足：{source_count}/{DEEP_MIN_VALID_SOURCES}")

    quality_reason = str(episode.get("quality_block_reason") or "")
    if quality_reason:
        if "超过" in quality_reason or "有效来源不足" in quality_reason:
            warnings.append(quality_reason)
        else:
            warnings.append("质量提醒：" + quality_reason)

    cover_quality = episode.get("cover_quality")
    if isinstance(cover_quality, dict):
        if cover_quality.get("blocked"):
            reasons.extend(str(item) for item in cover_quality.get("reasons", []))
        warnings.extend(str(item) for item in cover_quality.get("warnings", []))

    return {
        "blocked": bool(reasons),
        "series": series.get("title", ""),
        "episode": episode.get("title", ""),
        "reasons": reasons,
        "warnings": warnings,
    }


def generate_publish_assets(series: Dict, episode: Dict, result: Dict) -> Dict:
    # 发布信息只生成一次，后面发视频和发文案都直接复用。
    # title 直接作为 B 站标题使用，系列名留在简介和合集里，避免信息流里显得程式化。
    # B站信息流标题要先争取点击，但所有夸张点都必须来自脚本事实，避免骗点。
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
        "标题允许有轻标题党味道，优先写成反常识主体 + 被忽略位置/后果/疑问的短句；至少带一个反差、悬念、痛点或疑问句钩子，但不能编造事实。\n"
        "B站最终标题直接使用 title，不要强制套用系列名前缀；实体词或反差点尽量前置，让标题像真人写的短句。\n"
        "title_options 也按同样规则给 3 个自然标题备选，不要带系列名称前缀。\n"
        "封面只保留一个核心反差词或短问句，不要堆多个解释词，也不要写成长句。\n"
        "标题、封面文案、视频前3秒必须围绕同一个承诺，观众点进来后马上听到同一个答案方向。\n"
    )
    fallback_assets = {
        "title": episode.get("title", "深度视频"),
        "desc": f"{series.get('title', '')} / {episode.get('title', '')} 的深度内容发布文案。",
        "tags": "AI,深度内容,纪录片,口播",
        "cover_text": "AI 深度解析",
        "cover_prompt": "iOS 风格深度系列封面，简洁，清晰，白底，高对比，科技感",
        "comment_question": "你更赞同 A 还是 B？为什么",
        "title_options": [episode.get("title", "深度视频")],
        "cover_options": ["AI 深度解析"],
    }
    try:
        raw = call_llm(prompt, f"脚本：\n{script_text}\n\n研究报告：\n{research_text}")
        assets = parse_json_object(raw)
    except Exception as exc:
        # 发布文案是视频完成后的附属产物，LLM 网络失败时不能让已合成视频丢失状态。
        print(f"[深度发布] 发布信息 LLM 失败，使用默认发布素材：{exc}", flush=True)
        assets = {}
    if not assets:
        assets = fallback_assets

    try:
        assets, publish_review = polish_publish_assets_with_review(series, episode, assets, script_text, research_text)
    except Exception as exc:
        # 审校/改写同样依赖 LLM；失败时保留当前素材并标记为通过，保证 UI 能进入待发布状态。
        print(f"[深度发布] 发布审校 LLM 失败，保留当前发布素材：{exc}", flush=True)
        assets = normalize_publish_assets_payload(series, episode, assets)
        publish_review = {
            "blocked": False,
            "attempts": 0,
            "reviews": [],
            "final": {"blocked": False, "passed": True, "score": 0, "reasons": ["LLM 审校失败，已使用默认素材"], "suggestions": []},
        }
    assets["publish_review"] = publish_review

    output_dir = os.path.dirname(os.path.abspath(result.get("video_path") or result.get("script_path") or CONFIG_PATH))
    os.makedirs(output_dir, exist_ok=True)
    visual_design = result.get("visual_design") or load_visual_design(result.get("visual_design_path", ""))
    if not visual_design:
        visual_design = build_visual_design(series, episode, result, use_llm=False)
    assets["visual_design"] = visual_design
    assets["cover_path"] = create_deep_cover_image(series, episode, assets, output_dir)
    assets["cover_option_paths"] = create_deep_cover_options(series, episode, assets, output_dir)
    assets["cover_quality"] = assess_cover_quality(assets, assets["cover_path"])
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
        "research_plan_path",
        "research_path",
        "research_trace_path",
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


def build_episode_media_result(series: Dict, episode: Dict) -> Dict:
    # TTS 和视频两个阶段都需要同一组路径字段，集中在这里能避免两个入口漏传状态。
    return {
        "series": series.get("title", ""),
        "episode": episode.get("title", ""),
        "research_plan_path": episode.get("research_plan_path", ""),
        "research_path": episode.get("research_path", ""),
        "research_trace_path": episode.get("research_trace_path", ""),
        "audit_path": episode.get("audit_path", ""),
        "script_path": episode.get("script_path", ""),
        "script_notes_path": episode.get("script_notes_path", ""),
        "documentary_package_path": episode.get("documentary_package_path", ""),
        "audio_path": episode.get("audio_path", ""),
        "video_path": episode.get("video_path", ""),
        "agent_log_path": episode.get("agent_log_path", ""),
        "quality_report_path": episode.get("quality_report_path", ""),
        "visual_design_path": episode.get("visual_design_path", ""),
        "visual_asset_paths": episode.get("visual_asset_paths", {}),
        "cover_quality": episode.get("cover_quality", {}),
        "publish_gate": episode.get("publish_gate", {}),
        "source_count": episode.get("source_count", 0),
        "estimated_seconds": episode.get("estimated_seconds", 0.0),
        "actual_seconds": episode.get("actual_seconds", 0.0),
        "review_ready": bool(episode.get("review_ready")),
        "review_blocked": bool(episode.get("review_blocked")),
        "quality_block_reason": episode.get("quality_block_reason", ""),
        "fallback_used": bool(episode.get("fallback_used")),
    }


def generate_episode_tts_by_titles(series_title: str, episode_title: str) -> Dict:
    config = load_config()
    series = find_series(config, series_title)
    episode = find_episode(series, episode_title)
    if episode.get("review_blocked"):
        script_path = episode.get("script_path", "")
        if not script_path or not os.path.exists(script_path):
            raise ValueError("当前主题被审核阻断，请先补充资料或修改脚本后再合成TTS")
        # 有脚本产物时，审核建议只作为修改提醒，不再阻断后续手动合成。
        print("[深度TTS] 检测到审核提醒，已有脚本，继续合成TTS", flush=True)
        episode["review_blocked"] = False
        episode["review_ready"] = True
    result = build_episode_media_result(series, episode)
    result = generate_tts_from_script(series, episode, result)
    config = load_config()
    mark_episode_tts_generated(config, series_title, episode_title, result)
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
        # 有脚本和音频产物时，审核建议只作为修改提醒，不再阻断视频合成。
        print("[深度视频] 检测到审核提醒，已有脚本，继续生成视频", flush=True)
        episode["review_blocked"] = False
        episode["review_ready"] = True
    result = build_episode_media_result(series, episode)
    result = generate_video_from_audio(series, episode, result)
    assets = generate_publish_assets(series, episode, result)
    result["publish_assets_path"] = assets["path"]
    result["publish_title"] = assets["title"]
    result["publish_desc"] = assets["desc"]
    result["publish_tags"] = assets["tags"]
    result["cover_path"] = assets.get("cover_path", "")
    result["cover_option_paths"] = assets.get("cover_option_paths", [])
    result["cover_quality"] = assets.get("cover_quality", {})
    gate_input = dict(episode)
    gate_input.update(result)
    result["publish_gate"] = assess_publish_gate(series, gate_input)
    config = load_config()
    mark_episode_generated(config, series_title, episode_title, result)
    save_config(config)
    return result


def _metric_float(value, default: float = 0.0) -> float:
    # B站导出的表格可能带百分号或空值，这里统一转成浮点数，后续规则就能稳定计算。
    if value is None:
        return default
    text = str(value).strip().replace(",", "")
    if not text:
        return default
    is_percent = text.endswith("%")
    text = text.rstrip("%")
    try:
        number = float(text)
    except ValueError:
        return default
    if is_percent:
        return number / 100
    return number


def _first_metric_value(row: Dict, keys: List[str], default=""):
    for key in keys:
        if key in row and row.get(key) not in (None, ""):
            return row.get(key)
    return default


def _find_metric_value(row: Dict, keys: List[str]):
    # 详情页没有返回的字段必须保持缺失状态，不能用 0 伪装成真实指标。
    for key in keys:
        if key in row and row.get(key) not in (None, "", "-", "--", "暂无", "暂未更新"):
            return True, row.get(key)
    return False, None


def _metric_float_or_none(value):
    # 只在确实能解析成数字时才返回数值，像“2星”这类详情页展示值需要原样保留。
    parsed = _metric_float(value, None)
    return parsed


def _normalize_count_metric(value) -> int:
    # 数据总览中的播放、互动计数统一转成整数，便于报告汇总和比较。
    return int(_metric_float(value, 0))


def _normalize_optional_numeric_metric(value):
    # 播放分析中可能既有百分比，也有“2星”等等级；数字转数值，非数字保留原文。
    parsed = _metric_float_or_none(value)
    return parsed if parsed is not None else value


def normalize_feedback_metric_row(row: Dict) -> Dict:
    # 同时兼容手写 JSON 和 B站数据表的中文列名；详情页缺失的字段保持缺失，不做 0 兜底。
    normalized = {
        "series": str(_first_metric_value(row, ["series", "系列"], "")).strip(),
        "episode": str(_first_metric_value(row, ["episode", "主题", "标题", "title"], "")).strip(),
        "publish_title": str(_first_metric_value(row, ["publish_title", "发布标题", "稿件标题"], "")).strip(),
        "video_path": str(_first_metric_value(row, ["video_path", "视频路径"], "")).strip(),
    }
    for key in ("source", "aid", "bvid", "metric_sections"):
        if key in row and row.get(key) not in (None, ""):
            normalized[key] = row.get(key)

    count_fields = [
        ("views", ["views", "播放量", "播放"]),
        ("likes", ["likes", "点赞"]),
        ("danmaku", ["danmaku", "弹幕"]),
        ("comments", ["comments", "评论"]),
        ("shares", ["shares", "分享"]),
        ("favorites", ["favorites", "收藏"]),
        ("coins", ["coins", "投币"]),
        ("followers", ["followers", "涨粉", "新增粉丝"]),
        ("unfollows", ["unfollows", "取关", "取消关注"]),
    ]
    for field, aliases in count_fields:
        found, value = _find_metric_value(row, aliases)
        if found:
            normalized[field] = _normalize_count_metric(value)

    analysis_fields = [
        ("avg_view_seconds", ["avg_view_seconds", "平均观看秒数", "平均观看时长"]),
        ("completion_rate", ["completion_rate", "完播率"]),
        ("click_rate", ["click_rate", "点击率", "封面点击率", "封标点击率"]),
        ("three_second_exit_rate", ["three_second_exit_rate", "3秒跳出率", "三秒跳出率"]),
        ("interaction_rate", ["interaction_rate", "互动率"]),
        ("play_follower_rate", ["play_follower_rate", "播转粉率", "播放转粉率"]),
    ]
    for field, aliases in analysis_fields:
        found, value = _find_metric_value(row, aliases)
        if found:
            normalized[field] = _normalize_optional_numeric_metric(value)
    return normalized


def load_feedback_metrics(metrics_path: str = None) -> List[Dict]:
    # 这里只负责读取和规整指标；是否允许空数据由调用方按业务场景决定。
    path = metrics_path or DEEP_FEEDBACK_METRICS_FILE
    if not path or not os.path.exists(path):
        return []
    if path.lower().endswith(".csv"):
        with open(path, "r", encoding="utf-8-sig", newline="") as f:
            return [normalize_feedback_metric_row(row) for row in csv.DictReader(f)]
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    rows = data.get("videos", data) if isinstance(data, dict) else data
    if not isinstance(rows, list):
        return []
    return [normalize_feedback_metric_row(row) for row in rows if isinstance(row, dict)]


def _feedback_metrics_source(metrics_path: str = None) -> str:
    # B站自动回流文件需要识别来源，旧的列表指标不能继续冒充详情页指标。
    path = metrics_path or DEEP_FEEDBACK_METRICS_FILE
    if not path or not os.path.exists(path) or path.lower().endswith(".csv"):
        return ""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return ""
    return str(data.get("source") or "").strip() if isinstance(data, dict) else ""


def _assert_feedback_metrics_detail_source(metrics_path: str = None):
    # 只拦截明确标记为 B站旧来源的文件；手工文件没有 source 时仍按手工指标处理。
    source = _feedback_metrics_source(metrics_path)
    if source.startswith("bilibili_") and source != "bilibili_detail_page":
        raise RuntimeError(f"指标文件不是 B站详情页数据，拒绝分析：{source}，请重新点击“数据”采集详情页")


def collect_deep_feedback_metrics(metrics_path: str = None, auto_scrape: bool = True, output_dir: str = None) -> Dict:
    # 自动回流入口必须拿到真实指标；失败或 0 条都直接报错，避免后续用空数据做错误判断。
    if metrics_path:
        if not os.path.exists(metrics_path):
            raise RuntimeError(f"指标文件不存在：{metrics_path}")
        _assert_feedback_metrics_detail_source(metrics_path)
        metric_count = len(load_feedback_metrics(metrics_path))
        if metric_count <= 0:
            raise RuntimeError(f"指标文件没有有效数据：{metrics_path}")
        return {
            "metrics_path": metrics_path,
            "metric_count": metric_count,
            "metrics_source": "manual_file",
            "metrics_error": "",
        }

    output_dir = output_dir or os.path.dirname(DEEP_FEEDBACK_METRICS_FILE)
    output_path = os.path.join(output_dir, os.path.basename(DEEP_FEEDBACK_METRICS_FILE))
    if not auto_scrape:
        _assert_feedback_metrics_detail_source(output_path)
        metric_count = len(load_feedback_metrics(output_path))
        if metric_count <= 0:
            raise RuntimeError(f"本地指标文件没有有效数据：{output_path}")
        return {
            "metrics_path": output_path,
            "metric_count": metric_count,
            "metrics_source": "local_file",
            "metrics_error": "",
        }

    from crawler.bilibili_feedback import scrape_bilibili_article_metrics
    result = scrape_bilibili_article_metrics(output_path=output_path)
    metrics_path = result.get("metrics_path") or output_path
    metric_count = int(result.get("metric_count") or 0)
    error = str(result.get("error") or "").strip()
    if error or metric_count <= 0:
        raise RuntimeError(error or f"B站自动抓取未获得有效指标：{metrics_path}")
    if not os.path.exists(metrics_path):
        raise RuntimeError(f"B站自动抓取没有生成指标文件：{metrics_path}")
    _assert_feedback_metrics_detail_source(metrics_path)
    file_metric_count = len(load_feedback_metrics(metrics_path))
    if file_metric_count <= 0:
        raise RuntimeError(f"B站自动抓取结果文件没有有效数据：{metrics_path}")
    return {
        "metrics_path": metrics_path,
        "metric_count": file_metric_count,
        "metrics_source": result.get("source") or "bilibili_article_manager",
        "metrics_error": "",
    }


def _feedback_metric_key(row: Dict) -> tuple:
    return (
        str(row.get("series") or "").strip(),
        str(row.get("episode") or row.get("publish_title") or "").strip(),
    )


def _normalize_feedback_title(text: str) -> str:
    # 标题匹配只保留中英文和数字，忽略空格、冒号、问号等发布侧常见差异。
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", str(text or "")).lower()


def _find_feedback_metric_by_title(candidates: List[str], title_pairs: List[tuple[str, Dict]]) -> Dict:
    normalized_candidates = [_normalize_feedback_title(item) for item in candidates if item]
    for candidate in normalized_candidates:
        for metric_title, row in title_pairs:
            if candidate and metric_title and (candidate in metric_title or metric_title in candidate):
                return row
    best_score = 0.0
    best_row: Dict = {}
    for candidate in normalized_candidates:
        for metric_title, row in title_pairs:
            score = difflib.SequenceMatcher(None, candidate, metric_title).ratio()
            if score > best_score:
                best_score = score
                best_row = row
    return best_row if best_score >= 0.55 else {}


def _episode_feedback_risks(episode: Dict, metrics: Dict, gate: Dict) -> List[str]:
    risks: List[str] = []
    if gate.get("blocked"):
        risks.extend(gate.get("reasons", []))
    actual_seconds = _metric_float(episode.get("actual_seconds"), 0)
    avg_view_seconds = _metric_float_or_none(metrics.get("avg_view_seconds")) if "avg_view_seconds" in metrics else None
    completion_rate = _metric_float_or_none(metrics.get("completion_rate")) if "completion_rate" in metrics else None
    click_rate = _metric_float_or_none(metrics.get("click_rate")) if "click_rate" in metrics else None
    if actual_seconds and avg_view_seconds is not None and avg_view_seconds / actual_seconds < 0.35:
        risks.append("平均观看时长低于总时长35%")
    if completion_rate is not None and completion_rate < 0.35:
        risks.append("完播率偏低")
    if click_rate is not None and click_rate < 0.025:
        risks.append("点击率偏低")
    if episode.get("fallback_used"):
        risks.append("使用保守写稿，可能缺少差异化信息")
    return list(dict.fromkeys(str(item) for item in risks if item))


def build_deep_feedback_report(config: Dict, metrics_path: str = None) -> Dict:
    # 把平台指标和本地生成质量合并成一份机器可读报告，后续 AI 只消费这一份上下文。
    metrics_rows = load_feedback_metrics(metrics_path)
    metrics_by_key = {_feedback_metric_key(row): row for row in metrics_rows}
    metrics_by_title: Dict[str, Dict] = {}
    for row in metrics_rows:
        # B站接口只返回发布标题，不知道本地系列名；这里额外建标题索引，避免真实指标无法匹配回本地主题。
        for title in (row.get("publish_title"), row.get("episode")):
            title = str(title or "").strip()
            if title:
                metrics_by_title[title] = row
    normalized_title_pairs = [
        (_normalize_feedback_title(title), row)
        for title, row in metrics_by_title.items()
        if _normalize_feedback_title(title)
    ]
    videos: List[Dict] = []
    for series in config.get("series", []):
        for episode in series.get("episodes", []):
            episode_title = str(episode.get("title", "")).strip()
            publish_title = str(episode.get("publish_title", "")).strip()
            series_title = str(series.get("title", "")).strip()
            metric = metrics_by_key.get((series_title, episode_title)) or \
                metrics_by_key.get((series_title, publish_title)) or \
                metrics_by_title.get(publish_title) or \
                metrics_by_title.get(episode_title) or \
                _find_feedback_metric_by_title([publish_title, episode_title], normalized_title_pairs) or {}
            if not (episode.get("generated") or episode.get("published") or episode.get("video_path") or metric):
                continue
            gate = assess_publish_gate(series, episode)
            risks = _episode_feedback_risks(episode, metric, gate)
            videos.append({
                "series": series.get("title", ""),
                "episode": episode.get("title", ""),
                "publish_title": episode.get("publish_title", ""),
                "actual_seconds": _metric_float(episode.get("actual_seconds"), 0),
                "estimated_seconds": _metric_float(episode.get("estimated_seconds"), 0),
                "source_count": int(_metric_float(episode.get("source_count"), 0)),
                "quality_block_reason": episode.get("quality_block_reason", ""),
                "publish_gate": gate,
                "metrics": metric,
                "risks": risks,
            })
    summary = {
        "video_count": len(videos),
        "metric_count": len(metrics_rows),
        "blocked_count": sum(1 for item in videos if item["publish_gate"].get("blocked")),
        "low_click_count": sum(1 for item in videos if "点击率偏低" in item["risks"]),
        "low_retention_count": sum(1 for item in videos if "完播率偏低" in item["risks"] or "平均观看时长低于总时长35%" in item["risks"]),
    }
    return {"summary": summary, "videos": videos}


def build_deep_feedback_ai_prompt(report: Dict) -> str:
    # 提示词明确要求输出可执行的 Codex 任务，避免只得到泛泛运营建议。
    compact_rows = []
    # 先把有平台指标或风险的视频交给 AI，避免历史配置里的老视频挤掉真正需要复盘的样本。
    prompt_items = sorted(
        report.get("videos", []),
        key=lambda item: (
            bool(item.get("risks")),
            bool(item.get("metrics")),
            _metric_float(item.get("actual_seconds"), 0),
        ),
        reverse=True,
    )
    for item in prompt_items[:20]:
        metrics = item.get("metrics", {})
        compact_row = {
            "系列": item.get("series", ""),
            "主题": item.get("episode", ""),
            "发布标题": item.get("publish_title", ""),
            "时长": item.get("actual_seconds") or item.get("estimated_seconds"),
            "来源数": item.get("source_count"),
        }
        # 这里只写入 B站详情页实际返回的指标；缺失字段不传给 AI，避免被当成 0 分析。
        metric_labels = [
            ("views", "播放量"),
            ("followers", "涨粉"),
            ("unfollows", "取关"),
            ("likes", "点赞"),
            ("danmaku", "弹幕"),
            ("comments", "评论"),
            ("shares", "分享"),
            ("favorites", "收藏"),
            ("coins", "投币"),
            ("avg_view_seconds", "平均观看秒数"),
            ("completion_rate", "完播率"),
            ("click_rate", "点击率"),
            ("three_second_exit_rate", "3秒跳出率"),
            ("interaction_rate", "互动率"),
            ("play_follower_rate", "播转粉率"),
            ("metric_sections", "指标区块"),
        ]
        for field, label in metric_labels:
            if field in metrics:
                compact_row[label] = metrics[field]
        compact_row["风险"] = item.get("risks", [])
        compact_rows.append(compact_row)
    return (
        "你是 OpenNewsBrief 深度系列增长和工程优化顾问。\n"
        "请根据下面的数据回流报告，生成一份可以直接复制给 Codex 执行的优化建议。\n"
        "要求：\n"
        "1. 优先给代码层面的闭环任务，不要只写运营口号。\n"
        "2. 只分析视频数据里实际出现的字段；缺失的点击率、完播率、平均观看时长不要按 0 解读。\n"
        "3. 每条建议都要说明应修改的模块或函数。\n"
        "4. 输出中文 Markdown。\n\n"
        f"汇总：{json.dumps(report.get('summary', {}), ensure_ascii=False)}\n"
        f"视频数据：{json.dumps(compact_rows, ensure_ascii=False, indent=2)}"
    )


def fallback_deep_feedback_advice(report: Dict) -> str:
    # LLM 不可用时也给出可执行的保底建议，保证数据回流命令不会空跑。
    summary = report.get("summary", {})
    lines = [
        "## 规则生成的优化建议",
        "",
        f"- 当前纳入分析视频 {summary.get('video_count', 0)} 条，已有指标 {summary.get('metric_count', 0)} 条。",
    ]
    if summary.get("blocked_count", 0):
        lines.append("- 先回到生成阶段处理质量风险：超过150秒、来源不足或封面风险的视频不要在待发布或上传阶段拦截。")
    if summary.get("low_click_count", 0):
        lines.append("- 点击率偏低时，优先重做封面首屏和标题承诺一致性。")
    if summary.get("low_retention_count", 0):
        lines.append("- 留存偏低时，把脚本压到650-750字，并单独生成前15秒冷开场。")
    if len(lines) == 3:
        lines.append("- 暂无平台指标，先补齐 B站播放量、点击率、平均观看和完播率。")
    return "\n".join(lines)


def generate_deep_feedback_advice(
        metrics_path: str = None,
        output_dir: str = None,
        use_llm: bool = True,
        auto_scrape: bool = True) -> Dict:
    # 一键闭环入口只接受真实指标；采集失败时让异常直接暴露给 UI，不写误导性的兜底报告。
    output_dir = output_dir or os.path.join(main.ROOT_DIR, "deepContent")
    os.makedirs(output_dir, exist_ok=True)
    metrics_result = collect_deep_feedback_metrics(metrics_path=metrics_path, auto_scrape=auto_scrape, output_dir=output_dir)
    actual_metrics_path = metrics_result.get("metrics_path") or metrics_path
    if not actual_metrics_path or not os.path.exists(actual_metrics_path):
        raise RuntimeError(f"指标文件不存在：{actual_metrics_path or metrics_path}")
    if len(load_feedback_metrics(actual_metrics_path)) <= 0:
        raise RuntimeError(f"指标文件没有有效数据：{actual_metrics_path}")
    report = build_deep_feedback_report(load_config(), metrics_path=actual_metrics_path)
    report["metrics_collection"] = metrics_result
    report_path = os.path.join(output_dir, DEEP_FEEDBACK_REPORT_FILE)
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    prompt = build_deep_feedback_ai_prompt(report)
    if not use_llm:
        raise RuntimeError("数据回流建议已禁止规则兜底，请保持 use_llm=True 并使用真实 AI 分析")
    try:
        ai_advice = call_llm(prompt)
    except Exception as exc:
        # 报告已经基于真实指标写出；建议生成失败时直接报错，不再用规则建议伪装成完整判断。
        raise RuntimeError(f"AI 数据回流建议生成失败：{exc}") from exc

    codex_prompt = (
        "请在 `D:\\myself\\AIContentfactory\\bg\\OpenNewsBrief` 中继续优化深度系列视频生成闭环。\n"
        "先读取 `deepContent\\deep_feedback_report.json`，再根据报告里的点击率、完播率、平均观看时长、来源数和发布质量风险原因做窄范围改动。\n"
        "发布列表和单个/批量发布不要在待发布或上传阶段拦截，超时脚本必须回到专门的优化代理处理。\n"
        "优先顺序：质量风险回修、封面/首屏可读性、脚本时长、前15秒冷开场、数据回流字段。\n"
        "不要修改每日简报功能，新增或修改代码都写具体中文注释，并运行相关 unittest。"
    )
    advice_path = os.path.join(output_dir, DEEP_FEEDBACK_ADVICE_FILE)
    with open(advice_path, "w", encoding="utf-8") as f:
        f.write("# 深度系列自动优化建议\n\n")
        f.write(ai_advice.strip() + "\n\n")
        f.write("## 可复制给 Codex 的执行提示词\n\n")
        f.write("```text\n" + codex_prompt + "\n```\n")
    return {
        "report_path": report_path,
        "advice_path": advice_path,
        "summary": report.get("summary", {}),
        "metrics_path": metrics_result.get("metrics_path") or "",
        "metric_count": metrics_result.get("metric_count") or report.get("summary", {}).get("metric_count", 0),
        "metrics_source": metrics_result.get("metrics_source") or "",
        "metrics_error": metrics_result.get("metrics_error") or "",
    }
