# -*- coding: utf-8 -*-
import asyncio
import datetime
import json
import math
import os
import re
import subprocess
from typing import Dict, List, Optional

import main


CONFIG_PATH = os.path.join(main.ROOT_DIR, "deep_series_config.json")
FEMALE_VOICE = "zh-CN-XiaoxiaoNeural"
MALE_VOICE = "zh-CN-YunxiNeural"
DEEP_TTS_RATE = os.environ.get("OPENNEWSBRIEF_DEEP_TTS_RATE", "+16%")
DEEP_TTS_ENGINE = os.environ.get("OPENNEWSBRIEF_TTS_ENGINE", "chattts").lower()
DEEP_DIALOGUE_PAUSE_SECONDS = 0.45
DEEP_FINAL_SILENCE_SECONDS = 1.0
DEEP_VISUAL_MAX_SECONDS = 4.0
DEEP_SLIDE_DURATIONS_FILE = "slide_durations.json"
DEEP_PUBLISH_TITLE_MAX_CHARS = 18
DEEP_COVER_TEXT_MAX_CHARS = 10


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


def call_llm(prompt: str, text: str = "") -> str:
    from util.llm import LLmFactory

    content = prompt
    if text:
        content += "\n\n资料：\n" + text
    llm = LLmFactory().getDeepseek()
    result = llm.invoke(content)
    return _llm_content_to_text(result.content if hasattr(result, "content") else result)


