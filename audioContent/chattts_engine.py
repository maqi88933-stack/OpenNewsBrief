# -*- coding: utf-8 -*-
import os
import subprocess
import tempfile
import wave

import imageio_ffmpeg
import numpy as np


CHAT_TTS_SAMPLE_RATE = int(os.environ.get("OPENNEWSBRIEF_CHATTTS_SAMPLE_RATE", "24000"))
CHAT_TTS_COMPILE = os.environ.get("OPENNEWSBRIEF_CHATTTS_COMPILE", "0") == "1"
CHAT_TTS_SOURCE = os.environ.get("OPENNEWSBRIEF_CHATTTS_SOURCE", "huggingface")
CHAT_TTS_MODEL_DIR = os.environ.get("OPENNEWSBRIEF_CHATTTS_MODEL_DIR", "")
CHAT_TTS_SPEED = float(os.environ.get("OPENNEWSBRIEF_CHATTTS_SPEED", "1.15"))
_CHAT_MODEL = None


def _load_chattts():
    global _CHAT_MODEL
    if _CHAT_MODEL is not None:
        return _CHAT_MODEL
    try:
        import ChatTTS
    except ImportError as exc:
        raise RuntimeError(
            "未安装 ChatTTS。请先安装 ChatTTS 和 CPU 版 torch，或设置 "
            "OPENNEWSBRIEF_TTS_ENGINE=edge 临时切回 Edge TTS。"
        ) from exc

    chat = ChatTTS.Chat()
    custom_path = CHAT_TTS_MODEL_DIR or None
    loaded = chat.load(source=CHAT_TTS_SOURCE, custom_path=custom_path, compile=CHAT_TTS_COMPILE)
    if not loaded:
        raise RuntimeError("ChatTTS 模型加载失败，请检查模型下载是否完成。")
    _CHAT_MODEL = chat
    return _CHAT_MODEL


def _to_float_array(wav):
    if hasattr(wav, "detach"):
        wav = wav.detach().cpu().numpy()
    wav = np.asarray(wav, dtype=np.float32).squeeze()
    if wav.ndim != 1:
        wav = wav.reshape(-1)
    return np.clip(wav, -1.0, 1.0)


def _write_wav(path, wav, sample_rate=CHAT_TTS_SAMPLE_RATE):
    pcm = (_to_float_array(wav) * 32767).astype(np.int16)
    with wave.open(path, "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(sample_rate)
        f.writeframes(pcm.tobytes())


def _wav_to_mp3(wav_path, output_path):
    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    cmd = [ffmpeg_exe, "-y", "-i", wav_path]
    if CHAT_TTS_SPEED and abs(CHAT_TTS_SPEED - 1.0) > 0.01:
        cmd.extend(["-filter:a", f"atempo={CHAT_TTS_SPEED}"])
    cmd.extend(["-codec:a", "libmp3lame", "-q:a", "2", output_path])
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True)
    return output_path


def synthesize_text(text: str, output_path: str) -> str:
    clean_text = (text or "").strip()
    if not clean_text:
        raise ValueError("TTS 文本为空，无法生成音频")

    chat = _load_chattts()
    wavs = chat.infer([clean_text])
    if not wavs:
        raise RuntimeError("ChatTTS 未返回音频")

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        wav_path = tmp.name
    try:
        _write_wav(wav_path, wavs[0])
        return _wav_to_mp3(wav_path, output_path)
    finally:
        try:
            os.remove(wav_path)
        except OSError:
            pass
