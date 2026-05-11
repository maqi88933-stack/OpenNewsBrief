# -*- coding: utf-8 -*-
import asyncio
import datetime
import json
import os
import re
from typing import Dict, List

import main


CONFIG_PATH = os.path.join(main.ROOT_DIR, "deep_series_config.json")
FEMALE_VOICE = "zh-CN-XiaoxiaoNeural"
MALE_VOICE = "zh-CN-YunxiNeural"
DEEP_TTS_RATE = os.environ.get("OPENNEWSBRIEF_DEEP_TTS_RATE", "+16%")
DEEP_TTS_ENGINE = os.environ.get("OPENNEWSBRIEF_TTS_ENGINE", "chattts").lower()


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


def load_config(path: str = CONFIG_PATH) -> Dict:
    if not os.path.exists(path):
        save_config(DEFAULT_CONFIG, path)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_config(config: Dict, path: str = CONFIG_PATH) -> None:
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
        f"{question} case study",
        f"{question} data report",
        f"{question} controversy debate",
        f"{question} timeline",
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

必须输出：
1. 核心结论
2. 关键案例
3. 可引用数据
4. 主要观点
5. 争议与反方观点
6. 时间线
7. 适合 YouTube 深度视频的故事结构

要求：
- 只使用资料中能支撑的信息，不要编造机构、数据或日期。
- 每个重要判断后标注来源序号，例如 [1]。
- 如果资料不足，明确写“资料不足，不能下结论”。
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
    prompt = f"""你是 YouTube 深度视频编剧。请基于研究报告和审核意见，生成一篇男女问答式中文解说脚本。

系列：{series.get('title')}
主题：{episode.get('title')}
问题：{episode.get('question') or episode.get('title')}

要求：
- 形式必须是“女：...”和“男：...”轮流问答，必要时可少量使用“旁白：...”。
- 开头 20 秒必须有强钩子。
- 结构要有：问题抛出、常识反转、案例、数据、争议、时间线、未来三年推演、结尾总结。
- 不要输出镜头说明，不要输出 Markdown 标题，只输出可朗读脚本。
- 严禁加入研究报告和审核意见之外的新事实。
- 如果某个数据不确定，用“目前公开资料还不足以证明...”表达。
"""
    raw = call_llm(prompt, f"研究报告：\n{research_report}\n\n审核意见：\n{audit_report}")
    return clean_script_output(raw)


def clean_script_output(text: str) -> str:
    lines = [line.strip() for line in (text or "").splitlines()]
    cleaned = []
    rule_lines = {"---", "***", "___"}
    for line in lines:
        if not line:
            if cleaned:
                cleaned.append("")
            continue
        if line in rule_lines:
            continue
        if not cleaned:
            if line.startswith(("好的，作为", "好的,作为", "下面是", "以下是", "根据你提供", "根据您提供")):
                continue
            if "已基于" in line and "生成" in line and "脚本" in line:
                continue
        cleaned.append(line)
    return "\n".join(cleaned).strip()


