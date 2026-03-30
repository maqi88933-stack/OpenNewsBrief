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


# TTS 语音配置
VOICE_ZH = "zh-CN-XiaoxiaoNeural"
VOICE_EN = "en-US-AriaNeural"

# 博客开场白和结束语模板
INTRO_TEMPLATE_ZH = "欢迎收听{title}，以下是 {date} 的简讯。\n\n"
OUTRO_ZH = "\n\n以上就是今天的全部新闻简讯，感谢收听，我们明天见！"

INTRO_TEMPLATE_EN = "Welcome to {title}. Here is the news brief for {date}.\n\n"
OUTRO_EN = "\n\nThat concludes today's news brief. Thank you for listening, and see you tomorrow!"


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


def build_tts_text(md_content: str, date_str: str, title: str = "AI 每日简报", language: str = "zh-CN") -> str:
    """
    构建适合 TTS 朗读的完整文本
    添加博客风格的开场白和结束语
    :param title: 主题标题，用于开场白
    :param language: 目标语言，决定使用中文还是英文的开场/结束语
    """
    # 清洗 Markdown 标记
    clean_text = clean_markdown(md_content)
    
    # 根据语言选择模板
    is_english = "English" in language or "en" in language.lower()
    intro_template = INTRO_TEMPLATE_EN if is_english else INTRO_TEMPLATE_ZH
    outro = OUTRO_EN if is_english else OUTRO_ZH
    
    # 添加开场白
    intro = intro_template.format(date=date_str, title=title)
    return intro + clean_text + outro


def get_output_path(md_path: str, base_dir: str, title_dir: str = None) -> str:
    """
    根据 MD 文件路径推断输出音频路径
    例如：textContent/2026-03-18/<title>/news_brief_2026-03-18.md
       -> audioContent/2026-03-18/<title>/news_brief_2026-03-18.mp3
    :param title_dir: 主题安全目录名，用于区分不同主题
    """
    # 提取文件名（无扩展名）
    filename = os.path.splitext(os.path.basename(md_path))[0]
    # 从文件名提取日期
    date_match = re.search(r"(\d{4}-\d{2}-\d{2})", filename)
    if date_match:
        date_dir = date_match.group(1)
    else:
        # 尝试从父目录或祖父目录提取日期
        parent_dir = os.path.basename(os.path.dirname(md_path))
        if re.match(r"^\d{4}-\d{2}-\d{2}$", parent_dir):
            date_dir = parent_dir
        else:
            grandparent_dir = os.path.basename(os.path.dirname(os.path.dirname(md_path)))
            if re.match(r"^\d{4}-\d{2}-\d{2}$", grandparent_dir):
                date_dir = grandparent_dir
            else:
                date_dir = datetime.now().strftime("%Y-%m-%d")

    # 按 日期/title_dir/ 子目录存放
    if title_dir:
        output_dir = os.path.join(base_dir, date_dir, title_dir)
    else:
        output_dir = os.path.join(base_dir, date_dir)
    os.makedirs(output_dir, exist_ok=True)
    return os.path.join(output_dir, filename + ".mp3")


def extract_date_str(md_path: str, language: str = "zh-CN") -> str:
    """从文件路径或文件名中提取日期字符串用于播报开场"""
    is_english = "English" in language or "en" in language.lower()
    
    date_obj = None
    # 先尝试从文件名提取
    filename = os.path.basename(md_path)
    match = re.search(r"(\d{4})-(\d{2})-(\d{2})", filename)
    if match:
        date_obj = date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    else:
        # 尝试从父目录提取
        parent = os.path.basename(os.path.dirname(md_path))
        match = re.search(r"(\d{4})-(\d{2})-(\d{2})", parent)
        if match:
            date_obj = date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
            
    if date_obj is None:
        date_obj = datetime.now().date()
        
    if is_english:
        return date_obj.strftime("%B %d, %Y")
    else:
        return f"{date_obj.year}年{date_obj.month:02d}月{date_obj.day:02d}日"


async def convert_to_audio(tts_text: str, output_path: str, is_english: bool = False):
    """调用 edge-tts 将文本转换为 MP3 音频文件"""
    import edge_tts
    voice = VOICE_EN if is_english else VOICE_ZH
    communicate = edge_tts.Communicate(tts_text, voice)
    await communicate.save(output_path)


def convert_md_to_audio(md_path: str, output_base_dir: str = None, title: str = "AI 每日简报", title_dir: str = None, language: str = "zh-CN") -> str:
    """
    主转换函数：读取 MD 文件并转换为音频
    :param md_path: 新闻简讯 MD 文件的绝对或相对路径
    :param output_base_dir: 音频输出的基础目录，默认为脚本所在的 audioContent 目录
    :param title: 主题标题，用于开场白
    :param title_dir: 主题安全目录名，用于区分不同主题
    :param language: 目标语言
    :return: 生成的音频文件路径
    """
    # 默认输出到本脚本所在目录（即 audioContent/）
    if output_base_dir is None:
        output_base_dir = os.path.dirname(os.path.abspath(__file__))

    is_english = "English" in language or "en" in language.lower()

    # 1. 读取 MD 文件
    print(f"[1/3] 读取文件: {md_path}")
    md_content = read_md_file(md_path)

    # 2. 构建 TTS 文本
    print("[2/3] 处理文本...")
    date_str = extract_date_str(md_path, language=language)
    tts_text = build_tts_text(md_content, date_str, title=title, language=language)

    # 3. 转换并保存音频
    output_path = get_output_path(md_path, output_base_dir, title_dir=title_dir)
    print(f"[3/3] 转换音频 -> {output_path}")
    asyncio.run(convert_to_audio(tts_text, output_path, is_english=is_english))

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
