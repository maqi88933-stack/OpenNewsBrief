import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

import deep_series


class TestDeepSeries(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.config_path = os.path.join(self.tmpdir, "deep_series_config.json")

    def tearDown(self):
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

    def test_mark_episode_generated_records_video_state(self):
        config = deep_series.load_config(self.config_path)
        series_title = config["series"][0]["title"]
        episode_title = config["series"][0]["episodes"][0]["title"]
        result = {"video_path": "D:\\output\\demo.mp4", "script_path": "script.md"}

        deep_series.mark_episode_generated(config, series_title, episode_title, result)

        episode = config["series"][0]["episodes"][0]
        self.assertTrue(episode["generated"])
        self.assertEqual(episode["video_path"], "D:\\output\\demo.mp4")
        self.assertEqual(episode["script_path"], "script.md")
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
