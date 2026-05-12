# -*- coding: utf-8 -*-
import argparse
import math
import os
import re
import subprocess
import wave

import imageio_ffmpeg


def get_audio_duration(audio_path: str) -> float:
    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    process = subprocess.run(
        [ffmpeg_exe, "-i", audio_path],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        check=False,
    )
    stderr = process.stderr.decode("utf-8", errors="ignore")
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", stderr)
    if not match:
        return 0.0
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def build_slide_durations(total_duration: float, slide_count: int) -> list[float]:
    if slide_count <= 0:
        return []
    if slide_count == 1:
        return [max(total_duration, 0.1)]

    total_duration = max(float(total_duration), 0.1)
    if total_duration < float(slide_count) * 2.0:
        durations = [total_duration / slide_count] * slide_count
        durations[-1] += total_duration - sum(durations)
        return durations

    overview_duration = max(2.0, min(4.0, total_duration * 0.25))
    detail_duration = (total_duration - overview_duration) / (slide_count - 1)

    if detail_duration < 2.0:
        durations = [total_duration / slide_count] * slide_count
    else:
        durations = [overview_duration] + [detail_duration] * (slide_count - 1)

    durations[-1] += total_duration - sum(durations)
    return durations


def create_keyboard_click_track(output_path: str, switch_times: list[float], total_duration: float) -> str:
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    sample_rate = 44100
    total_samples = max(1, int(total_duration * sample_rate))
    click_samples = int(0.075 * sample_rate)
    switch_samples = sorted(int(max(0.0, switch_time) * sample_rate) for switch_time in switch_times)
    switch_index = 0

    with wave.open(output_path, "w") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        frames = []
        for pos in range(total_samples):
            while switch_index < len(switch_samples) and pos >= switch_samples[switch_index] + click_samples:
                switch_index += 1

            sample = 0.0
            if switch_index < len(switch_samples) and pos >= switch_samples[switch_index]:
                i = pos - switch_samples[switch_index]
                envelope = max(0.0, 1.0 - i / click_samples)
                tone = math.sin(2 * math.pi * 1800 * (i / sample_rate))
                tick = math.sin(2 * math.pi * 3200 * (i / sample_rate))
                sample = (tone * 0.45 + tick * 0.25) * envelope

            value = int(max(-1.0, min(1.0, sample)) * 12000)
            frames.append(value.to_bytes(2, byteorder="little", signed=True))
            if len(frames) >= 4096:
                wav_file.writeframes(b"".join(frames))
                frames = []
        if frames:
            wav_file.writeframes(b"".join(frames))
    return output_path


def _video_size() -> tuple[int, int]:
    return 1920, 1080


def _fit_video_filter(input_label: str, index: str, width: int, height: int, duration: float | None = None) -> str:
    duration_filter = ""
    if duration is not None:
        duration_filter = f",trim=duration={max(duration, 0.1):.3f},setpts=PTS-STARTPTS"
    return (
        f"{input_label}scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},boxblur=24:2,setsar=1[bg{index}];"
        f"{input_label}scale={width}:{height}:force_original_aspect_ratio=decrease,setsar=1[fg{index}];"
        f"[bg{index}][fg{index}]overlay=(W-w)/2:(H-h)/2{duration_filter}[v{index}]"
    )


def _run_ffmpeg(cmd: list[str]):
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    except subprocess.CalledProcessError as e:
        err_msg = e.stderr.decode("utf-8", errors="ignore")
        print(f"视频合成失败: {err_msg}")
        raise RuntimeError(f"FFmpeg 合成视频出错:\n{err_msg}")


def _waveform_layout(width: int, height: int) -> tuple[int, int, int, int]:
    wave_height = min(int(height * 0.12), 160)
    wave_width = int(width * 0.72)
    wave_width = max(2, wave_width - (wave_width % 2))
    wave_x = int((width - wave_width) / 2)
    wave_y = height - wave_height - max(24, int(height * 0.035))
    return wave_width, wave_height, wave_x, wave_y


def _create_single_image_video(ffmpeg_exe: str, audio_path: str, image_path: str, output_path: str, width: int, height: int) -> str:
    wave_width, wave_height, wave_x, wave_y = _waveform_layout(width, height)

    cmd = [
        ffmpeg_exe, "-y",
        "-loop", "1", "-i", image_path,
        "-i", audio_path,
        "-filter_complex",
        _fit_video_filter("[0:v]", "0", width, height)
        + f";[1:a]showwaves=s={wave_width}x{wave_height}:mode=cline:colors=lightskyblue,format=rgba,colorkey=0x000000:0.04:0.0[wave];"
        + f"[v0][wave]overlay={wave_x}:{wave_y},format=yuv420p[finalv]",
        "-map", "[finalv]",
        "-map", "1:a",
        "-c:v", "libx264",
        "-c:a", "aac",
        "-preset", "ultrafast",
        "-shortest",
        output_path,
    ]
    _run_ffmpeg(cmd)
    return output_path


