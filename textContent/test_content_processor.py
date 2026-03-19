# -*- coding: utf-8 -*-
import unittest
import os
import tempfile
from unittest.mock import patch, MagicMock

# 确保能正确引入待测模块
import sys
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
import content_processor

class TestContentProcessor(unittest.TestCase):
    def setUp(self):
        # 创建一个临时目录和文件用于测试
        self.test_dir = tempfile.TemporaryDirectory()
        self.mock_crawled_dir = os.path.join(self.test_dir.name, "crawler_data")
        os.makedirs(self.mock_crawled_dir)
        
        # 准备一个爬虫格式的测试MD文件
        self.sample_md_filepath = os.path.join(self.mock_crawled_dir, "test_news.md")
        sample_md_content = """# 【Test Keyword】 每日新闻追踪

> **生成日期**: 2026-03-18

---

### 1. 测试用的第一条新闻

- **爬取日期**: 2026-03-18
- **关键词**: Test
- **原链接**: [http://example.com/1](http://example.com/1)

**新闻正文**:

这是第一条新闻的测试正文内容。

---

### 2. 测试用的第二条新闻

- **爬取日期**: 2026-03-18
- **关键词**: Test
- **原链接**: [http://example.com/2](http://example.com/2)

**新闻正文**:

这是第二条新闻的测试正文内容，这里有一些文本。

---
"""
        with open(self.sample_md_filepath, 'w', encoding='utf-8') as f:
            f.write(sample_md_content)
            
        # 预设输出文件
        self.output_md_file = os.path.join(self.test_dir.name, "processed_output.md")
        self.brief_md_file = os.path.join(self.test_dir.name, "brief_output.md")

    def tearDown(self):
        # 销毁临时目录
        self.test_dir.cleanup()

    @patch('content_processor.call_llm')
    def test_is_theme_matched(self, mock_call_llm):
        # 测试匹配情况
        mock_call_llm.return_value = "是"
        self.assertTrue(content_processor.is_theme_matched("一段符合主题的内容"))
        
        # 测试不匹配情况
        mock_call_llm.return_value = "否"
        self.assertFalse(content_processor.is_theme_matched("一段与主题完全无关的内容"))
        
    def test_read_crawled_content(self):
        # 测试能否正确解析 MD 文件中的新闻
        news_items = content_processor.read_crawled_content(self.mock_crawled_dir)
        
        self.assertEqual(len(news_items), 2)
        self.assertEqual(news_items[0]['title'], "测试用的第一条新闻")
        self.assertEqual(news_items[0]['link'], "http://example.com/1")
        self.assertIn("这是第一条新闻的测试正文内容", news_items[0]['content'])

    def test_get_previously_written_content_text(self):
        # 不存在时应返回空字符串
        text = content_processor.get_previously_written_content_text("not_exist.md")
        self.assertEqual(text, "")
        
        # 存在时返回内容
        test_file = os.path.join(self.test_dir.name, "exist.md")
        with open(test_file, 'w', encoding='utf-8') as f:
            f.write("Some previous content")
            
        text = content_processor.get_previously_written_content_text(test_file)
        self.assertEqual(text, "Some previous content")

    @patch('content_processor.call_llm')
    def test_is_duplicate(self, mock_call_llm):
        news = {"title": "新的新闻标题", "content": "新内容"}
        
        # 内容为空时，一定不重复
        self.assertFalse(content_processor.is_duplicate(news, ""))
        
        # 标题就在上下文中，直接判定重复
        self.assertTrue(content_processor.is_duplicate(news, "### 1. 新的新闻标题\n"))
        
        # 标题不在上下文中，需大模型判断 - 不重复
        mock_call_llm.return_value = "否"
        self.assertFalse(content_processor.is_duplicate(news, "### 1. 其他新闻\n"))
        
        # 标题不在上下文中，需大模型判断 - 重复
        mock_call_llm.return_value = "是"
        self.assertTrue(content_processor.is_duplicate(news, "### 1. 相似的新闻\n"))

    def test_write_to_md(self):
        news = {"title": "写入测试", "link": "http://write.com", "content": "写入的正文"}
        content_processor.write_to_md(news, self.output_md_file, 1)
        
        self.assertTrue(os.path.exists(self.output_md_file))
        with open(self.output_md_file, 'r', encoding='utf-8') as f:
            text = f.read()
            self.assertIn("### 1. 写入测试", text)
            self.assertIn("http://write.com", text)
            self.assertIn("写入的正文", text)

    @patch('content_processor.call_llm')
    def test_generate_briefs(self, mock_call_llm):
        # 没有输入文件时
        content_processor.generate_briefs("not_exist.md", self.brief_md_file)
        self.assertFalse(os.path.exists(self.brief_md_file))
        
        # 正常生成情况
        with open(self.output_md_file, 'w', encoding='utf-8') as f:
            f.write("这里是写入的一些新闻正文内容...")
            
        mock_call_llm.return_value = "1. 简讯一\n2. 简讯二"
        
        content_processor.generate_briefs(self.output_md_file, self.brief_md_file)
        
        self.assertTrue(os.path.exists(self.brief_md_file))
        with open(self.brief_md_file, 'r', encoding='utf-8') as f:
            text = f.read()
            self.assertIn("每日新闻简讯", text)
            self.assertIn("1. 简讯一", text)

if __name__ == '__main__':
    unittest.main()
