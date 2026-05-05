import os
import sys
import wave
import math
import struct
import shutil
import uuid
from PIL import Image, ImageDraw

# 将上一级目录加入sys.path以便导入外层或同级模块（按需）
base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(base_dir)

from video.Audio2Video import create_video

def test_waveform_effect():
    workdir = os.path.join(base_dir, f"tmp_video_test_{uuid.uuid4().hex}")
    os.makedirs(workdir, exist_ok=True)
    audio_path = os.path.join(workdir, "sample.wav")
    image_path = os.path.join(workdir, "Gemini_Generated_Image.png")
    output_path = os.path.join(workdir, "sample.mp4")

    with wave.open(audio_path, "w") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16000)
        frames = []
        for i in range(16000):
            value = int(8000 * math.sin(2 * math.pi * 440 * (i / 16000)))
            frames.append(struct.pack("<h", value))
        wav_file.writeframes(b"".join(frames))

    image = Image.new("RGB", (1080, 1080), "#F2F2F7")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((80, 120, 1000, 980), radius=48, fill="white")
    draw.text((140, 180), "AI Daily Brief", fill="#111111")
    image.save(image_path)

    try:
        final_video = create_video(audio_path, image_path, output_path)
        assert os.path.exists(final_video)
    except Exception as e:
        raise
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

if __name__ == "__main__":
    test_waveform_effect()
