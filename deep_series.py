# -*- coding: utf-8 -*-
import asyncio
import datetime
import json
import os
import re
import subprocess
from typing import Dict, List

import main


CONFIG_PATH = os.path.join(main.ROOT_DIR, "deep_series_config.json")
FEMALE_VOICE = "zh-CN-XiaoxiaoNeural"
MALE_VOICE = "zh-CN-YunxiNeural"
DEEP_TTS_RATE = os.environ.get("OPENNEWSBRIEF_DEEP_TTS_RATE", "+16%")
DEEP_TTS_ENGINE = os.environ.get("OPENNEWSBRIEF_TTS_ENGINE", "chattts").lower()
DEEP_DIALOGUE_PAUSE_SECONDS = 0.45
DEEP_VISUAL_MAX_SECONDS = 4.0
DEEP_SLIDE_DURATIONS_FILE = "slide_durations.json"


DEFAULT_CONFIG = {
    "series": [
        {
            "title": "AI未来三年系列",
            "description": "围绕未来三年 AI 对搜索、软件、记忆、协议、私有化和内容生产的影响做深度讨论。",
            "episodes": [
                {"title": "AI 为什么会替代搜索？", "question": "AI 为什么会替代搜索？"},
                {"title": "为什么 Agent 会重构软件？", "question": "为什么 Agent 会重构软件？"},
                {"title": "AI 为什么需要记忆？", "question": "AI 为什么需要记忆？"},
                {"title": "为什么 MCP 很关键？", "question": "为什么 MCP 很关键？"},
                {"title": "私有化 AI 为什么会爆发？", "question": "私有化 AI 为什么会爆发？"},
                {"title": "AI 内容工厂会出现吗？", "question": "AI 内容工厂会出现吗？"},
            ],
        }
    ]
}


def load_config(path: str = None) -> Dict:
    path = path or CONFIG_PATH
    if not os.path.exists(path):
        save_config(DEFAULT_CONFIG, path)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_config(config: Dict, path: str = None) -> None:
    path = path or CONFIG_PATH
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


def find_series(config: Dict, title: str) -> Dict:
    for series in config.get("series", []):
        if series.get("title") == title:
            return series
    raise ValueError(f"未找到深度系列: {title}")


def find_episode(series: Dict, title: str) -> Dict:
    for episode in series.get("episodes", []):
        if episode.get("title") == title:
            return episode
    raise ValueError(f"未找到深度主题: {title}")


def call_llm(prompt: str, text: str = "") -> str:
    from util.llm import LLmFactory

    content = prompt
    if text:
        content += "\n\n资料如下：\n" + text
    llm = LLmFactory().getDeepseek()
    result = llm.invoke(content)
    return result.content.strip() if hasattr(result, "content") else str(result).strip()


def build_search_keywords(series: Dict, episode: Dict) -> List[str]:
    question = episode.get("question") or episode.get("title", "")
    base_terms = [
        question,
        f"{question} 核心机制 原因",
        f"{question} 用户行为 需求变化",
        f"{question} 商业模式 收入 成本",
        f"{question} 技术限制 瓶颈 幻觉",
        f"{question} 反方观点 失败案例",
        f"{question} 监管风险 合规 版权",
        f"{question} 赢家输家 产业链 冲击",
        f"{question} 竞争格局 Google OpenAI 百度",
        f"{question} case study data report",
        f"{question} controversy debate criticism",
        f"{question} timeline future prediction",
    ]
    return [term for term in base_terms if term.strip()]


def collect_research_sources(series: Dict, episode: Dict, limit_per_keyword: int = 4) -> List[Dict]:
    from crawler import news_crawler

    sources = []
    seen_links = set()
    old_max_hours = news_crawler.MAX_HOURS
    news_crawler.MAX_HOURS = 24 * 365 * 5
    try:
        for keyword in build_search_keywords(series, episode):
            print(f"[深度研究] 搜索资料: {keyword}")
            items, _expired_count, _used_query = news_crawler.collect_news_for_keyword(keyword)
            for item in items[:limit_per_keyword]:
                link = item.get("link", "")
                if not link or link in seen_links:
                    continue
                seen_links.add(link)
                sources.append({
                    "title": item.get("title", ""),
                    "link": link,
                    "content": item.get("content", ""),
                    "date": item.get("date", ""),
                })
    finally:
        news_crawler.MAX_HOURS = old_max_hours
    return sources


