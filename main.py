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
# 爬虫关键词配置：crawler/news_crawler.py 使用
KEYWORDS = [
    
    # 国际头部开发商
    "OpenAI", "Anthropic", "Google","x-ai",
    "GPT","Claude","Gemini","Mistral","Grok","Claude Code","Codex","antigravity","stitch google"
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
    "Nvidia", "AMD", "Intel", "英伟达", "英特尔"
    # 智能体领域
    "AI Agent", "智能体", "AutoGPT",
]

# 内容处理主题配置：textContent/content_processor.py 使用
THEME_CONFIG = "AI大模型、智能体、最新的科技前沿动态"

# ─────────────────────────────────────────────
# 步骤 1：调用爬虫，返回爬取结果目录
# ─────────────────────────────────────────────
def step_crawl() -> str:
    """运行爬虫，返回当日爬取数据的目录路径"""
    from crawler.news_crawler import run_crawler
    run_crawler()
    # 爬虫按日期保存到 crawler/<今日日期>/ 目录
    today_str = datetime.date.today().strftime("%Y-%m-%d")
    crawled_dir = os.path.join(ROOT_DIR, "crawler", today_str)
    print(f"\n[主程序] 爬取目录: {crawled_dir}")
    return crawled_dir


# ─────────────────────────────────────────────
# 步骤 2：内容处理，返回简报 MD 文件路径
# ─────────────────────────────────────────────
def step_process(crawled_dir: str) -> str:
    """调用内容处理器，生成简报 MD 文件，返回其路径"""
    from textContent.content_processor import process_news
    process_news(crawled_dir)
    # content_processor 将简报保存到 textContent/<日期>/news_brief_<日期>.md
    today_str = datetime.date.today().strftime("%Y-%m-%d")
    brief_path = os.path.join(ROOT_DIR, "textContent", today_str, f"news_brief_{today_str}.md")
    print(f"[主程序] 简报文件: {brief_path}")
    return brief_path


# ─────────────────────────────────────────────
# 步骤 3：根据简报内容生成音频博客封面提示词
# ─────────────────────────────────────────────
def step_cover_prompt(brief_path: str) -> str:
    """读取简报内容，生成适合 Google Imagen 的 iOS 风格封面图片提示词"""
    import re
    from util.llm import LLmFactory
    today = datetime.date.today()
    # 格式：2026年3月30日
    date_cn = f"{today.year}年{today.month}月{today.day}日"
    title = f"AI 每日简报（{date_cn}）"

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
    if raw_titles:
        llm = LLmFactory().getDeepseek()
        # 构建批量压缩的 Prompt
        titles_input = "\n".join(f"{no}. {t}" for no, t in raw_titles)
        compress_prompt = (
            f"请将以下每条新闻标题压缩总结为不超过22个中文字的简短标题，"
            f"保留核心信息，语言简练。"
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

    # 构造 iOS 风格封面提示词（中文）
    prompt = (
        f"生成一张简洁优雅的iOS风格播客封面图片。"
        f"封面顶部居中显示加粗大标题：「{title}」。"
        f"标题下方用紧凑列表展示以下{len(news_titles)}条AI新闻标题，每条一行：{titles_block}。"
        f"设计风格：极简iOS美学，柔和渐变背景（浅蓝到白色或深海军蓝），"
        f"圆角卡片，细腻投影，SF Pro风格字体排版，"
        f"点缀小型AI科技图标，整体简单漂亮，高品质，1:1正方形构图。"
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
def step_audio(brief_path: str) -> str:
    """根据简报 MD 生成音频文件，返回 MP3 路径"""
    from audioContent.news_to_audio import convert_md_to_audio
    audio_dir = os.path.join(ROOT_DIR, "audioContent")
    audio_path = convert_md_to_audio(brief_path, audio_dir)
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
# 主流水线入口
# ─────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 50)
    print("      AI内容工厂 - 每日新闻播报流水线")
    print("=" * 50)

    # 1. 爬虫
    crawled_dir = step_crawl()

    # 2. 内容处理 & 简报生成
    brief_path = step_process(crawled_dir)

    if not os.path.exists(brief_path):
        print(f"[错误] 简报文件不存在: {brief_path}")
        sys.exit(1)

    # 3. 生成封面提示词
    cover_prompt = step_cover_prompt(brief_path)

    # 4. 音频合成
    audio_path = step_audio(brief_path)

    # 5. 打印路径并播放
    print("\n" + "=" * 50)
    print(f"✅ 封面提示词已保存，可用于 Google Imagen 生成封面图片")
    print(f"✅ 音频文件路径: {audio_path}")
    print("=" * 50)
    step_play(audio_path)
