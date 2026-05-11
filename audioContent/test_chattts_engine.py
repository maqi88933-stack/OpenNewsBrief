import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

from audioContent import chattts_engine


class FakeInferCodeParams:
    def __init__(self, **kwargs):
        self.prompt = kwargs.get("prompt")
        self.spk_emb = kwargs.get("spk_emb")


class FakeChat:
    InferCodeParams = FakeInferCodeParams

    def __init__(self):
        self.sample_count = 0
        self.calls = []

    def sample_random_speaker(self):
        self.sample_count += 1
        return f"speaker-{self.sample_count}"

    def infer(self, texts, params_infer_code=None):
        self.calls.append((texts, params_infer_code.spk_emb))
        return [[0.0, 0.1, -0.1]]


class TestChatTtsEngine(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.original_cache_path = chattts_engine.CHAT_TTS_SPEAKER_CACHE_PATH
        self.original_role_speakers = dict(chattts_engine.CHAT_TTS_ROLE_SPEAKERS)
        chattts_engine.CHAT_TTS_SPEAKER_CACHE_PATH = os.path.join(self.tmpdir, "chattts_speakers.json")
        chattts_engine.CHAT_TTS_ROLE_SPEAKERS.update({"female": "", "male": "", "narrator": ""})
        chattts_engine._CHAT_MODEL = None
        chattts_engine._ROLE_SPEAKERS.clear()

    def tearDown(self):
        chattts_engine.CHAT_TTS_SPEAKER_CACHE_PATH = self.original_cache_path
        chattts_engine.CHAT_TTS_ROLE_SPEAKERS.clear()
        chattts_engine.CHAT_TTS_ROLE_SPEAKERS.update(self.original_role_speakers)
        chattts_engine._ROLE_SPEAKERS.clear()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_reuses_stable_separate_speaker_for_each_gender_role(self):
        fake_chat = FakeChat()
        chattts_engine._CHAT_MODEL = fake_chat

        with patch.object(chattts_engine, "_write_wav"), patch.object(chattts_engine, "_wav_to_mp3", side_effect=lambda _wav, out: out):
            chattts_engine.synthesize_text("第一句女声", "female1.mp3", role="female")
            chattts_engine.synthesize_text("第二句女声", "female2.mp3", role="female")
            chattts_engine.synthesize_text("第一句男声", "male1.mp3", role="male")
            chattts_engine.synthesize_text("第二句男声", "male2.mp3", role="male")

        self.assertEqual(fake_chat.sample_count, 2)
        self.assertEqual(fake_chat.calls[0][1], fake_chat.calls[1][1])
        self.assertEqual(fake_chat.calls[2][1], fake_chat.calls[3][1])
        self.assertNotEqual(fake_chat.calls[0][1], fake_chat.calls[2][1])

    def test_uses_configured_gender_speakers_without_sampling(self):
        fake_chat = FakeChat()
        chattts_engine._CHAT_MODEL = fake_chat
        chattts_engine.CHAT_TTS_ROLE_SPEAKERS["female"] = "configured-female"
        chattts_engine.CHAT_TTS_ROLE_SPEAKERS["male"] = "configured-male"

        with patch.object(chattts_engine, "_write_wav"), patch.object(chattts_engine, "_wav_to_mp3", side_effect=lambda _wav, out: out):
            chattts_engine.synthesize_text("第一句女声", "female.mp3", role="female")
            chattts_engine.synthesize_text("第一句男声", "male.mp3", role="male")

        self.assertEqual(fake_chat.sample_count, 0)
        self.assertEqual(fake_chat.calls[0][1], "configured-female")
        self.assertEqual(fake_chat.calls[1][1], "configured-male")

    def test_reuses_persisted_gender_speakers_across_process_runs(self):
        first_chat = FakeChat()
        chattts_engine._CHAT_MODEL = first_chat

        with patch.object(chattts_engine, "_write_wav"), patch.object(chattts_engine, "_wav_to_mp3", side_effect=lambda _wav, out: out):
            chattts_engine.synthesize_text("第一句女声", "female.mp3", role="female")
            chattts_engine.synthesize_text("第一句男声", "male.mp3", role="male")

        chattts_engine._ROLE_SPEAKERS.clear()
        second_chat = FakeChat()
        chattts_engine._CHAT_MODEL = second_chat

        with patch.object(chattts_engine, "_write_wav"), patch.object(chattts_engine, "_wav_to_mp3", side_effect=lambda _wav, out: out):
            chattts_engine.synthesize_text("第二天女声", "female-next.mp3", role="female")
            chattts_engine.synthesize_text("第二天男声", "male-next.mp3", role="male")

        self.assertEqual(second_chat.sample_count, 0)
        self.assertEqual(second_chat.calls[0][1], first_chat.calls[0][1])
        self.assertEqual(second_chat.calls[1][1], first_chat.calls[1][1])

    def test_normalize_tts_text_reads_numbers(self):
        text = chattts_engine.normalize_tts_text(
            "2025年1月1日，增长12.5%，共有3,200个样本和第6组。"
        )

        self.assertIn("二零二五年一月一日", text)
        self.assertIn("百分之十二点五", text)
        self.assertIn("三千二百个样本", text)
        self.assertIn("第六组", text)

    def test_synthesize_passes_normalized_text_to_chattts(self):
        fake_chat = FakeChat()
        chattts_engine._CHAT_MODEL = fake_chat

        with patch.object(chattts_engine, "_write_wav"), patch.object(chattts_engine, "_wav_to_mp3", side_effect=lambda _wav, out: out):
            chattts_engine.synthesize_text("今天是2025年1月1日", "date.mp3", role="narrator")

        self.assertEqual(fake_chat.calls[0][0], ["今天是二零二五年一月一日"])


if __name__ == "__main__":
    unittest.main()