def sources_to_markdown(sources: List[Dict]) -> str:
    lines = []
    for index, source in enumerate(sources, 1):
        content = re.sub(r"\s+", " ", source.get("content", "")).strip()
        lines.append(
            f"### {index}. {source.get('title', '')}\n"
            f"- 链接: {source.get('link', '')}\n"
            f"- 日期: {source.get('date', '')}\n"
            f"- 摘要: {content[:1200]}"
        )
    return "\n\n".join(lines)


def generate_research_report(series: Dict, episode: Dict, sources: List[Dict]) -> str:
    prompt = f"""你是深度视频研究员。请基于资料研究《{series.get('title')}》里的这一期：
问题：{episode.get('question') or episode.get('title')}

请做多角度整理加工，不要只摘新闻或只给单一结论。必须输出：
1. 核心结论：一句话说明最值得拍成视频的趋势判断。
2. 正方观点：为什么这个趋势可能成立，列出证据和来源。
3. 反方观点：为什么这个趋势可能被夸大，列出反例、失败案例或资料不足处。
4. 技术角度：底层技术、产品体验、能力边界、技术限制分别是什么。
5. 商业模式：收入、成本、渠道、入口、平台利益会怎样变化。
6. 用户行为：用户为什么会改变习惯，哪些人先变，哪些人不会变。
7. 监管风险：合规、版权、安全、数据、政策和舆论风险。
8. 利益相关方：谁会赢、谁会输、谁会被迫转型。
9. 关键案例和可引用数据：每条都标来源编号。
10. 时间线：过去发生了什么，现在为什么到拐点，未来三年可能怎样。
11. 适合科技纪录片的故事结构：冲突、旧世界、变化原因、受冲击者、未来悬念。

要求：
- 只使用资料中能支撑的信息，不要编造机构、数据或日期。
- 每个重要判断后标注来源序号，例如 [1]。
- 如果资料不足，明确写“资料不足，不能下结论”。
- 对正反双方都要给出最强论据，再做自己的综合判断。
"""
    return call_llm(prompt, sources_to_markdown(sources))


def audit_research(series: Dict, episode: Dict, research_report: str, sources: List[Dict]) -> str:
    source_text = sources_to_markdown(sources)
    roles = [
        ("事实核查员", "检查事实、数据、日期、来源编号是否自洽，指出可能胡编的地方。"),
        ("反方审稿人", "专门找反例、争议、过度推断和标题党风险。"),
        ("结构编辑", "检查故事结构是否像 YouTube 深度视频，有钩子、冲突、递进、反转和结尾。"),
    ]
    results = []
    for role, task in roles:
        prompt = f"""你是{role}。{task}

请审核下面这期研究报告：
系列：{series.get('title')}
主题：{episode.get('title')}

输出格式：
- 通过/不通过
- 主要问题
- 必须修改的建议
"""
        results.append(f"## {role}\n" + call_llm(prompt, research_report + "\n\n原始资料：\n" + source_text))
    return "\n\n".join(results)