def build_search_keywords(series: Dict, episode: Dict) -> List[str]:
    question = _episode_question(episode)
    base_terms = [
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
    return [term for term in base_terms if term.strip()]


def collect_research_sources(series: Dict, episode: Dict, limit_per_keyword: int = 4) -> List[Dict]:
    from crawler import news_crawler

    sources: List[Dict] = []
    seen_links = set()
    old_max_hours = news_crawler.MAX_HOURS
    news_crawler.MAX_HOURS = 24 * 365 * 5
    try:
        for keyword in build_search_keywords(series, episode):
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


def generate_dialogue_script(series: Dict, episode: Dict, research_report: str, audit_report: str) -> str:
    # 深度系列的视频脚本不走 PPT 纯文字，而是按对话来写。
    prompt = (
        "你是纪录片口播脚本作者。\n"
        f"系列：{series.get('title', '')}\n"
        f"主题：{episode.get('title', '')}\n"
        f"问题：{_episode_question(episode)}\n\n"
        "请写一份适合深度视频的对话脚本。\n"
        "要求：\n"
        "1. 只有主持人对话和必要旁白，不要写成 PPT 纯文字。\n"
        "2. 全片目标 90-120秒，每行控制在 1 到 2 个短句，避免拖成长段。\n"
        "3. 前3秒第一句必须直接给出反常识结论，不要铺垫，不要使用“想象一下”，不要使用“今天我们探讨”。\n"
        "4. 前30秒必须交付核心答案框架：先说结论，再说为什么重要，再给出后面要展开的 2 到 3 个答案点。\n"
        "5. 前三行分别承担：结论、追问、答案路线图，让观众在 30 秒前知道继续看的价值。\n"
        "6. 每隔一段留一个悬念，方便观众继续看下去。\n"
        "7. 结尾要落到一个站得住的问题。\n"
        "8. 每一行使用“女：/男：/旁白：”这种格式。\n"
    )
    raw = call_llm(prompt, f"研究报告：\n{research_report}\n\n审校意见：\n{audit_report}")
    return clean_script_output(raw)


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
        return "narrator"
    if any(token in clean for token in ("女", "女士")) and "男" not in clean:
        return "female"
    if any(token in clean for token in ("男", "先生")) and "女" not in clean:
        return "male"
    if any(token in clean for token in ("主持", "主持人")):
        return "narrator"
    return "narrator"


def normalize_dialogue_line(line: str) -> str:
    # 先去掉粗体、斜体等标记，再统一成“角色：内容”的格式。
    line = strip_inline_markdown(line).strip()
    match = SPEAKER_LINE_RE.match(line)
    if match:
        speaker = match.group("label").strip()
        body = match.group("body").strip()
        if speaker and body:
            return f"{speaker}：{body}"
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
    return "\n".join(cleaned).strip()


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

    segment_dir = os.path.join(os.path.dirname(output_path), "dialogue_segments")
    os.makedirs(segment_dir, exist_ok=True)
    clear_generated_files(segment_dir, (".mp3", ".wav", ".json", ".txt"))

    audio_segments = []
    segment_paths = []
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
    # 先用自然语义短语拆卡，不够时再按字数切，保证视觉节奏跟得上音频。
    target_count = max(1, target_count)
    units = _split_text_units(text)
    if len(units) < target_count:
        return _split_text_evenly(text, target_count)

    groups = [[] for _ in range(target_count)]
    for index, unit in enumerate(units):
        groups[min(index, target_count - 1)].append(unit)
    return ["".join(group).strip() for group in groups if "".join(group).strip()]


def _split_visual_segment(segment: Dict) -> List[Dict]:
    # 音频段超过视觉上限时，拆成多张短卡，但总时长保持不变。
    duration = max(float(segment.get("duration", 0.0)), 0.1)
    speaker = segment.get("speaker", "narrator")
    text = re.sub(r"\s+", " ", segment.get("text", "")).strip()
    if duration <= DEEP_VISUAL_MAX_SECONDS:
        return [{"speaker": speaker, "text": text, "duration": duration}]

    target_count = int(math.ceil(duration / DEEP_VISUAL_MAX_SECONDS))
    texts = _split_text_for_visual_cards(text, target_count)
    if not texts:
        texts = [text]
    per_duration = duration / len(texts)
    result = []
    elapsed = 0.0
    for index, item_text in enumerate(texts):
        item_duration = per_duration
        if index == len(texts) - 1:
            item_duration = duration - elapsed
        elapsed += item_duration
        result.append({"speaker": speaker, "text": item_text, "duration": item_duration})
    return result


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
                    "speaker": item.get("role") or item.get("speaker") or "narrator",
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
                            "speaker": item.get("speaker", "narrator"),
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
        "narrator": "#A6B2AA", # 中性灰绿，给旁白用更克制。
    }
    labels = {"female": "女主持", "male": "男主持", "narrator": "旁白"}
    slide_plan = build_deep_visual_slide_plan(script_path, audio_path)
    if not slide_plan:
        slide_plan = [
            {
                "speaker": "narrator",
                "text": episode.get("title", "") or series.get("title", ""),
                "duration": DEEP_VISUAL_MAX_SECONDS,
            }
        ]
    total = len(slide_plan)
    for index, segment in enumerate(slide_plan):
        image_path = os.path.join(slide_dir, f"slide_{index:03d}.png")
        subtitle = f"{series.get('title', '')} · {labels.get(segment['speaker'], '旁白')}"
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
        "audio_path": "",
        "video_path": "",
    }

    print("[深度系列] 开始检索资料", flush=True)
    sources = collect_research_sources(series, episode)
    print("[深度系列] 开始生成研究报告", flush=True)
    research = generate_research_report(series, episode, sources)
    print("\n========== 研究报告 ==========\n", flush=True)
    print(research, flush=True)
    audit = audit_research(series, episode, research, sources)
    print("\n========== 审校结果 ==========\n", flush=True)
    print(audit, flush=True)
    script = generate_dialogue_script(series, episode, research, audit)
    print("\n========== 对话脚本 ==========\n", flush=True)
    print(script, flush=True)
    script_notes = generate_script_notes(series, episode, research, audit, script)
    print("\n========== 脚本备注 ==========\n", flush=True)
    print(script_notes, flush=True)
    documentary_package = generate_documentary_package(series, episode, research, audit, script, result)

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
    print("[深度视频] 开始生成画面卡片", flush=True)
    image_paths = create_deep_slide_images(series, episode, script_path, audio_path)
    print("[深度视频] 开始合成视频", flush=True)
    result["audio_path"] = audio_path
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
        "audio_path",
        "video_path",
        "publish_assets_path",
        "cover_path",
        "publish_title",
        "publish_desc",
        "publish_tags",
    ):
        if result.get(key):
            episode[key] = result[key]
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


