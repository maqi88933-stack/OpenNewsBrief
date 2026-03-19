# -*- coding: utf-8 -*-
"""
news_to_audio.py 的单元测试
"""

import os
import sys
import unittest
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


class TestGetOutputPath(unittest.TestCase):
    """测试输出路径生成"""

    def test_date_dir_created(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = news_to_audio.get_output_path(
                "/textContent/2026-03-18/news_brief_2026-03-18.md",
                tmp
            )
            self.assertTrue(path.endswith(".mp3"))
            self.assertIn("2026-03-18", path)
            self.assertTrue(os.path.isdir(os.path.dirname(path)))


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
        result = news_to_audio.build_tts_text("1. 一条新闻内容", "2026年03月18日")
        self.assertIn("欢迎收听", result)
        self.assertIn("2026年03月18日", result)
        self.assertIn("感谢收听", result)

    def test_contains_cleaned_content(self):
        result = news_to_audio.build_tts_text("**重要新闻**内容", "2026年03月18日")
        self.assertIn("重要新闻", result)
        self.assertNotIn("**", result)


if __name__ == "__main__":
    unittest.main()