def generate_dialogue_script(series: Dict, episode: Dict, research_report: str, audit_report: str) -> str:
    prompt = f"""你是“AI科技纪录片导演 + B站增长策划 + 中文深度视频编剧”。请基于研究报告和审核意见，生成一篇男女问答式中文解说脚本。

系列：{series.get('title')}
主题：{episode.get('title')}
问题：{episode.get('question') or episode.get('title')}

要求：
- 形式必须是“女：...”和“男：...”轮流问答，必要时可少量使用“旁白：...”。
- 整体风格是科技纪录片 / AI产业深度分析，不要像AI自动朗读PPT。
- 前3秒必须是冲突开场，直接制造危机感、未来感或行业变化。
- 必须围绕一个核心观点展开，不要只讲新闻，要讲趋势、赢家、输家和未来三年变化。
- 结构必须是：冲突开场、旧世界是什么、变化为什么开始、谁受到冲击、未来会怎样、结尾留下未来感。
- 必须讨论正反双方，先给正方最强理由，再给反方最强质疑，最后给出有保留的综合判断。
- 至少四个角度展开：技术、商业、用户、风险；如果资料支持，也加入监管、资本、产业链和国际竞争。
- 不能只给单一结论，每个关键判断都要说明“为什么成立”和“哪里可能不成立”。
- 必须明确不同利益相关方：用户、平台、创业公司、传统公司、监管者分别会受到什么影响。
- 必须加入第一人称观点，例如“我最近越来越强烈地感觉...”“我的判断是...”。
- 需要有停顿、留白、强调句和关键转折，避免平铺直叙。
- 禁止“今天我们来聊”，不要写成第一点、第二点、第三点的课程结构。
- 每个可朗读段落都必须以“女：”“男：”或“旁白：”开头。
- 禁止输出“好的，以下是...”、Markdown 标题、**女：** 这类加粗角色名或任何解释性开头。
- 不要输出镜头说明，不要输出 Markdown 标题，只输出可朗读脚本。
- 严禁加入研究报告和审核意见之外的新事实。
- 如果某个数据不确定，用“目前公开资料还不足以证明...”表达。
"""
    raw = call_llm(prompt, f"研究报告：\n{research_report}\n\n审核意见：\n{audit_report}")
    return clean_script_output(raw)


SPEAKER_PATTERN = re.compile(r"^(?:[-*]\s*)?(?:\*\*)?\s*(女主持|男主持|女生|男生|旁白|女|男)\s*[:：]\s*(?:\*\*)?\s*(.*)$")


def strip_inline_markdown(text: str) -> str:
    text = re.sub(r"[*_`]+", "", text or "")
    return re.sub(r"\s+", " ", text).strip()


def normalize_dialogue_line(line: str) -> str:
    line = line.strip()
    match = SPEAKER_PATTERN.match(line)
    if match:
        return f"{match.group(1)}：{strip_inline_markdown(match.group(2))}"
    return strip_inline_markdown(line)


def clean_script_output(text: str) -> str:
    lines = [line.strip() for line in (text or "").splitlines()]
    cleaned = []
    rule_lines = {"---", "***", "___"}
    seen_dialogue = False
    for line in lines:
        if not line:
            if cleaned:
                cleaned.append("")
            continue
        if line in rule_lines or re.match(r"^#{1,6}\s+", line):
            continue
        normalized = normalize_dialogue_line(line)
        is_dialogue = SPEAKER_PATTERN.match(normalized)
        if is_dialogue:
            seen_dialogue = True
            cleaned.append(normalized)
            continue
        if not seen_dialogue:
            continue
        cleaned.append(normalized)
    while cleaned and not cleaned[0]:
        cleaned.pop(0)
    while cleaned and not cleaned[-1]:
        cleaned.pop()
    return "\n".join(cleaned).strip()


def audit_research(series: Dict, episode: Dict, research_report: str, sources: List[Dict]) -> str:
    source_text = sources_to_markdown(sources)
    roles = [
        ("事实核查员", "检查事实、数据、日期、来源编号是否自洽，指出可能编造的地方。"),
        ("反方审稿人", "专门寻找反例、争议、过度推断和标题党风险。"),
        ("结构编辑", "检查故事结构是否适合 YouTube 深度视频，是否有钩子、冲突、递进、反转和结尾。"),
        ("多角度审稿人", "检查研究是否覆盖技术、商业、用户、风险、监管、赢家输家等角度，指出观点单薄和缺少正反论证的地方。"),
    ]
    results = []
    for role, task in roles:
        prompt = f"""你是{role}。{task}

请审核下面这期研究报告：
系列：{series.get('title')}
主题：{episode.get('title')}

请详细输出审稿意见：
- 结论：通过/不通过
- 关键问题：逐条列出，每条说明对应原文位置或来源编号
- 风险等级：高/中/低
- 修改建议：给出可以直接交给编剧修改的具体建议
- 可保留内容：说明哪些内容可以继续使用"""
        results.append(f"## {role}\n" + call_llm(prompt, research_report + "\n\n原始资料：\n" + source_text))
    return "\n\n".join(results)