def create_deep_cover_image(series: Dict, episode: Dict, assets: Dict, output_dir: str) -> str:
    # 封面保持简洁：左侧主标题，右侧信息块，整体更接近 iOS 风格页面。
    from PIL import Image, ImageDraw

    output_path = os.path.join(output_dir, "cover.png")
    image = Image.new("RGB", (1080, 1080), "#F2F2F7")
    draw = ImageDraw.Draw(image)

    draw.rounded_rectangle((64, 72, 1016, 1008), radius=48, fill="#FFFFFF")
    draw.rounded_rectangle((96, 128, 168, 952), radius=24, fill="#007AFF")
    draw.rounded_rectangle((618, 128, 968, 384), radius=32, fill="#F5F5F7")
    draw.rounded_rectangle((618, 420, 968, 548), radius=32, fill="#1D1D1F")

    draw.text((220, 150), "OpenNewsBrief", font=_font(28, True), fill="#007AFF")
    draw.text((220, 230), normalize_cover_text(assets.get("cover_text", ""), assets.get("title", "")), font=_font(88, True), fill="#1D1D1F")
    draw.text((220, 356), assets.get("title", episode.get("title", ""))[:24], font=_font(38, True), fill="#6E6E73")
    draw.text((646, 166), "AI Answer", font=_font(28, True), fill="#1D1D1F")
    draw.text((646, 456), "Deep Series", font=_font(34, True), fill="#FFFFFF")
    draw.text((220, 940), series.get("title", "AI未来三年系列")[:18], font=_font(26, True), fill="#8E8E93")
    image.save(output_path)
    return output_path


def generate_publish_assets(series: Dict, episode: Dict, result: Dict) -> Dict:
    # 发布信息只生成一次，后面发视频和发文案都直接复用。
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

    assets["title"] = normalize_publish_title(str(assets.get("title") or ""), episode.get("title", "深度视频"), series.get("title", ""))
    assets["comment_question"] = normalize_comment_question(str(assets.get("comment_question") or ""))
    assets["desc"] = append_comment_question(str(assets.get("desc") or ""), assets["comment_question"])
    assets["tags"] = str(assets.get("tags") or "AI,深度内容,纪录片,口播").strip()
    assets["cover_text"] = normalize_cover_text(str(assets.get("cover_text") or ""), assets["title"])
    assets["cover_prompt"] = str(assets.get("cover_prompt") or "iOS 风格深度系列封面，简洁，清晰，白底，高对比，科技感").strip()

    if not isinstance(assets.get("title_options"), list):
        assets["title_options"] = [assets["title"]]
    assets["title_options"] = [
        normalize_publish_title(str(item), episode.get("title", "深度视频"), series.get("title", ""))
        for item in assets["title_options"][:3]
    ] or [assets["title"]]

    if not isinstance(assets.get("cover_options"), list):
        assets["cover_options"] = [assets["cover_text"]]
    assets["cover_options"] = [
        normalize_cover_text(str(item), assets["title"])
        for item in assets["cover_options"][:3]
    ] or [assets["cover_text"]]

    output_dir = os.path.dirname(os.path.abspath(result.get("video_path") or result.get("script_path") or CONFIG_PATH))
    os.makedirs(output_dir, exist_ok=True)
    assets["cover_path"] = create_deep_cover_image(series, episode, assets, output_dir)
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
    for key in ("research_path", "audit_path", "script_path", "script_notes_path", "documentary_package_path", "agent_log_path"):
        if result.get(key):
            episode[key] = result[key]
    episode["audio_path"] = ""
    episode["video_path"] = ""
    episode["published"] = False
    episode["published_at"] = ""
    episode["review_ready"] = True
    save_config(config)
    return result


def generate_episode_video_by_titles(series_title: str, episode_title: str) -> Dict:
    config = load_config()
    series = find_series(config, series_title)
    episode = find_episode(series, episode_title)
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
