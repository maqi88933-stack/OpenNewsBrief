import unittest
from unittest.mock import patch

import main


class TestMainFlow(unittest.TestCase):
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
        mock_video.assert_called_once_with("demo.mp3", "爆款标题")


if __name__ == "__main__":
    unittest.main()
