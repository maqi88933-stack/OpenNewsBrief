import unittest
import os
import uuid
from unittest.mock import patch

import main


class TestMainFlow(unittest.TestCase):
    def test_estimate_news_slide_durations_matches_overview_and_news_count(self):
        brief_path = "demo_brief.md"
        topic = {"title": "AI 每日简报"}
        content = "1. OpenAI 发布新功能\n2. Claude Code 更新开发能力\n"

        with patch("builtins.open", unittest.mock.mock_open(read_data=content)):
            with patch.object(main.os.path, "exists", return_value=True):
                durations = main.estimate_news_slide_durations(brief_path, topic, 30.0)

        self.assertEqual(len(durations), 3)
        self.assertAlmostEqual(sum(durations), 30.0)
        self.assertGreater(durations[0], 0)

    def test_load_slide_durations_from_timing_uses_real_segment_durations(self):
        import json

        audio_path = os.path.join(os.getcwd(), f"brief_{uuid.uuid4().hex}.mp3")
        timing_path = audio_path + ".timing.json"
        try:
            with open(timing_path, "w", encoding="utf-8") as f:
                json.dump({
                    "segments": [
                        {"role": "overview", "slide_index": 0, "duration": 1.2},
                        {"role": "news", "slide_index": 1, "duration": 3.4},
                        {"role": "outro", "slide_index": 1, "duration": 0.6},
                    ]
                }, f)

            durations = main.load_slide_durations_from_timing(audio_path, 2)
        finally:
            if os.path.exists(timing_path):
                os.remove(timing_path)

        self.assertEqual(durations, [1.2, 4.0])

    @patch.object(main, "step_video", return_value="demo.mp4")
    @patch.object(main, "ensure_cover_image", return_value="Gemini_Generated_Image.png")
    @patch.object(main, "step_video_title", return_value="爆款标题")
    @patch.object(main, "step_audio", return_value="demo.mp3")
    @patch.object(main, "step_cover_prompt", return_value="cover prompt")
    @patch.object(main, "step_process", return_value="demo.md")
    @patch.object(main, "step_crawl", return_value="crawler_dir")
    def test_run_topic_pipeline_outputs_video(
        self,
        _mock_crawl,
        _mock_process,
        _mock_cover_prompt,
        _mock_audio,
        mock_video_title,
        mock_cover_image,
        mock_video,
    ):
        topic = {
            "title": "AI 每日简报",
            "theme": "AI",
            "keywords": ["OpenAI 最新消息"],
            "language": "zh-CN",
        }

        with patch.object(main.os.path, "exists", return_value=True):
            result = main.run_topic_pipeline(topic)

        self.assertEqual(result["brief_path"], "demo.md")
        self.assertEqual(result["audio_path"], "demo.mp3")
        self.assertEqual(result["video_path"], "demo.mp4")
        mock_video_title.assert_called_once_with("demo.md", topic)
        mock_cover_image.assert_called_once_with("demo.md", "demo.mp3", topic)
        mock_video.assert_called_once_with("demo.mp3", "爆款标题", "demo.md", topic)


if __name__ == "__main__":
    unittest.main()
