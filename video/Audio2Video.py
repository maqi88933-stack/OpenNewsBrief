# -*- coding: utf-8 -*-
"""
将音频和对应的封面合成视频文件
"""

import os
import argparse
import subprocess
import imageio_ffmpeg
from PIL import Image


def create_video(audio_path: str, image_path: str, output_path: str) -> str:
    """
    将指定的音频和图片文件合并为视频，并在底部添加跳动的音频波形图
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
        
    print(f"[1/3] 加载输入文件并获取图片尺寸...")
    
    # 获取图片尺寸以调整波形图和最终视频
    with Image.open(image_path) as img:
        width, height = img.size
        # 确保尺寸是偶数 (libx264 编码要求)
        width = width - (width % 2)
        height = height - (height % 2)

    # 波形图高度设为图片高度的 15%左右，最大不超过 200px
    wave_height = min(int(height * 0.15), 200)
    # 波形图宽度设为宽度的 60%，左右各留 20% 空白
    wave_width = int(width * 0.6)
    wave_width = wave_width - (wave_width % 2) # 必须保证是偶数
    wave_x = int(width * 0.2)
    # 波形图颜色: cyan (或者其它鲜艳颜色)
    wave_color = "cyan"

    # 确保输出目录存在
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

    print(f"[2/3] 开始合成带有音频波形图的视频文件(这可能需要一些时间)...")
    
    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    
    # 使用 ffmpeg filter_complex 生成波形图并叠加在图片底部
    # [1:a] 取音频，生成波形图 [wave];
    # [0:v][wave] 将原始图片和波形图叠加 (overlay=wave_x:H-h 居中位于底部) [outv];
    # 对输出视频按 [outv]scale 调整为偶数尺寸 [finalv]
    cmd = [
        ffmpeg_exe, "-y",
        "-loop", "1", "-i", image_path,
        "-i", audio_path,
        "-filter_complex", 
        f"[1:a]showwaves=s={wave_width}x{wave_height}:mode=cline:colors={wave_color}[wave];[0:v][wave]overlay={wave_x}:H-h[outv];[outv]scale={width}:{height}[finalv]",
        "-map", "[finalv]",
        "-map", "1:a",
        "-c:v", "libx264",
        "-c:a", "aac",
        "-preset", "ultrafast",
        "-shortest",
        output_path
    ]
    
    try:
        # 执行命令，如果失败将捕获错误日志
        process = subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    except subprocess.CalledProcessError as e:
        err_msg = e.stderr.decode('utf-8', errors='ignore')
        print(f"❌ 视频合成失败: {err_msg}")
        raise RuntimeError(f"FFmpeg合成视频出错:\n{err_msg}")

    print(f"✅ [3/3] 视频合成成功: {output_path}")
    return output_path


if __name__ == "__main__":
    import datetime
    
    # 默认获取今天的日期
    today = datetime.date.today().strftime("%Y-%m-%d")
    
    # 构建默认路径
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    audio_dir = os.path.join(base_dir, "audioContent", today)

    #AI_每日简报
    audio_dir = os.path.join(audio_dir, "AI_每日简报")
    
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
