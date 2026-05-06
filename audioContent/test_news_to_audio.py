# -*- coding: utf-8 -*-
"""
news_to_audio.py 的单元测试
"""

import os
import sys
import unittest
import uuid
from unittest.mock import AsyncMock, patch, MagicMock

# 确保能引入被测模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import news_to_audio


class TestCleanMarkdown(unittest.TestCase):
    """测试 Markdown 清洗函数"""

    def test_removes_headings(self):
        """去除标题 # 号"""
        result = news_to_audio.clean_markdown("# 每日新闻简讯")
        self.assertNotIn("#", result)
        self.assertIn("每日新闻简讯", result)

    def test_removes_bold(self):
        """去除加粗标记"""
        result = news_to_audio.clean_markdown("**主题**: AI大模型")
        self.assertNotIn("**", result)
        self.assertIn("AI大模型", result)

    def test_removes_blockquote(self):
        """去除引用块"""
        result = news_to_audio.clean_markdown("> **主题**: AI前沿")
        self.assertNotIn(">", result)

    def test_removes_horizontal_rule(self):
        """去除水平线"""
        result = news_to_audio.clean_markdown("内容\n---\n更多内容")
        self.assertNotIn("---", result)

    def test_removes_links(self):
        """去除 Markdown 链接，保留文字"""
        result = news_to_audio.clean_markdown("[示例链接](http://example.com)")
        self.assertNotIn("http://", result)
        self.assertIn("示例链接", result)

    def test_collapses_blank_lines(self):
        """多余空行应被压缩"""
        result = news_to_audio.clean_markdown("第一段\n\n\n\n第二段")
        self.assertNotIn("\n\n\n", result)


class TestExtractDateStr(unittest.TestCase):
    """测试日期字符串提取"""

    def test_from_filename(self):
        result = news_to_audio.extract_date_str("/some/path/news_brief_2026-03-18.md")
        self.assertEqual(result, "2026年03月18日")

    def test_from_parent_dir(self):
        result = news_to_audio.extract_date_str("/textContent/2026-03-18/news.md")
        self.assertEqual(result, "2026年03月18日")

    def test_english_language(self):
        """测试英文语言标识下的日期提取格式"""
        result = news_to_audio.extract_date_str("/textContent/2026-03-18/news.md", language="English")
        self.assertEqual(result, "March 18, 2026")


class TestGetOutputPath(unittest.TestCase):
    """测试输出路径生成"""

    def test_date_dir_created(self):
        with patch.object(news_to_audio.os, "makedirs") as mock_makedirs:
            tmp = os.getcwd()
            path = news_to_audio.get_output_path(
                "/textContent/2026-03-18/news_brief_2026-03-18.md",
                tmp
            )
            self.assertTrue(path.endswith(".mp3"))
            self.assertIn("2026-03-18", path)
            mock_makedirs.assert_called_once()


class TestReadMdFile(unittest.TestCase):
    """测试 MD 文件读取"""

    def test_file_not_found(self):
        with self.assertRaises(FileNotFoundError):
            news_to_audio.read_md_file("/not/exist/file.md")

    def test_reads_content(self):
        import tempfile
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8",
                                         suffix=".md", delete=False) as f:
            f.write("# 测试内容")
            tmp_path = f.name
        try:
            content = news_to_audio.read_md_file(tmp_path)
            self.assertEqual(content, "# 测试内容")
        finally:
            os.unlink(tmp_path)


class TestBuildTtsText(unittest.TestCase):
    """测试 TTS 文本构建"""

    def test_contains_intro_and_outro(self):
        result = news_to_audio.build_tts_text("1. 一条新闻内容", "2026年03月18日", title="AI 每日简报")
        self.assertIn("欢迎收听", result)
        self.assertIn("AI 每日简报", result)
        self.assertIn("2026年03月18日", result)
        self.assertIn("感谢收听", result)

    def test_contains_cleaned_content(self):
        result = news_to_audio.build_tts_text("**重要新闻**内容", "2026年03月18日")
        self.assertIn("重要新闻", result)
        self.assertNotIn("**", result)

    def test_custom_title(self):
        """测试自定义主题标题"""
        result = news_to_audio.build_tts_text("1. 内容", "2026年03月18日", title="工程机器 破碎机 每日简讯")
        self.assertIn("工程机器 破碎机 每日简讯", result)


class TestSegmentedAudio(unittest.TestCase):
    def test_build_tts_segments_splits_intro_news_and_outro(self):
        segments = news_to_audio.build_tts_segments(
            "1. OpenAI 发布新功能\n2. Claude Code 更新",
            "2026年03月18日",
            title="AI 每日简报",
        )

        self.assertEqual([item["role"] for item in segments], ["overview", "news", "news", "outro"])
        self.assertEqual(segments[0]["slide_index"], 0)
        self.assertEqual(segments[1]["slide_index"], 1)
        self.assertEqual(segments[2]["slide_index"], 2)
        self.assertEqual(segments[3]["slide_index"], 2)

    def test_write_timing_file_records_real_segment_durations(self):
        import json

        audio_path = os.path.join(os.getcwd(), f"brief_{uuid.uuid4().hex}.mp3")
        timing_path = audio_path + ".timing.json"
        try:
            segments = [
                {"role": "overview", "slide_index": 0, "duration": 1.5, "text": "intro"},
                {"role": "news", "slide_index": 1, "duration": 3.0, "text": "news"},
                {"role": "outro", "slide_index": 1, "duration": 0.8, "text": "outro"},
            ]
            created_path = news_to_audio.write_timing_file(audio_path, segments)

            with open(created_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        finally:
            if os.path.exists(timing_path):
                os.remove(timing_path)

        self.assertEqual(data["audio_path"], audio_path)
        self.assertAlmostEqual(data["total_duration"], 5.3)
        self.assertEqual(data["segments"][1]["slide_index"], 1)

    def test_convert_md_to_audio_generates_each_segment_and_timing(self):
        output_path = os.path.join(os.getcwd(), f"brief_{uuid.uuid4().hex}.mp3")

        async def fake_convert_to_audio(_text, _path, is_english=False):
            return None

        with patch.object(news_to_audio, "read_md_file", return_value="1. 第一条新闻\n2. 第二条新闻"):
            with patch.object(news_to_audio, "get_output_path", return_value=output_path):
                with patch.object(news_to_audio.os, "makedirs"):
                    with patch.object(news_to_audio, "convert_to_audio", side_effect=fake_convert_to_audio) as mock_convert:
                        with patch.object(news_to_audio, "get_audio_duration", side_effect=[1.0, 2.0, 3.0, 0.5]):
                            with patch.object(news_to_audio, "concat_audio_files", return_value=output_path) as mock_concat:
                                with patch.object(news_to_audio, "write_timing_file", return_value=output_path + ".timing.json") as mock_timing:
                                    result = news_to_audio.convert_md_to_audio(
                                        "news_brief_2026-03-18.md",
                                        os.getcwd(),
                                        title="AI 每日简报",
                                    )

        self.assertEqual(result, output_path)
        self.assertEqual(mock_convert.call_count, 4)
        self.assertEqual(len(mock_concat.call_args.args[0]), 4)
        timing_segments = mock_timing.call_args.args[1]
        self.assertEqual([item["duration"] for item in timing_segments], [1.0, 2.0, 3.0, 0.5])


if __name__ == "__main__":
    unittest.main()
