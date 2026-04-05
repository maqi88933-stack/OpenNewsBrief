# -*- coding: utf-8 -*-
"""
AI内容工厂 - 主流水线程序
流程：新闻爬取 → 内容处理/简报生成 → 音频合成 → 播放
"""

import os
import sys
import datetime
import subprocess

# 将工程根目录加入路径
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT_DIR)

# ─────────────────────────────────────────────
# 全局配置（单一来源）
# ─────────────────────────────────────────────
# 多主题配置数组：每个主题包含 title（标题）、theme（内容判定主题）、keywords（爬虫关键词）
TOPICS = [
    {
        "title": "AI 每日简报",
        "theme": "AI大模型、智能体、最新的科技前沿动态",
        "keywords": [
            # 国际头部开发商
            "OpenAI", "Anthropic", "Google", "x-ai",
            "GPT", "Claude", "Gemini", "Mistral", "Grok", "Claude Code", "Codex", "antigravity", "stitch google",
            "OpencClaw",
            # 国内主流大模型开发商及其模型名称
            "通义千问",
            "腾讯", "混元大模型",
            "字节跳动", "豆包",
            "智谱AI", "GLM",
            "月之暗面", "Kimi",
            "DeepSeek",
            "MiniMax",
            "昆仑万维", "天工大模型",
            "阶跃星辰",
            "MiniCPM",
            # 芯片及计算硬件开发商
            "Nvidia", "AMD", "Intel", "英伟达", "英特尔",
            # 智能体领域
            "AI Agent", "智能体", "AutoGPT",
        ],
        "language": "zh-CN"
    },
    # {
    #     "title": "工程机器 破碎机 每日简讯",
    #     "theme": "工程机械、破碎机、矿山设备、建筑机械行业动态",
    #     "keywords": [
    #         "破碎机", "颚式破碎机", "圆锥破碎机", "反击式破碎机",
    #         "挖掘机", "装载机", "工程机械",
    #         "三一重工", "徐工", "中联重科", "柳工", "山特维克", "美卓",
    #         "矿山设备", "砂石骨料",
    #     ],
    #     "language": "English"
    # },
]

# 向后兼容别名（供各模块独立运行时使用）
KEYWORDS = TOPICS[0]["keywords"]
THEME_CONFIG = TOPICS[0]["theme"]
TITLE_CONFIG = TOPICS[0]["title"]
LANGUAGE = TOPICS[0].get("language", "zh-CN")

# ─────────────────────────────────────────────
# 工具函数：将 title 转为安全的目录名
# ─────────────────────────────────────────────
def safe_dir_name(title: str) -> str:
    """将主题标题转换为安全的文件夹名（去掉特殊字符，空格替换为下划线）"""
    return title.replace("/", "_").replace("\\", "_").replace(" ", "_")


# ─────────────────────────────────────────────
# 步骤 1：调用爬虫，返回爬取结果目录
# ─────────────────────────────────────────────
def step_crawl(topic: dict) -> str:
    """运行爬虫，返回当日爬取数据的目录路径（按 日期/title 子目录存放）"""
    from crawler.news_crawler import run_crawler
    title_dir = safe_dir_name(topic["title"])
    run_crawler(keywords=topic["keywords"], title_dir=title_dir)
    # 爬虫按日期+主题保存到 crawler/<今日日期>/<title>/ 目录
    today_str = datetime.date.today().strftime("%Y-%m-%d")
    crawled_dir = os.path.join(ROOT_DIR, "crawler", today_str, title_dir)
    print(f"\n[主程序] 爬取目录: {crawled_dir}")
    return crawled_dir


# ─────────────────────────────────────────────
# 步骤 2：内容处理，返回简报 MD 文件路径
# ─────────────────────────────────────────────
def step_process(crawled_dir: str, topic: dict) -> str:
    """调用内容处理器，生成简报 MD 文件，返回其路径"""
    from textContent.content_processor import process_news
    title_dir = safe_dir_name(topic["title"])
    language = topic.get("language", "zh-CN")
    process_news(crawled_dir, theme=topic["theme"], title_dir=title_dir, language=language)
    # content_processor 将简报保存到 textContent/<日期>/<title>/news_brief_<日期>.md
    today_str = datetime.date.today().strftime("%Y-%m-%d")
    brief_path = os.path.join(ROOT_DIR, "textContent", today_str, title_dir, f"news_brief_{today_str}.md")
    print(f"[主程序] 简报文件: {brief_path}")
    return brief_path


