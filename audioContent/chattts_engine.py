# -*- coding: utf-8 -*-
import json
import os
import re
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
CHAT_TTS_PROMPT = os.environ.get("OPENNEWSBRIEF_CHATTTS_PROMPT", "[speed_5]")
CHAT_TTS_SPEAKER_CACHE_PATH = os.environ.get(
    "OPENNEWSBRIEF_CHATTTS_SPEAKER_CACHE",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "chattts_speakers.json"),
)
CHAT_TTS_ROLE_SPEAKERS = {
    "female": os.environ.get("OPENNEWSBRIEF_CHATTTS_FEMALE_SPK", ""),
    "male": os.environ.get("OPENNEWSBRIEF_CHATTTS_MALE_SPK", ""),
    "narrator": os.environ.get("OPENNEWSBRIEF_CHATTTS_NARRATOR_SPK", ""),
}
_CHAT_MODEL = None
_ROLE_SPEAKERS = {}
_CN_DIGITS = "零一二三四五六七八九"
_CN_UNITS = ["", "十", "百", "千"]
_CN_BIG_UNITS = ["", "万", "亿"]


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


def _get_role_speaker(chat, role: str) -> str:
    speaker_role = role if role in ("female", "male", "narrator") else "narrator"
    if speaker_role in _ROLE_SPEAKERS:
        return _ROLE_SPEAKERS[speaker_role]

    spk_emb = CHAT_TTS_ROLE_SPEAKERS.get(speaker_role) or _load_speaker_cache().get(speaker_role)
    if not spk_emb:
        spk_emb = chat.sample_random_speaker()
        _save_speaker_cache_value(speaker_role, spk_emb)
    _ROLE_SPEAKERS[speaker_role] = spk_emb
    return spk_emb


def _load_speaker_cache() -> dict:
    if not CHAT_TTS_SPEAKER_CACHE_PATH or not os.path.exists(CHAT_TTS_SPEAKER_CACHE_PATH):
        return {}
    try:
        with open(CHAT_TTS_SPEAKER_CACHE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_speaker_cache_value(role: str, spk_emb: str) -> None:
    if not CHAT_TTS_SPEAKER_CACHE_PATH:
        return
    data = _load_speaker_cache()
    data[role] = spk_emb
    os.makedirs(os.path.dirname(os.path.abspath(CHAT_TTS_SPEAKER_CACHE_PATH)), exist_ok=True)
    with open(CHAT_TTS_SPEAKER_CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _digits_to_cn(text: str) -> str:
    return "".join(_CN_DIGITS[int(ch)] if ch.isdigit() else ch for ch in text)


def _int_to_cn(number_text: str) -> str:
    number_text = number_text.lstrip("0") or "0"
    if number_text == "0":
        return "零"

    def convert_group(group: str) -> str:
        group = group.zfill(4)
        result = []
        zero_pending = False
        for index, ch in enumerate(group):
            digit = int(ch)
            unit_index = 3 - index
            if digit == 0:
                if result:
                    zero_pending = True
                continue
            if zero_pending:
                result.append("零")
                zero_pending = False
            result.append(_CN_DIGITS[digit] + _CN_UNITS[unit_index])
        return "".join(result)

    groups = []
    while number_text:
        groups.insert(0, number_text[-4:])
        number_text = number_text[:-4]

    parts = []
    zero_pending = False
    for index, group in enumerate(groups):
        value = int(group)
        big_unit = _CN_BIG_UNITS[len(groups) - index - 1]
        if value == 0:
            if parts:
                zero_pending = True
            continue
        if zero_pending:
            parts.append("零")
            zero_pending = False
        parts.append(convert_group(group) + big_unit)

    result = "".join(parts)
    return result.replace("一十", "十", 1)


def normalize_tts_text(text: str) -> str:
    text = text or ""
    text = re.sub(
        r"(\d{4})年(\d{1,2})月(\d{1,2})日",
        lambda m: f"{_digits_to_cn(m.group(1))}年{_int_to_cn(m.group(2))}月{_int_to_cn(m.group(3))}日",
        text,
    )
    text = re.sub(
        r"(\d{4})-(\d{1,2})-(\d{1,2})",
        lambda m: f"{_digits_to_cn(m.group(1))}年{_int_to_cn(m.group(2))}月{_int_to_cn(m.group(3))}日",
        text,
    )
    text = re.sub(
        r"(\d+(?:,\d{3})*(?:\.\d+)?)%",
        lambda m: "百分之" + (
            _int_to_cn(m.group(1).split(".")[0].replace(",", "")) + "点" + _digits_to_cn(m.group(1).split(".")[1])
            if "." in m.group(1)
            else _int_to_cn(m.group(1).replace(",", ""))
        ),
        text,
    )
    text = re.sub(
        r"\d+(?:,\d{3})*\.\d+",
        lambda m: _int_to_cn(m.group(0).split(".")[0].replace(",", "")) + "点" + _digits_to_cn(m.group(0).split(".")[1]),
        text,
    )
    text = re.sub(r"\d+(?:,\d{3})*", lambda m: _int_to_cn(m.group(0).replace(",", "")), text)
    return text


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


def synthesize_text(text: str, output_path: str, role: str = "narrator") -> str:
    clean_text = normalize_tts_text((text or "").strip())
    if not clean_text:
        raise ValueError("TTS 文本为空，无法生成音频")

    chat = _load_chattts()
    params = chat.InferCodeParams(prompt=CHAT_TTS_PROMPT, spk_emb=_get_role_speaker(chat, role))
    wavs = chat.infer([clean_text], params_infer_code=params)
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
