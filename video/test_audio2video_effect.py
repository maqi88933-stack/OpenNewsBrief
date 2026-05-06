import os
import sys
import wave
import math
import struct
import shutil
import subprocess
import uuid
import imageio_ffmpeg
from PIL import Image, ImageDraw

# 将上一级目录加入sys.path以便导入外层或同级模块（按需）
base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(base_dir)

from video.Audio2Video import build_slide_durations, create_keyboard_click_track, create_video

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


def test_slide_durations_start_with_overview():
    durations = build_slide_durations(20.0, 4)

    assert len(durations) == 4
    assert durations[0] == 4.0
    assert round(sum(durations), 2) == 20.0
    assert all(duration >= 2.0 for duration in durations)


def test_keyboard_click_track_created_for_news_switches():
    workdir = os.path.join(base_dir, f"tmp_video_test_{uuid.uuid4().hex}")
    os.makedirs(workdir, exist_ok=True)
    click_path = os.path.join(workdir, "keyboard_clicks.wav")

    try:
        created = create_keyboard_click_track(click_path, [4.0, 8.0], 10.0)
        assert created == click_path
        assert os.path.exists(click_path)
        assert os.path.getsize(click_path) > 1000
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def test_slideshow_video_uses_multiple_news_images():
    workdir = os.path.join(base_dir, f"tmp_video_test_{uuid.uuid4().hex}")
    os.makedirs(workdir, exist_ok=True)
    audio_path = os.path.join(workdir, "sample.wav")
    output_path = os.path.join(workdir, "slideshow.mp4")
    image_paths = []

    with wave.open(audio_path, "w") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16000)
        frames = []
        for i in range(32000):
            value = int(7000 * math.sin(2 * math.pi * 440 * (i / 16000)))
            frames.append(struct.pack("<h", value))
        wav_file.writeframes(b"".join(frames))

    for index, color in enumerate(["#F2F2F7", "#EAF3FF", "#E8F7EE"]):
        image_path = os.path.join(workdir, f"slide_{index}.png")
        image = Image.new("RGB", (540, 540), color)
        draw = ImageDraw.Draw(image)
        draw.text((80, 220), f"Slide {index}", fill="#111111")
        image.save(image_path)
        image_paths.append(image_path)

    try:
        final_video = create_video(audio_path, image_paths[0], output_path, image_paths=image_paths)
        assert os.path.exists(final_video)
        assert os.path.getsize(final_video) > 1000

        first_frame = os.path.join(workdir, "first_frame.png")
        subprocess.run(
            [
                imageio_ffmpeg.get_ffmpeg_exe(),
                "-y",
                "-ss",
                "0",
                "-i",
                final_video,
                "-frames:v",
                "1",
                first_frame,
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        with Image.open(first_frame) as frame:
            colors = frame.convert("RGB").resize((1, 1)).getpixel((0, 0))
        assert colors != (0, 0, 0)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

if __name__ == "__main__":
    test_waveform_effect()
    test_slide_durations_start_with_overview()
    test_keyboard_click_track_created_for_news_switches()
    test_slideshow_video_uses_multiple_news_images()
