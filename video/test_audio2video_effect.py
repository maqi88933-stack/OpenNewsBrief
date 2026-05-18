import os
import sys
import wave
import math
import struct
import shutil
import subprocess
import uuid
import imageio_ffmpeg
from unittest.mock import patch
from PIL import Image, ImageDraw

# 将上一级目录加入sys.path以便导入外层或同级模块（按需）
base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(base_dir)

from video.Audio2Video import build_slide_durations, create_keyboard_click_track, create_video, get_audio_duration, _waveform_layout
import video.Audio2Video as audio2video


def test_get_waveform_color_uses_env_override():
    # 这里确认视频层不是写死颜色，而是读取 UI 通过环境变量下发的配置。
    with patch.dict(os.environ, {"OPENNEWSBRIEF_WAVEFORM_COLOR": "#E0E0E3"}, clear=False):
        assert audio2video.get_waveform_color() == "#E0E0E3"

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
            assert frame.size == (1920, 1080)
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
        assert get_audio_duration(final_video) >= 1.8

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
            assert frame.size == (1920, 1080)
            colors = frame.convert("RGB").resize((1, 1)).getpixel((0, 0))
        assert colors != (0, 0, 0)

        waveform_frame = os.path.join(workdir, "waveform_frame.png")
        subprocess.run(
            [
                imageio_ffmpeg.get_ffmpeg_exe(),
                "-y",
                "-ss",
                "1",
                "-i",
                final_video,
                "-frames:v",
                "1",
                waveform_frame,
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        with Image.open(waveform_frame) as frame:
            width, height = frame.size
            bottom = frame.convert("RGB").crop((0, int(height * 0.75), width, height))
            waveform_pixels = sum(
                1
                for r, g, b in bottom.getdata()
                if abs(r - g) < 14 and abs(g - b) < 14 and r > 140 and r < 240
            )
        assert waveform_pixels > 500
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def test_slideshow_concat_video_keeps_full_audio_duration():
    workdir = os.path.join(base_dir, f"tmp_video_test_{uuid.uuid4().hex}")
    os.makedirs(workdir, exist_ok=True)
    audio_path = os.path.join(workdir, "sample.wav")
    output_path = os.path.join(workdir, "slideshow_duration.mp4")
    image_paths = []

    with wave.open(audio_path, "w") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16000)
        frames = []
        for i in range(64000):
            value = int(7000 * math.sin(2 * math.pi * 440 * (i / 16000)))
            frames.append(struct.pack("<h", value))
        wav_file.writeframes(b"".join(frames))

    colors = ["#F2F2F7", "#EAF3FF", "#E8F7EE", "#FFF4D9", "#FFE8E8"]
    for index in range(10):
        image_path = os.path.join(workdir, f"slide_{index:03d}.png")
        image = Image.new("RGB", (320, 320), colors[index % len(colors)])
        draw = ImageDraw.Draw(image)
        draw.text((80, 140), f"Slide {index}", fill="#111111")
        image.save(image_path)
        image_paths.append(image_path)

    try:
        final_video = create_video(
            audio_path,
            image_paths[0],
            output_path,
            image_paths=image_paths,
            slide_durations=[0.4] * len(image_paths),
            transition_clicks=False,
        )

        assert get_audio_duration(final_video) >= 3.8
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def test_slideshow_waveform_changes_over_time():
    workdir = os.path.join(base_dir, f"tmp_video_test_{uuid.uuid4().hex}")
    os.makedirs(workdir, exist_ok=True)
    audio_path = os.path.join(workdir, "sample.wav")
    output_path = os.path.join(workdir, "slideshow_waveform.mp4")
    image_paths = []

    with wave.open(audio_path, "w") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16000)
        frames = []
        for i in range(64000):
            if i < 32000:
                value = int(10000 * math.sin(2 * math.pi * 440 * (i / 16000)))
            else:
                value = int(1000 * math.sin(2 * math.pi * 440 * (i / 16000)))
            frames.append(struct.pack("<h", value))
        wav_file.writeframes(b"".join(frames))

    for index in range(10):
        image_path = os.path.join(workdir, f"slide_{index:03d}.png")
        Image.new("RGB", (320, 320), "#F2F2F7").save(image_path)
        image_paths.append(image_path)

    try:
        final_video = create_video(
            audio_path,
            image_paths[0],
            output_path,
            image_paths=image_paths,
            slide_durations=[0.4] * len(image_paths),
            transition_clicks=False,
        )

        frames = []
        for timestamp in ("0.8", "3.0"):
            frame_path = os.path.join(workdir, f"frame_{timestamp}.png")
            subprocess.run(
                [
                    imageio_ffmpeg.get_ffmpeg_exe(),
                    "-y",
                    "-ss",
                    timestamp,
                    "-i",
                    final_video,
                    "-frames:v",
                    "1",
                    frame_path,
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
            frames.append(frame_path)

        with Image.open(frames[0]) as first, Image.open(frames[1]) as second:
            wave_width, wave_height, wave_x, wave_y = _waveform_layout(*first.size)
            first_wave = first.convert("RGB").crop((wave_x, wave_y, wave_x + wave_width, wave_y + wave_height))
            second_wave = second.convert("RGB").crop((wave_x, wave_y, wave_x + wave_width, wave_y + wave_height))
            changed_pixels = sum(
                1
                for a, b in zip(first_wave.getdata(), second_wave.getdata())
                if sum(abs(a[i] - b[i]) for i in range(3)) > 30
            )
        assert changed_pixels > 100
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def test_slideshow_uses_concat_file_to_keep_ffmpeg_command_short():
    workdir = os.path.join(base_dir, f"tmp_video_test_{uuid.uuid4().hex}")
    os.makedirs(workdir, exist_ok=True)
    audio_path = os.path.join(workdir, "sample.wav")
    output_path = os.path.join(workdir, "long_slideshow.mp4")
    image_paths = []

    with wave.open(audio_path, "w") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16000)
        wav_file.writeframes(struct.pack("<h", 0) * 1600)

    for index in range(80):
        image_path = os.path.join(workdir, f"slide_{index:03d}.png")
        Image.new("RGB", (64, 64), "#F2F2F7").save(image_path)
        image_paths.append(image_path)

    commands = []
    try:
        waveform_path = os.path.join(workdir, "waveform.mp4")
        with patch.object(audio2video, "get_audio_duration", return_value=8.0), \
                patch.object(audio2video, "_create_waveform_video", return_value=waveform_path), \
                patch.object(audio2video, "_run_ffmpeg", side_effect=lambda cmd: commands.append(cmd)):
            create_video(
                audio_path,
                image_paths[0],
                output_path,
                image_paths=image_paths,
                slide_durations=[0.1] * len(image_paths),
                transition_clicks=False,
            )

        assert commands
        cmd = commands[0]
        assert cmd.count("-i") == 3
        assert "-f" in cmd and "concat" in cmd
        assert len(" ".join(cmd)) < 8000
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

if __name__ == "__main__":
    test_waveform_effect()
    test_slide_durations_start_with_overview()
    test_keyboard_click_track_created_for_news_switches()
    test_slideshow_video_uses_multiple_news_images()
    test_slideshow_concat_video_keeps_full_audio_duration()
    test_slideshow_waveform_changes_over_time()
    test_slideshow_uses_concat_file_to_keep_ffmpeg_command_short()