def _create_slideshow_video(
    ffmpeg_exe: str,
    audio_path: str,
    image_paths: list[str],
    output_path: str,
    width: int,
    height: int,
    slide_durations: list[float] | None = None,
    transition_clicks: bool = True,
) -> str:
    total_duration = get_audio_duration(audio_path)
    if total_duration <= 0:
        total_duration = float(len(image_paths) * 4)
    durations = slide_durations if slide_durations and len(slide_durations) == len(image_paths) else build_slide_durations(total_duration, len(image_paths))
    durations[-1] += total_duration - sum(durations)

    switch_times = []
    elapsed = 0.0
    for duration in durations[:-1]:
        elapsed += duration
        switch_times.append(elapsed)

    click_path = output_path + ".keyboard_clicks.wav"
    try:
        cmd = [ffmpeg_exe, "-y"]
        for image_path, duration in zip(image_paths, durations):
            cmd.extend(["-loop", "1", "-t", f"{max(duration, 0.1):.3f}", "-i", image_path])
        audio_index = len(image_paths)
        cmd.extend(["-i", audio_path])
        click_index = len(image_paths) + 1
        if transition_clicks:
            create_keyboard_click_track(click_path, switch_times, total_duration)
            cmd.extend(["-i", click_path])
        wave_width, wave_height, wave_x, wave_y = _waveform_layout(width, height)

        video_parts = []
        for index in range(len(image_paths)):
            video_parts.append(
                _fit_video_filter(f"[{index}:v]", str(index), width, height, durations[index])
            )
        concat_inputs = "".join(f"[v{index}]" for index in range(len(image_paths)))
        filter_complex = (
            ";".join(video_parts)
            + f";{concat_inputs}concat=n={len(image_paths)}:v=1:a=0[basev];"
            + f"[{audio_index}:a]asplit=2[voice][waveaudio];"
            + f"[waveaudio]showwaves=s={wave_width}x{wave_height}:mode=cline:colors=lightskyblue,format=rgba,colorkey=0x000000:0.04:0.0[wave];"
            + f"[basev][wave]overlay={wave_x}:{wave_y},format=yuv420p[finalv]"
        )
        audio_map = "[voice]"
        if transition_clicks:
            filter_complex += f";[voice][{click_index}:a]amix=inputs=2:duration=first:dropout_transition=0[aout]"
            audio_map = "[aout]"

        cmd.extend([
            "-filter_complex", filter_complex,
            "-map", "[finalv]",
            "-map", audio_map,
            "-c:v", "libx264",
            "-c:a", "aac",
            "-r", "30",
            "-preset", "ultrafast",
            "-movflags", "+faststart",
            "-shortest",
            output_path,
        ])
        _run_ffmpeg(cmd)
    finally:
        try:
            if os.path.exists(click_path):
                os.remove(click_path)
        except OSError:
            pass
    return output_path


def create_video(
    audio_path: str,
    image_path: str,
    output_path: str,
    image_paths: list[str] | None = None,
    slide_durations: list[float] | None = None,
    transition_clicks: bool = True,
) -> str:
    if not os.path.exists(audio_path):
        raise FileNotFoundError(f"输入的音频文件不存在: {audio_path}")
    if not os.path.exists(image_path):
        raise FileNotFoundError(f"输入的图片文件不存在: {image_path}")

    slide_paths = image_paths or [image_path]
    for slide_path in slide_paths:
        if not os.path.exists(slide_path):
            raise FileNotFoundError(f"输入的图片文件不存在: {slide_path}")

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    width, height = _video_size()
    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()

    print("[1/3] 加载输入文件...")
    if len(slide_paths) > 1:
        sound_text = "并叠加键盘切换音" if transition_clicks else "不叠加切换音"
        print(f"[2/3] 合成 {len(slide_paths)} 张新闻轮播图，{sound_text}...")
        final_video = _create_slideshow_video(
            ffmpeg_exe,
            audio_path,
            slide_paths,
            output_path,
            width,
            height,
            slide_durations,
            transition_clicks=transition_clicks,
        )
    else:
        print("[2/3] 合成单图视频，并保留音频波形效果...")
        final_video = _create_single_image_video(ffmpeg_exe, audio_path, image_path, output_path, width, height)

    print(f"[3/3] 视频合成成功: {final_video}")
    return final_video


if __name__ == "__main__":
    import datetime

    today = datetime.date.today().strftime("%Y-%m-%d")
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    audio_dir = os.path.join(base_dir, "audioContent", today, "AI_每日简报")
    default_audio = os.path.join(audio_dir, f"news_brief_{today}.mp3")
    default_image = os.path.join(audio_dir, "Gemini_Generated_Image.png")
    default_output = os.path.join(audio_dir, f"video_{today}.mp4")

    parser = argparse.ArgumentParser(description="将音频和图片合成为视频")
    parser.add_argument("-a", "--audio", type=str, default=default_audio, help="输入音频文件路径")
    parser.add_argument("-i", "--image", type=str, default=default_image, help="输入图片文件路径")
    parser.add_argument("-o", "--output", type=str, default=default_output, help="输出 MP4 路径")
    args = parser.parse_args()

    if args.audio != default_audio and args.image == default_image:
        args.image = os.path.join(os.path.dirname(args.audio), "Gemini_Generated_Image.png")
        args.output = os.path.join(os.path.dirname(args.audio), "video_output.mp4")

    create_video(args.audio, args.image, args.output)