def audit_research(series: Dict, episode: Dict, research_report: str, sources: List[Dict]) -> str:
    source_text = sources_to_markdown(sources)
    roles = [
        ("事实核查员", "检查事实、数据、日期、来源编号是否自洽，指出可能编造的地方。"),
        ("反方审稿人", "专门寻找反例、争议、过度推断和标题党风险。"),
        ("结构编辑", "检查故事结构是否适合 YouTube 深度视频，是否有钩子、冲突、递进、反转和结尾。"),
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


def parse_dialogue_script(script: str) -> List[Dict]:
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
        line = raw_line.strip()
        if not line:
            continue
        match = re.match(r"^(女主持|男主持|女生|男生|旁白|女|男)[:：]\s*(.+)$", line)
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


def convert_dialogue_to_audio(script_path: str, output_path: str) -> str:
    from audioContent.news_to_audio import concat_audio_files, get_audio_duration, write_timing_file

    with open(script_path, "r", encoding="utf-8") as f:
        segments = parse_dialogue_script(f.read())
    if not segments:
        raise ValueError("深度脚本为空，无法生成音频")

    segment_dir = os.path.join(os.path.dirname(output_path), "dialogue_segments")
    os.makedirs(segment_dir, exist_ok=True)

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

    concat_audio_files(segment_paths, output_path)
    write_timing_file(output_path, audio_segments)
    return output_path


def _font(size: int, bold: bool = False):
    return main.load_cover_font(size, bold=bold)


def _wrap_text(text: str, max_chars: int, max_lines: int) -> List[str]:
    return main.wrap_display_lines(text, max_chars, max_lines)


def create_text_card(title: str, subtitle: str, body: str, output_path: str, accent: str = "#007AFF") -> str:
    from PIL import Image, ImageDraw

    size = 1080
    image = Image.new("RGB", (size, size), "#F5F5F7")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((64, 72, 1016, 1008), radius=58, fill="#FFFFFF")
    draw.rounded_rectangle((118, 128, 222, 164), radius=18, fill=accent)

    draw.text((118, 214), title, font=_font(48, True), fill="#1D1D1F")
    draw.text((118, 282), subtitle, font=_font(27), fill="#6E6E73")

    y = 390
    for line in _wrap_text(body, 21, 8):
        draw.text((118, y), line, font=_font(42, True), fill="#1D1D1F")
        y += 62

    draw.rounded_rectangle((118, 910, 962, 946), radius=18, fill="#EAF3FF")
    draw.text((118, 958), "OpenNewsBrief 深度系列", font=_font(26), fill="#8E8E93")
    image.save(output_path)
    return output_path


def create_deep_slide_images(series: Dict, episode: Dict, script_path: str, audio_path: str) -> List[str]:
    with open(script_path, "r", encoding="utf-8") as f:
        segments = parse_dialogue_script(f.read())
    slide_dir = os.path.join(os.path.dirname(audio_path), "deep_slides")
    os.makedirs(slide_dir, exist_ok=True)

    image_paths = []
    accents = {"female": "#FF2D55", "male": "#007AFF", "narrator": "#34C759"}
    labels = {"female": "女主持", "male": "男主持", "narrator": "旁白"}
    for index, segment in enumerate(segments):
        image_path = os.path.join(slide_dir, f"slide_{index:03d}.png")
        create_text_card(
            episode.get("title", ""),
            f"{series.get('title', '')} · {labels.get(segment['speaker'], '旁白')}",
            segment["text"],
            image_path,
            accent=accents.get(segment["speaker"], "#007AFF"),
        )
        image_paths.append(image_path)
    return image_paths


def safe_filename(text: str) -> str:
    clean = re.sub(r'[\\/:*?"<>|]', "", text or "").strip()
    return clean.replace(" ", "_") or "deep_episode"


def step_video(audio_path: str, video_title: str, image_paths: List[str]) -> str:
    from video.Audio2Video import create_video

    output_path = os.path.join(os.path.dirname(audio_path), f"{safe_filename(video_title)}.mp4")
    slide_durations = main.load_slide_durations_from_timing(audio_path, len(image_paths))
    create_video(audio_path, image_paths[0], output_path, image_paths=image_paths, slide_durations=slide_durations)
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
    for key in ("research_path", "audit_path", "script_path", "script_notes_path", "audio_path", "video_path", "publish_assets_path"):
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


def generate_publish_assets(series: Dict, episode: Dict, result: Dict) -> Dict:
    script_text = read_text_if_exists(result.get("script_path", ""))
    research_text = read_text_if_exists(result.get("research_path", ""), limit=3000)
    prompt = f"""请为即将发布到 B 站的深度视频生成发布素材。

系列：{series.get('title')}
主题：{episode.get('title')}

请只输出 JSON，不要 Markdown，不要解释。格式：
{{
  "title": "不超过80字的视频标题",
  "desc": "适合B站的视频简介，说明本期看点",
  "tags": "用英文逗号分隔的标签，最多8个"
}}
"""
    raw = call_llm(prompt, f"脚本：\n{script_text}\n\n研究：\n{research_text}")
    assets = parse_json_object(raw)
    if not assets:
        assets = {
            "title": episode.get("title", "深度视频"),
            "desc": f"《{series.get('title', '')}》深度系列，本期主题：{episode.get('title', '')}。",
            "tags": "人工智能,AI,科技,深度视频",
        }
    assets["title"] = str(assets.get("title") or episode.get("title", "深度视频")).strip()
    assets["desc"] = str(assets.get("desc") or "").strip()
    assets["tags"] = str(assets.get("tags") or "人工智能,AI,科技,深度视频").strip()

    output_dir = os.path.dirname(os.path.abspath(result.get("video_path") or result.get("script_path") or CONFIG_PATH))
    os.makedirs(output_dir, exist_ok=True)
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
    episode["generated"] = False
    episode["generated_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for key in ("research_path", "audit_path", "script_path", "script_notes_path", "agent_log_path"):
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
    mark_episode_generated(config, series_title, episode_title, result)
    save_config(config)
    return result