def generate_script_notes(series: Dict, episode: Dict, research_report: str, audit_report: str, script: str) -> str:
    prompt = f"""你是深度视频主编。请详细说明最终脚本是如何根据研究报告和审稿意见写成的。
系列：{series.get('title')}
主题：{episode.get('title')}

输出格式：
1. 审稿意见采纳清单：逐条说明采纳了哪些事实核查、反方审稿、结构审稿意见。
2. 未采纳意见及原因：如果没有，写“无”。
3. 脚本结构说明：说明开头钩子、冲突、案例、数据、争议、时间线、结尾分别放在哪里。
4. 仍需人工重点审核：列出上线前最应该人工检查的事实、数字、表述风险。"""
    return call_llm(
        prompt,
        f"研究报告：\n{research_report}\n\n审稿意见：\n{audit_report}\n\n最终脚本：\n{script}",
    )


def generate_documentary_package(series: Dict, episode: Dict, research_report: str, audit_report: str, script: str, result: Dict) -> str:
    prompt = f"""你是AI科技纪录片导演和B站增长策划。请把这期深度系列升级成科技纪录片级的制作包。

系列：{series.get('title')}
主题：{episode.get('title')}

请严格输出以下12个部分：
1. 新版标题（10个）：每个12到24个字，单核心、强冲突、一眼理解。
2. 新版封面文案（10个）：每个2到8个字，必须有冲突感。
3. 新版开场脚本（前30秒）：禁止“今天我们来聊”。
4. 视频结构重构方案：使用冲突开场、旧世界、变化原因、受冲击者、未来、结尾未来感。
5. 分镜脚本：每2到4秒一个画面变化。
6. B-roll建议：只给可执行画面，不写空泛词。
7. 运镜建议：写明机位、景别、推拉摇移、节奏和情绪。
8. AI视频提示词：每条包含 documentary style, cinematic, handheld, shallow depth of field, natural lighting。
9. B站完播率优化建议。
10. 人格化表达优化。
11. 情绪曲线设计。
12. Shorts切片方案。

要求：
- 不新增研究报告外的新事实。
- 不做课程PPT结构，不写第一点、第二点、第三点。
- 强调趋势判断、行业冲突和未来三年系列化追更感。
"""
    package = call_llm(
        prompt,
        f"研究报告：\n{research_report}\n\n审稿意见：\n{audit_report}\n\n最终脚本：\n{script}",
    )
    package_path = result.get("documentary_package_path")
    if package_path:
        with open(package_path, "w", encoding="utf-8") as f:
            f.write(package)
    return package


def parse_dialogue_script(script: str) -> List[Dict]:
    script = clean_script_output(script)
    segments = []
    current_speaker = "narrator"
    buffer = []
    speaker_map = {
        "女": "female",
        "女生": "female",
        "女主持": "female",
        "男": "male",
        "男生": "male",
        "男主持": "male",
        "旁白": "narrator",
    }
    for raw_line in script.splitlines():
        line = normalize_dialogue_line(raw_line)
        if not line:
            continue
        match = SPEAKER_PATTERN.match(line)
        if match:
            if buffer:
                segments.append({"speaker": current_speaker, "text": "\n".join(buffer).strip()})
            current_speaker = speaker_map.get(match.group(1), "narrator")
            buffer = [match.group(2).strip()]
        else:
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
        raise ValueError("深度脚本为空，无法生成音频")

    segment_dir = os.path.join(os.path.dirname(output_path), "dialogue_segments")
    os.makedirs(segment_dir, exist_ok=True)
    clear_generated_files(segment_dir, (".mp3", ".wav", ".json", ".txt"))

    audio_segments = []
    segment_paths = []
    for index, segment in enumerate(segments):
        voice = FEMALE_VOICE if segment["speaker"] == "female" else MALE_VOICE
        segment_path = os.path.join(segment_dir, f"{index:03d}_{segment['speaker']}.mp3")
        asyncio.run(_save_tts(segment["text"], segment_path, voice, role=segment["speaker"]))
        segment["audio_path"] = segment_path
        segment["duration"] = get_audio_duration(segment_path)
        segment["role"] = segment["speaker"]
        segment["slide_index"] = index
        audio_segments.append(segment)
        segment_paths.append(segment_path)
        if index < len(segments) - 1:
            silence_path = os.path.join(segment_dir, f"{index:03d}_pause.mp3")
            create_silence_audio(silence_path, DEEP_DIALOGUE_PAUSE_SECONDS)
            segment["duration"] += DEEP_DIALOGUE_PAUSE_SECONDS
            segment_paths.append(silence_path)

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


