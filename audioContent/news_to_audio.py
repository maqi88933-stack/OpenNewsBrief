# -*- coding: utf-8 -*-
"""
新闻简讯 MD 文件转博客音频程序
使用 edge-tts（微软 Edge 神经网络语音）将 Markdown 文本转换为 MP3 音频文件
"""

import asyncio
import os
import re
import sys
from datetime import date, datetime


# TTS 语音配置 - 使用最自然的中文女声
VOICE = "zh-CN-XiaoxiaoNeural"

# 博客开场白和结束语模板
INTRO_TEMPLATE = "欢迎收听今日科技前沿播报，以下是 {date} 的每日新闻简讯。\n\n"
OUTRO = "\n\n以上就是今天的全部新闻简讯，感谢收听，我们明天见！"


def read_md_file(md_path: str) -> str:
    """读取 Markdown 文件内容"""
    if not os.path.exists(md_path):
        raise FileNotFoundError(f"文件不存在: {md_path}")
    with open(md_path, "r", encoding="utf-8") as f:
        return f.read()


def clean_markdown(text: str) -> str:
    """
    清洗 Markdown 标记，转换为适合 TTS 朗读的纯文本
    处理：标题符号、引用块、加粗、链接、水平线等
    """
    # 去除标题的 # 号，保留文字
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    # 去除引用块的 > 符号
    text = re.sub(r"^>\s*\*\*.*?\*\*[:：]\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"^>\s*", "", text, flags=re.MULTILINE)
    # 去除加粗/斜体标记 ** 和 *
    text = re.sub(r"\*{1,2}(.*?)\*{1,2}", r"\1", text)
    # 去除 Markdown 链接 [text](url) -> text
    text = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", text)
    # 去除图片 ![alt](url)
    text = re.sub(r"!\[.*?\]\(.*?\)", "", text)
    # 去除水平线
    text = re.sub(r"^---+$", "", text, flags=re.MULTILINE)
    # 去除行内代码和代码块
    text = re.sub(r"`[^`]+`", "", text)
    # 合并多余的空行（超过两个换行的都压缩为两个）
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def build_tts_text(md_content: str, date_str: str) -> str:
    """
    构建适合 TTS 朗读的完整文本
    添加博客风格的开场白和结束语
    """
    # 清洗 Markdown 标记
    clean_text = clean_markdown(md_content)
    # 添加开场白
    intro = INTRO_TEMPLATE.format(date=date_str)
    return intro + clean_text + OUTRO


def get_output_path(md_path: str, base_dir: str) -> str:
    """
    根据 MD 文件路径推断输出音频路径
    例如：textContent/2026-03-18/news_brief_2026-03-18.md
       -> audioContent/2026-03-18/news_brief_2026-03-18.mp3
    """
    # 提取文件名（无扩展名）
    filename = os.path.splitext(os.path.basename(md_path))[0]
    # 尝试从父目录名提取日期
    parent_dir = os.path.basename(os.path.dirname(md_path))
    # 判断父目录是否为日期格式 YYYY-MM-DD
    if re.match(r"^\d{4}-\d{2}-\d{2}$", parent_dir):
        date_dir = parent_dir
    else:
        # 从文件名提取日期，格式 news_brief_2026-03-18
        date_match = re.search(r"(\d{4}-\d{2}-\d{2})", filename)
        date_dir = date_match.group(1) if date_match else datetime.now().strftime("%Y-%m-%d")

    output_dir = os.path.join(base_dir, date_dir)
    os.makedirs(output_dir, exist_ok=True)
    return os.path.join(output_dir, filename + ".mp3")


def extract_date_str(md_path: str) -> str:
    """从文件路径或文件名中提取日期字符串用于播报开场"""
    filename = os.path.basename(md_path)
    match = re.search(r"(\d{4})-(\d{2})-(\d{2})", filename)
    if match:
        return f"{match.group(1)}年{match.group(2)}月{match.group(3)}日"
    # 也尝试父目录
    parent = os.path.basename(os.path.dirname(md_path))
    match = re.search(r"(\d{4})-(\d{2})-(\d{2})", parent)
    if match:
        return f"{match.group(1)}年{match.group(2)}月{match.group(3)}日"
    return datetime.now().strftime("%Y年%m月%d日")


async def convert_to_audio(tts_text: str, output_path: str):
    """调用 edge-tts 将文本转换为 MP3 音频文件"""
    import edge_tts
    communicate = edge_tts.Communicate(tts_text, VOICE)
    await communicate.save(output_path)


def convert_md_to_audio(md_path: str, output_base_dir: str = None) -> str:
    """
    主转换函数：读取 MD 文件并转换为音频
    :param md_path: 新闻简讯 MD 文件的绝对或相对路径
    :param output_base_dir: 音频输出的基础目录，默认为脚本所在的 audioContent 目录
    :return: 生成的音频文件路径
    """
    # 默认输出到本脚本所在目录（即 audioContent/）
    if output_base_dir is None:
        output_base_dir = os.path.dirname(os.path.abspath(__file__))

    # 1. 读取 MD 文件
    print(f"[1/3] 读取文件: {md_path}")
    md_content = read_md_file(md_path)

    # 2. 构建 TTS 文本
    print("[2/3] 处理文本...")
    date_str = extract_date_str(md_path)
    tts_text = build_tts_text(md_content, date_str)

    # 3. 转换并保存音频
    output_path = get_output_path(md_path, output_base_dir)
    print(f"[3/3] 转换音频 -> {output_path}")
    asyncio.run(convert_to_audio(tts_text, output_path))

    print(f"✅ 音频生成成功: {output_path}")
    return output_path


if __name__ == "__main__":

    # 默认读取 textContent 当天的文件
    today = date.today().strftime("%Y-%m-%d")
    md_file_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "../textContent",
        today,
        f"news_brief_{today}.md",
    )
    
    #判断文件是否存在
    if not os.path.exists(md_file_path):
        print(f"文件不存在: {md_file_path}")
        sys.exit(1)
    convert_md_to_audio(md_file_path)
