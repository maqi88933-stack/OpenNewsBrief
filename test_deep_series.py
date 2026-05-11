import asyncio
import os
import shutil
import sys
import tempfile
import types
import unittest
from unittest.mock import MagicMock, patch

import deep_series


class FakeCommunicate:
    calls = []

    def __init__(self, text, voice, rate=None):
        self.text = text
        self.voice = voice
        self.rate = rate
        self.output_path = ""
        FakeCommunicate.calls.append(self)

    async def save(self, output_path):
        self.output_path = output_path


class TestDeepSeries(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.config_path = os.path.join(self.tmpdir, "deep_series_config.json")
        self.original_tts_engine = deep_series.DEEP_TTS_ENGINE
        FakeCommunicate.calls.clear()

    def tearDown(self):
        deep_series.DEEP_TTS_ENGINE = self.original_tts_engine
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_load_config_creates_ai_future_series(self):
        config = deep_series.load_config(self.config_path)

        self.assertTrue(config["series"][0]["title"])
        self.assertTrue(config["series"][0]["episodes"])
        self.assertTrue(os.path.exists(self.config_path))

    def test_save_config_round_trips_new_series_and_episode(self):
        config = {
            "series": [{
                "title": "新系列",
                "description": "测试",
                "episodes": [{"title": "新主题", "question": "为什么？"}],
            }]
        }

        deep_series.save_config(config, self.config_path)
        loaded = deep_series.load_config(self.config_path)

        self.assertEqual(loaded, config)

    def test_parse_dialogue_script_extracts_speakers(self):
        script = "女：为什么 Agent 会重构软件？\n男：因为软件会从按钮变成目标驱动。\n旁白：结尾总结。"

        segments = deep_series.parse_dialogue_script(script)

        self.assertEqual([item["speaker"] for item in segments], ["female", "male", "narrator"])
        self.assertIn("目标驱动", segments[1]["text"])

    def test_clean_script_output_strips_markdown_rule_header(self):
        raw = "---\n\n女：你有没有想过，如果AI不只是聊天，而是能直接替你干活，那该多爽？\n\n男：比如呢？"

        cleaned = deep_series.clean_script_output(raw)

        self.assertFalse(cleaned.startswith("---"))
        self.assertEqual(
            cleaned,
            "女：你有没有想过，如果AI不只是聊天，而是能直接替你干活，那该多爽？\n\n男：比如呢？",
        )

    def test_save_tts_uses_chattts_for_gendered_dialogue_when_chattts_enabled(self):
        fake_chattts = types.SimpleNamespace(synthesize_text=MagicMock())
        fake_edge_tts = types.SimpleNamespace(Communicate=FakeCommunicate)
        deep_series.DEEP_TTS_ENGINE = "chattts"

        with patch.dict(sys.modules, {
            "audioContent.chattts_engine": fake_chattts,
            "edge_tts": fake_edge_tts,
        }):
            asyncio.run(deep_series._save_tts("第一句女声", "female.mp3", deep_series.FEMALE_VOICE, role="female"))
            asyncio.run(deep_series._save_tts("第一句男声", "male.mp3", deep_series.MALE_VOICE, role="male"))

        fake_chattts.synthesize_text.assert_any_call("第一句女声", "female.mp3", role="female")
        fake_chattts.synthesize_text.assert_any_call("第一句男声", "male.mp3", role="male")
        self.assertEqual(fake_chattts.synthesize_text.call_count, 2)
        self.assertEqual(FakeCommunicate.calls, [])

    def test_save_tts_keeps_chattts_for_narrator(self):
        fake_chattts = types.SimpleNamespace(synthesize_text=MagicMock())
        fake_edge_tts = types.SimpleNamespace(Communicate=FakeCommunicate)
        deep_series.DEEP_TTS_ENGINE = "chattts"

        with patch.dict(sys.modules, {
            "audioContent.chattts_engine": fake_chattts,
            "edge_tts": fake_edge_tts,
        }):
            asyncio.run(deep_series._save_tts("旁白总结", "narrator.mp3", deep_series.MALE_VOICE, role="narrator"))

        fake_chattts.synthesize_text.assert_called_once_with("旁白总结", "narrator.mp3", role="narrator")
        self.assertEqual(FakeCommunicate.calls, [])

    def test_mark_episode_generated_records_video_state(self):
        config = deep_series.load_config(self.config_path)
        series_title = config["series"][0]["title"]
        episode_title = config["series"][0]["episodes"][0]["title"]
        episode = config["series"][0]["episodes"][0]
        episode["published"] = True
        episode["published_at"] = "2026-05-11 10:00:00"
        result = {"video_path": "D:\\output\\demo.mp4", "script_path": "script.md"}

        deep_series.mark_episode_generated(config, series_title, episode_title, result)

        episode = config["series"][0]["episodes"][0]
        self.assertTrue(episode["generated"])
        self.assertEqual(episode["video_path"], "D:\\output\\demo.mp4")
        self.assertEqual(episode["script_path"], "script.md")
        self.assertFalse(episode["published"])
        self.assertEqual(episode["published_at"], "")
        self.assertIn("generated_at", episode)

    @patch("deep_series.call_llm", return_value='{"title":"深度标题","desc":"深度简介","tags":"AI,科技"}')
    def test_generate_publish_assets_writes_ai_metadata(self, _llm):
        script_path = os.path.join(self.tmpdir, "script.md")
        with open(script_path, "w", encoding="utf-8") as f:
            f.write("女：AI 为什么会替代搜索？\n男：用户要的是答案。")
        result = {"script_path": script_path, "video_path": os.path.join(self.tmpdir, "demo.mp4")}
        series = {"title": "AI未来三年系列"}
        episode = {"title": "AI 为什么会替代搜索？"}

        assets = deep_series.generate_publish_assets(series, episode, result)

        self.assertEqual(assets["title"], "深度标题")
        self.assertEqual(assets["desc"], "深度简介")
        self.assertEqual(assets["tags"], "AI,科技")
        self.assertTrue(os.path.exists(os.path.join(self.tmpdir, "publish_assets.json")))

    @patch("deep_series.step_video", return_value="demo.mp4")
    @patch("deep_series.create_deep_slide_images", return_value=["cover.png", "section.png"])
    @patch("deep_series.convert_dialogue_to_audio", return_value="demo.mp3")
    @patch("deep_series.call_llm", side_effect=[
        "研究报告",
        "事实核查：通过",
        "反方审稿：通过",
        "结构审核：通过",
        "女：AI 为什么会替代搜索？\n男：因为用户要答案，不只是链接。",
        "写稿意见：已采纳审稿建议",
    ])
    @patch("deep_series.collect_research_sources", return_value=[
        {"title": "Source 1", "link": "https://example.com/1", "content": "AI search source"},
    ])
    def test_run_episode_pipeline_returns_review_artifacts(self, _sources, _llm, _audio, _slides, _video):
        episode = {"title": "AI 为什么会替代搜索？", "question": "AI 为什么会替代搜索？"}
        series = {"title": "AI未来三年系列", "description": "测试系列"}

        result = deep_series.run_episode_pipeline(series, episode, base_dir=self.tmpdir)

        self.assertEqual(result["video_path"], "")
        self.assertEqual(result["audio_path"], "")
        self.assertTrue(result["script_path"].endswith("script.md"))
        self.assertTrue(os.path.exists(result["research_path"]))
        self.assertTrue(os.path.exists(result["script_notes_path"]))
        self.assertTrue(os.path.exists(result["agent_log_path"]))
        _audio.assert_not_called()
        _slides.assert_not_called()
        _video.assert_not_called()


if __name__ == "__main__":
    unittest.main()
