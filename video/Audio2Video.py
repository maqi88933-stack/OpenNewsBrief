# -*- coding: utf-8 -*-
"""
将音频和对应的封面合成视频文件
"""

import os
import argparse
from moviepy import AudioFileClip, ImageClip


def create_video(audio_path: str, image_path: str, output_path: str) -> str:
    """
    将指定的音频和图片文件合并为视频
    :param audio_path: 音频文件(.mp3, .wav 等)绝对或相对路径
    :param image_path: 封面图片(.jpg, .png 等)绝对或相对路径
    :param output_path: 输出视频文件(.mp4)绝对或相对路径
    :return: 最终生成的视频路径
    """
    # 1. 检查文件是否存在
    if not os.path.exists(audio_path):
        raise FileNotFoundError(f"输入的音频文件不存在: {audio_path}")
    
    if not os.path.exists(image_path):
        raise FileNotFoundError(f"输入的图片文件不存在: {image_path}")
        
    print(f"[1/3] 加载输入文件...")
    audio_clip = AudioFileClip(audio_path)
    
    # 图片持续时间与音频同步
    image_clip = ImageClip(image_path).with_duration(audio_clip.duration)
    
    # 图片配上音频
    video_clip = image_clip.with_audio(audio_clip)

    # 确保输出目录存在
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

    print(f"[2/3] 开始合成视频文件(这可能需要一些时间)...")
    # 2. 导出视频：使用非常低的 fps, h264 和 ultrafast preset 来加快导出速度并缩小体积
    #    针对有声静止图片，1 fps 完全可以满足需求。
    video_clip.write_videofile(
        output_path,
        fps=1, 
        codec="libx264",
        audio_codec="aac",
        preset="ultrafast",
        logger=None # 设置为None可以避免过多输出，设为'bar'可以显示进度条
    )
    
    # 3. 释放资源
    audio_clip.close()
    image_clip.close()
    video_clip.close()

    print(f"✅ [3/3] 视频合成成功: {output_path}")
    return output_path


if __name__ == "__main__":
    import datetime
    
    # 默认获取今天的日期
    today = datetime.date.today().strftime("%Y-%m-%d")
    
    # 构建默认路径
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    audio_dir = os.path.join(base_dir, "audioContent", today)
    
    default_audio = os.path.join(audio_dir, f"news_brief_{today}.mp3")
    default_image = os.path.join(audio_dir, "Gemini_Generated_Image.png")
    default_output = os.path.join(audio_dir, f"video_{today}.mp4")
    
    parser = argparse.ArgumentParser(description="将音频和图片合成视频")
    parser.add_argument("-a", "--audio", type=str, default=default_audio, help="输入的音频文件路径")
    parser.add_argument("-i", "--image", type=str, default=default_image, help="输入的图片文件路径 (默认: 同目录下的 Gemini_Generated_Image.png)")
    parser.add_argument("-o", "--output", type=str, default=default_output, help="输出的视频文件路径 (.mp4)")
    args = parser.parse_args()

    # 如果有传递具体的 audio 且未指定 image，则覆盖 image 路径为该 audio 所在目录下的 Gemini_Generated_Image.png
    if args.audio != default_audio and args.image == default_image:
        args.image = os.path.join(os.path.dirname(args.audio), "Gemini_Generated_Image.png")
        args.output = os.path.join(os.path.dirname(args.audio), "video_output.mp4")

    print(f"[{today}] 准备合并以下文件为视频：")
    print(f" 音频：{args.audio}")
    print(f" 图片：{args.image}")
    create_video(args.audio, args.image, args.output)