def _wrap_text(text: str, max_chars: int, max_lines: int) -> List[str]:
    return main.wrap_display_lines(text, max_chars, max_lines)


def create_text_card(title: str, subtitle: str, body: str, output_path: str, accent: str = "#007AFF") -> str:
    from PIL import Image, ImageDraw

    size = 1080
    image = Image.new("RGB", (size, size), "#101014")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((72, 68, 1008, 1012), radius=42, fill="#1C1C1E")
    draw.rounded_rectangle((104, 104, 270, 142), radius=19, fill=accent)
    draw.text((124, 111), "AI FUTURE", font=_font(21, True), fill="#FFFFFF")
    draw.line((104, 812, 976, 812), fill="#38383A", width=2)

    y = 188
    for line in _wrap_text(title, 17, 2):
        draw.text((104, y), line, font=_font(42, True), fill="#F5F5F7")
        y += 56
    for line in _wrap_text(subtitle, 25, 2):
        draw.text((104, y + 6), line, font=_font(24), fill="#A1A1AA")
        y += 34

    y = max(366, y + 56)
    body_lines = _wrap_text(body, 14, 6)
    for line in body_lines:
        draw.text((104, y), line, font=_font(56, True), fill="#FFFFFF")
        y += 78

    draw.rounded_rectangle((104, 858, 976, 920), radius=24, fill="#2C2C2E")
    draw.text((128, 874), "OpenNewsBrief 深度系列", font=_font(26, True), fill="#D1D1D6")
    draw.text((104, 946), "documentary style / trend analysis", font=_font(24), fill="#8E8E93")
    image.save(output_path)
    return output_path


