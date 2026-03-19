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
    "人工智能", "大语言模型",

    # 国际头部开发商
    "OpenAI", "Anthropic", "Google",

    # 国内主流大模型开发商及其模型名称
    "百度", "文心一言",
    "阿里巴巴", "通义千问",
    "腾讯", "混元大模型",
    "字节跳动", "豆包",
    "智谱AI", "ChatGLM",
    "月之暗面", "Kimi",
    "深度求索", "DeepSeek",
    "MiniMax",
    "昆仑万维", "天工大模型",
    "商汤科技",
    "阶跃星辰",
    "面壁智能", "MiniCPM",

    # 芯片及计算硬件开发商
    "Nvidia", "AMD", "Intel", "英伟达", "英特尔", "昇腾",

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
# 步骤 3：音频合成，返回 MP3 文件路径
# ─────────────────────────────────────────────
def step_audio(brief_path: str) -> str:
    """根据简报 MD 生成音频文件，返回 MP3 路径"""
    from audioContent.news_to_audio import convert_md_to_audio
    audio_dir = os.path.join(ROOT_DIR, "audioContent")
    audio_path = convert_md_to_audio(brief_path, audio_dir)
    print(f"[主程序] 音频文件: {audio_path}")
    return audio_path


# ─────────────────────────────────────────────
# 步骤 4：播放音频
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

    # 3. 音频合成
    if not os.path.exists(brief_path):
        print(f"[错误] 简报文件不存在，无法生成音频: {brief_path}")
        sys.exit(1)
    audio_path = step_audio(brief_path)

    # 4. 打印路径并播放
    print("\n" + "=" * 50)
    print(f"✅ 音频文件路径: {audio_path}")
    print("=" * 50)
    step_play(audio_path)