# ─────────────────────────────────────────────
# 步骤 3：根据简报内容生成音频博客封面提示词
# ─────────────────────────────────────────────
def step_cover_prompt(brief_path: str, topic: dict) -> str:
    """读取简报内容，生成适合 Google Imagen 的 iOS 风格封面图片提示词"""
    import re
    from util.llm import LLmFactory
    today = datetime.date.today()
    # 格式：2026年3月30日
    date_cn = f"{today.year}年{today.month}月{today.day}日"
    title = f"{topic['title']}（{date_cn}）"

    # 提取所有新闻标题（以 "数字. " 开头的行）
    raw_titles = []
    if os.path.exists(brief_path):
        with open(brief_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                m = re.match(r"^(\d+)\.\s+(.+)$", line)
                if m:
                    raw_titles.append((m.group(1), m.group(2)))

    # 用大模型一次性批量压缩所有标题至 22 字以内
    news_titles = []
    language = topic.get("language", "zh-CN")
    if raw_titles:
        llm = LLmFactory().getDeepseek()
        # 构建批量压缩的 Prompt
        titles_input = "\n".join(f"{no}. {t}" for no, t in raw_titles)
        compress_prompt = (
            f"请将以下每条新闻标题压缩总结为不超过22个字符的简短标题，"
            f"保留核心信息，语言简练。"
            f"必须使用 {language} 输出压缩后的标题。"
            f"严格按照原来的编号输出，每条占一行，格式为：序号. 压缩标题。不要输出其他内容。\n\n"
            f"{titles_input}"
        )
        print("[主程序] 正在调用大模型压缩新闻标题...")
        result = llm.invoke(compress_prompt)
        # 解析压缩后的标题
        for line in result.content.strip().splitlines():
            line = line.strip()
            m = re.match(r"^(\d+)\.\s+(.+)$", line)
            if m:
                news_titles.append(f"{m.group(1)}. {m.group(2)}")

    # 将所有标题拼成列表字符串
    titles_block = "；".join(news_titles) if news_titles else "暂无新闻"

    # 构造 iOS 风格封面提示词
    # 特别针对英文主题，由于需要提供给画图模型使用，建议提示词包含明确的语言要求
    lang_hint = "（请使用纯英文书写封面所有的文字内容）" if "English" in language or "en" in language.lower() else "（中文字体呈现）"
    prompt = (
        f"Generate a minimalist iOS-style podcast cover image. "
        f"Top center bold title: '{title}'. "
        f"Below the title, display a compact list of these {len(news_titles)} news headlines, one per line: {titles_block}. "
        f"Design style: minimalist iOS aesthetic, soft gradient background (light blue to white or deep navy), "
        f"rounded card, subtle shadow, SF Pro style typography {lang_hint}, "
        f"decorated with small tech icons, overall simple, beautiful, high quality, 1:1 square composition."
    )

    # 将提示词保存到简报同目录
    prompt_path = brief_path.replace(".md", "_cover_prompt.txt")
    with open(prompt_path, "w", encoding="utf-8") as f:
        f.write(prompt)

    print(f"[主程序] 封面提示词已生成: {prompt_path}")
    print(f"[封面提示词]\n{prompt}\n")
    return prompt



# ─────────────────────────────────────────────
# 步骤 4：音频合成，返回 MP3 文件路径
# ─────────────────────────────────────────────
def step_audio(brief_path: str, topic: dict) -> str:
    """根据简报 MD 生成音频文件，返回 MP3 路径"""
    from audioContent.news_to_audio import convert_md_to_audio
    audio_dir = os.path.join(ROOT_DIR, "audioContent")
    title_dir = safe_dir_name(topic["title"])
    language = topic.get("language", "zh-CN")
    audio_path = convert_md_to_audio(brief_path, audio_dir, title=topic["title"], title_dir=title_dir, language=language)
    print(f"[主程序] 音频文件: {audio_path}")
    return audio_path


# ─────────────────────────────────────────────
# 步骤 5：播放音频
# ─────────────────────────────────────────────
def step_play(audio_path: str):
    """使用系统默认播放器播放生成的音频"""
    print(f"\n[主程序] 正在播放: {audio_path}")
    # Windows 系统使用 start 命令调用默认关联程序
    os.startfile(audio_path)


# ─────────────────────────────────────────────
# 步骤 6：LLM 提取最炸裂新闻 → 生成视频文件名
# ─────────────────────────────────────────────
def step_video_title(brief_path: str, topic: dict) -> str:
    """
    读取简报内容，让 LLM 挑选当天最值得关注的一条新闻，
    并生成适合作为视频文件名的标题字符串。
    格式示例：突发！OpenAI估值破1220亿 | 阿里云全模态升级 | AI每日速递 0401
    :return: 合法的视频文件名（不含扩展名），已去除不能用于文件名的特殊字符
    """
    import re
    from util.llm import LLmFactory

    today = datetime.date.today()
    # 月日格式，如 0401
    date_short = today.strftime("%m%d")
    topic_title = topic["title"]

    # 读取简报全文
    if not os.path.exists(brief_path):
        # 简报不存在时回退到默认文件名
        return f"{topic_title} {date_short}"

    with open(brief_path, "r", encoding="utf-8") as f:
        brief_content = f.read()

    llm = LLmFactory().getDeepseek()
    prompt = (
        f"以下是今天的新闻简报内容：\n\n{brief_content}\n\n"
        f"请从中挑选出今天最重磅、最值得关注的 3 条新闻，"
        f"用简短有力的中文（不超过20字）写成吸引眼球的标题，"
        f"然后按照以下格式输出视频文件名（不要加扩展名，不要加任何解释）：\n"
        f"<最炸裂新闻短标题> | {topic_title} {date_short}\n"
        f"例如：突发！OpenAI估值破1220亿 | AI每日速递 {date_short}"
    )
    print("[主程序] 正在调用大模型生成视频文件名...")
    result = llm.invoke(prompt)
    raw_title = result.content.strip().splitlines()[0].strip()

    # 去除文件名中不合法的字符（Windows/Linux 通用）
    safe_title = re.sub(r'[\\/:*?"<>|]', '', raw_title)
    # 去除首尾多余空白
    safe_title = safe_title.strip()

    print(f"[主程序] 智能视频文件名: {safe_title}")
    return safe_title


# ─────────────────────────────────────────────
# 步骤 7：合成视频
# ─────────────────────────────────────────────
def step_video(audio_path: str, video_title: str) -> str:
    """
    根据音频和封面图合成视频，使用 LLM 生成的智能标题作为文件名。
    :param audio_path: MP3 音频文件路径
    :param video_title: 不含扩展名的视频文件名
    :return: 生成的视频文件路径
    """
    from video.Audio2Video import create_video

    audio_dir = os.path.dirname(audio_path)
    # 封面图固定命名为 Gemini_Generated_Image.png，与现有流程一致
    image_path = os.path.join(audio_dir, "Gemini_Generated_Image.png")
    output_path = os.path.join(audio_dir, f"{video_title}.mp4")

    if not os.path.exists(image_path):
        print(f"[警告] 封面图不存在: {image_path}，跳过视频合成")
        return ""

    create_video(audio_path, image_path, output_path)
    print(f"[主程序] 视频文件: {output_path}")
    return output_path


# ─────────────────────────────────────────────
# 主流水线入口
# ─────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 50)
    print("      AI内容工厂 - 每日新闻播报流水线")
    print("=" * 50)

    # 遍历所有主题，依次执行完整流水线
    for topic in TOPICS:
        print(f"\n{'─' * 50}")
        print(f"  开始处理主题：{topic['title']}")
        print(f"{'─' * 50}")

        # 1. 爬虫
        crawled_dir = step_crawl(topic)

        # 2. 内容处理 & 简报生成
        brief_path = step_process(crawled_dir, topic)

        if not os.path.exists(brief_path):
            print(f"[警告] 主题「{topic['title']}」简报文件不存在: {brief_path}，跳过后续步骤")
            continue

        # 3. 生成封面提示词
        cover_prompt = step_cover_prompt(brief_path, topic)

        # 4. 音频合成
        audio_path = step_audio(brief_path, topic)

        # 5. LLM 生成视频文件名
        #video_title = step_video_title(brief_path, topic)

      

        # 7. 打印路径
        print("\n" + "=" * 50)
        print(f"✅ 主题「{topic['title']}」处理完成")
        print(f"✅ 封面提示词已保存，可用于 Google Imagen 生成封面图片")
        print(f"✅ 音频文件路径: {audio_path}")
        print("=" * 50)

    print(f"\n{'═' * 50}")
    print("  所有主题处理完毕！")
    print(f"{'═' * 50}")