def _load_timing_segments(audio_path: str) -> List[Dict]:
    timing_path = audio_path + ".timing.json"
    if not os.path.exists(timing_path):
        return []
    with open(timing_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("segments", [])


def _chunk_text(text: str, max_chars: int = 24) -> List[str]:
    clean = re.sub(r"\s+", " ", text or "").strip()
    if not clean:
        return []
    parts = [part.strip() for part in re.split(r"(?<=[。！？!?；;])", clean) if part.strip()]
    chunks = []
    for part in parts or [clean]:
        while len(part) > max_chars:
            chunks.append(part[:max_chars])
            part = part[max_chars:]
        if part:
            chunks.append(part)
    return chunks or [clean]


def _split_visual_text(text: str, target_count: int) -> List[str]:
    target_count = max(1, target_count)
    chunks = _chunk_text(text)
    if not chunks:
        return [""]
    if len(chunks) <= target_count:
        while len(chunks) < target_count:
            chunks.append(chunks[-1])
        return chunks

    grouped = []
    index = 0
    for group_index in range(target_count):
        remaining_groups = target_count - group_index
        remaining_chunks = len(chunks) - index
        take = max(1, int(round(remaining_chunks / remaining_groups)))
        grouped.append("".join(chunks[index:index + take]))
        index += take
    return grouped


def build_deep_visual_slide_plan(script_path: str, audio_path: str) -> List[Dict]:
    timing_segments = _load_timing_segments(audio_path)
    if timing_segments:
        source_segments = [
            {
                "speaker": item.get("role") or item.get("speaker") or "narrator",
                "text": item.get("text", ""),
                "duration": float(item.get("duration", 0.0)),
            }
            for item in timing_segments
        ]
    else:
        with open(script_path, "r", encoding="utf-8") as f:
            source_segments = [
                {**item, "duration": DEEP_VISUAL_MAX_SECONDS}
                for item in parse_dialogue_script(f.read())
            ]

    slide_plan = []
    for segment in source_segments:
        duration = max(float(segment.get("duration", 0.0)), 0.1)
        visual_count = max(1, int((duration + DEEP_VISUAL_MAX_SECONDS - 0.001) / DEEP_VISUAL_MAX_SECONDS))
        chunks = _split_visual_text(segment.get("text", ""), visual_count)
        per_slide = duration / len(chunks)
        for chunk in chunks:
            slide_plan.append({
                "speaker": segment.get("speaker", "narrator"),
                "text": chunk,
                "duration": per_slide,
            })
    return slide_plan


def write_deep_slide_durations(slide_dir: str, durations: List[float]) -> str:
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
    accents = {"female": "#FF2D55", "male": "#007AFF", "narrator": "#34C759"}
    labels = {"female": "女主持", "male": "男主持", "narrator": "旁白"}
    slide_plan = build_deep_visual_slide_plan(script_path, audio_path)
    for index, segment in enumerate(slide_plan):
        image_path = os.path.join(slide_dir, f"slide_{index:03d}.png")
        create_text_card(
            episode.get("title", ""),
            f"{series.get('title', '')} · {labels.get(segment['speaker'], '旁白')}",
            segment["text"],
            image_path,
            accent=accents.get(segment["speaker"], "#007AFF"),
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

    sources = collect_research_sources(series, episode)
    research = generate_research_report(series, episode, sources)
    print("[多智能体] 研究员：研究报告已完成")
    audit = audit_research(series, episode, research, sources)
    print("[多智能体] 审核组：事实核查/反方审稿/结构审稿已完成")
    print("\n========== 审稿详细意见 ==========\n")
    print(audit)
    script = generate_dialogue_script(series, episode, research, audit)
    print("[多智能体] 编剧：脚本已完成")
    script_notes = generate_script_notes(series, episode, research, audit, script)
    print("\n========== 写稿详细意见 ==========\n")
    print(script_notes)
    documentary_package = generate_documentary_package(series, episode, research, audit, script, result)

    with open(result["research_path"], "w", encoding="utf-8") as f:
        f.write("# 研究报告\n\n")
        f.write(research)
        f.write("\n\n# 原始资料\n\n")
        f.write(sources_to_markdown(sources))
    with open(result["audit_path"], "w", encoding="utf-8") as f:
        f.write(audit)
    with open(result["script_path"], "w", encoding="utf-8") as f:
        f.write(script)
    with open(result["script_notes_path"], "w", encoding="utf-8") as f:
        f.write(script_notes)

    log_path = os.path.join(output_dir, "agent_interaction.log")
    with open(log_path, "w", encoding="utf-8") as f:
        f.write("多智能体交互日志\n")
        f.write("=" * 56 + "\n")
        f.write("[研究员]\n" + research + "\n\n")
        f.write("[审核组]\n" + audit + "\n\n")
        f.write("[写稿说明]\n" + script_notes + "\n\n")
        f.write("[纪录片制作包]\n" + documentary_package + "\n\n")
        f.write("[编剧]\n" + script + "\n")
    result["agent_log_path"] = log_path
    return result


def generate_video_from_script(series: Dict, episode: Dict, result: Dict) -> Dict:
    today = datetime.date.today().strftime("%Y-%m-%d")
    script_path = result.get("script_path") or episode.get("script_path", "")
    if not script_path or not os.path.exists(script_path):
        raise ValueError("未找到脚本，请先完成调研和写稿")
    output_dir = os.path.dirname(script_path)
    audio_path = os.path.join(output_dir, "dialogue.mp3")
    audio_path = convert_dialogue_to_audio(script_path, audio_path)
    image_paths = create_deep_slide_images(series, episode, script_path, audio_path)
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
    for key in ("research_path", "audit_path", "script_path", "script_notes_path", "documentary_package_path", "audio_path", "video_path", "publish_assets_path", "cover_path"):
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


def normalize_publish_title(title: str, episode_title: str) -> str:
    clean = re.sub(r"\s+", "", title or "").strip(" -_｜|:：")
    clean = re.sub(r"^AI未来三年系列[:：]?", "", clean)
    if not clean:
        clean = re.sub(r"\s+", "", episode_title or "AI深度分析")
    if len(clean) <= 24:
        return clean
    for sep in ("？", "?", "！", "!", "：", ":", "，", ",", "。"):
        first = clean.split(sep)[0].strip()
        if 8 <= len(first) <= 24:
            return first
    return clean[:24]


def normalize_cover_text(text: str, title: str) -> str:
    clean = re.sub(r"\s+", "", text or "").strip(" -_｜|:：")
    if not clean:
        clean = title
    return clean[:8] or "趋势变了"


def create_deep_cover_image(series: Dict, episode: Dict, assets: Dict, output_dir: str) -> str:
    from PIL import Image, ImageDraw

    output_path = os.path.join(output_dir, "cover.png")
    image = Image.new("RGB", (1080, 1080), "#101014")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((64, 86, 492, 620), radius=34, fill="#F5F5F7")
    draw.rounded_rectangle((102, 142, 450, 206), radius=24, fill="#FFFFFF")
    draw.text((132, 160), "Search", font=_font(28, True), fill="#1D1D1F")
    draw.line((118, 274, 438, 274), fill="#C7C7CC", width=5)
    draw.line((118, 338, 390, 338), fill="#D1D1D6", width=5)
    draw.line((118, 402, 424, 402), fill="#D1D1D6", width=5)

    draw.rounded_rectangle((588, 86, 1016, 620), radius=34, fill="#1C1C1E")
    draw.rounded_rectangle((632, 150, 958, 250), radius=28, fill="#2C2C2E")
    draw.text((662, 180), "AI Answer", font=_font(28, True), fill="#F5F5F7")
    draw.rounded_rectangle((632, 314, 958, 420), radius=28, fill="#007AFF")
    draw.text((662, 344), "Direct", font=_font(34, True), fill="#FFFFFF")

    draw.text((84, 704), normalize_cover_text(assets.get("cover_text", ""), assets.get("title", "")), font=_font(86, True), fill="#FFFFFF")
    draw.text((84, 824), assets.get("title", episode.get("title", ""))[:24], font=_font(36, True), fill="#D1D1D6")
    draw.rounded_rectangle((84, 930, 390, 980), radius=25, fill="#FF453A")
    draw.text((112, 942), series.get("title", "AI未来三年系列")[:12], font=_font(24, True), fill="#FFFFFF")
    image.save(output_path)
    return output_path


def generate_publish_assets(series: Dict, episode: Dict, result: Dict) -> Dict:
    script_text = read_text_if_exists(result.get("script_path", ""))
    research_text = read_text_if_exists(result.get("research_path", ""), limit=3000)
    prompt = f"""请为即将发布到 B 站的科技纪录片/AI产业深度分析视频生成发布素材。

系列：{series.get('title')}
主题：{episode.get('title')}

要求：
- title 必须 12到24个字，单核心、强冲突、一眼理解，不要把多个主题塞进一个标题。
- cover_text 是封面文案，只能2到8个字，高对比、强情绪、有冲突。
- cover_prompt 描述封面构图，例如左侧旧入口、右侧AI入口、中间冲突大字。
- 标题和封面都要符合“AI未来三年系列”的统一风格。

请只输出 JSON，不要 Markdown，不要解释。格式：
{{
  "title": "12到24个字的视频标题",
  "desc": "适合B站的视频简介，说明本期看点",
  "tags": "用英文逗号分隔的标签，最多8个",
  "cover_text": "2到8个字的封面文案",
  "cover_prompt": "封面构图和视觉冲突说明",
  "title_options": ["标题1", "标题2"],
  "cover_options": ["封面文案1", "封面文案2"]
}}
"""
    raw = call_llm(prompt, f"脚本：\n{script_text}\n\n研究：\n{research_text}")
    assets = parse_json_object(raw)
    if not assets:
        assets = {
            "title": episode.get("title", "深度视频"),
            "desc": f"《{series.get('title', '')}》深度系列，本期主题：{episode.get('title', '')}。",
            "tags": "人工智能,AI,科技,深度视频",
            "cover_text": "趋势变了",
            "cover_prompt": "深色科技纪录片封面，中间冲突大字，左右对比旧入口和AI入口。",
        }
    assets["title"] = normalize_publish_title(str(assets.get("title") or ""), episode.get("title", "深度视频"))
    assets["desc"] = str(assets.get("desc") or "").strip()
    assets["tags"] = str(assets.get("tags") or "人工智能,AI,科技,深度视频").strip()
    assets["cover_text"] = normalize_cover_text(str(assets.get("cover_text") or ""), assets["title"])
    assets["cover_prompt"] = str(assets.get("cover_prompt") or "深色科技纪录片封面，中间冲突大字，左右对比旧入口和AI入口。").strip()
    if not isinstance(assets.get("title_options"), list):
        assets["title_options"] = [assets["title"]]
    if not isinstance(assets.get("cover_options"), list):
        assets["cover_options"] = [assets["cover_text"]]

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
