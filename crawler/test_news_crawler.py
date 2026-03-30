# -*- coding: utf-8 -*-
import unittest
import os
import datetime
import sys
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from news_crawler import parse_and_deduplicate, save_to_markdown_file, run_crawler

class TestNewsCrawler(unittest.TestCase):
    def setUp(self):
        # 准备工作目录和测试所用的关键变量
        self.keyword = "Test Keyword"
        self.today_str = datetime.date.today().strftime("%Y-%m-%d")
        self.script_dir = os.path.dirname(os.path.abspath(__file__))
        self.date_dir = os.path.join(self.script_dir, self.today_str)
        self.safe_keyword = self.keyword.replace(" ", "")
        
        # 预期生成的文件路径
        self.filepath = os.path.join(self.date_dir, f"{self.safe_keyword}.md")

        # 确保环境干净
        if os.path.exists(self.filepath):
            os.remove(self.filepath)

    def test_parse_and_deduplicate(self):
        # 测试 1: 解析 XML 并对当日新闻正确去重
        today_rfc2822 = datetime.datetime.now().astimezone().strftime('%a, %d %b %Y %H:%M:%S %z')
        yesterday_rfc2822 = (datetime.datetime.now().astimezone() - datetime.timedelta(days=1)).strftime('%a, %d %b %Y %H:%M:%S %z')

        mock_xml = f"""<?xml version="1.0" encoding="utf-8" ?>
        <rss version="2.0">
            <channel>
                <!-- 当天新闻 -->
                <item>
                    <title>Today News 1</title>
                    <link>http://example.com/1</link>
                    <pubDate>{today_rfc2822}</pubDate>
                </item>
                <!-- 当天重复新闻 -->
                <item>
                    <title>Today News 1</title>
                    <link>http://example.com/1</link>
                    <pubDate>{today_rfc2822}</pubDate>
                </item>
                <!-- 昨天新闻 -->
                <item>
                    <title>Yesterday News</title>
                    <link>http://example.com/old</link>
                    <pubDate>{yesterday_rfc2822}</pubDate>
                </item>
            </channel>
        </rss>
        """
        
        results, expired_count = parse_and_deduplicate(mock_xml, self.keyword)
        
        self.assertEqual(len(results), 1, "去重后应该保留1条最近12小时内的新闻")
        self.assertEqual(expired_count, 1, "过时的旧新闻（昨天那条）应该被丢弃并统计")
        self.assertEqual(results[0]["title"], "Today News 1", "提取的新闻标题不对")
        self.assertEqual(results[0]["link"], "http://example.com/1", "提取的新闻链接不对")
        self.assertTrue("content" in results[0], "结果中必须包含 content 正文本段")

    def test_save_to_markdown_file(self):
        # 测试 2: 验证文件和文件夹生成逻辑与内容完整度验证
        mock_news = [
            {
                "title": "测试新闻标题1",
                "link": "http://test.com/link1",
                "content": "<模拟完整的正文段落1>\n\n<模拟完整的正文段落2>",
                "date": self.today_str
            }
        ]
        
        save_to_markdown_file(self.keyword, mock_news, 5)
        
        self.assertTrue(os.path.exists(self.date_dir), f"目标日期文件夹应该存在: {self.date_dir}")
        self.assertTrue(os.path.isdir(self.date_dir), f"路径应该是一个文件夹: {self.date_dir}")
        self.assertTrue(os.path.exists(self.filepath), f"目标文件应该存在: {self.filepath}")
        
        with open(self.filepath, 'r', encoding='utf-8') as f:
            content = f.read()
            self.assertIn(self.keyword, content, "应该包含配置的关键词")
            self.assertIn("模拟完整的正文段落", content, "新的 content 字段内容应该被正确写入")
            self.assertIn("已忽略过期新闻", content, "因为传入了5个过时记录，应该包含对应的统计文案")
            self.assertIn(self.today_str, content, "应该包含日期声明")
            
    def tearDown(self):
        if os.path.exists(self.filepath):
            os.remove(self.filepath)
        if os.path.exists(self.date_dir) and not os.listdir(self.date_dir):
            os.rmdir(self.date_dir)

if __name__ == '__main__':
    print("="*40)
    print(" 开始全量自动化测试: 包含爬取、解析、正文提取和写入逻辑 ")
    print("="*40)
    
    # 建立测试套件
    suite = unittest.TestLoader().loadTestsFromTestCase(TestNewsCrawler)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    
    # 如果核心单元测试均通过，则直接拉起一次全量的真实爬虫运行作为集成环境的冒烟测试
    if result.wasSuccessful():
        print("\n[+] 单元测试通过。准备开始执行一次真实环境的全量冒烟测试...\n")
        run_crawler()
