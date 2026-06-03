import json
import unittest

from crawler.bilibili_feedback import extract_bilibili_metric_rows


class TestBilibiliFeedback(unittest.TestCase):
    def test_extract_bilibili_metric_rows_reads_nested_article_stats(self):
        payload = {
            "data": {
                "list": [
                    {
                        "title": "HOYA站上光刻入口",
                        "stat": {"view": 320, "like": 12, "reply": 3, "share": 2},
                    }
                ]
            }
        }

        rows = extract_bilibili_metric_rows(payload)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["episode"], "HOYA站上光刻入口")
        self.assertEqual(rows[0]["publish_title"], "HOYA站上光刻入口")
        self.assertEqual(rows[0]["views"], 320)
        self.assertEqual(rows[0]["likes"], 12)
        self.assertEqual(rows[0]["comments"], 3)
        self.assertEqual(rows[0]["shares"], 2)

    def test_extract_bilibili_metric_rows_accepts_json_text(self):
        payload = json.dumps(
            {
                "data": {
                    "archives": [
                        {
                            "archive_title": "AI时代的隐形地基",
                            "view": "1,024",
                            "likes": 35,
                            "comments": 6,
                            "shares": 4,
                        }
                    ]
                }
            },
            ensure_ascii=False,
        )

        rows = extract_bilibili_metric_rows(payload)

        self.assertEqual(rows[0]["episode"], "AI时代的隐形地基")
        self.assertEqual(rows[0]["views"], 1024)
        self.assertEqual(rows[0]["likes"], 35)
        self.assertEqual(rows[0]["comments"], 6)
        self.assertEqual(rows[0]["shares"], 4)

    def test_extract_bilibili_metric_rows_reads_archive_child_title(self):
        payload = {
            "data": {
                "list": [
                    {
                        "archive": {"title": "嵌套标题稿件"},
                        "stat": {"view": 88, "like": 5, "reply": 1},
                    }
                ]
            }
        }

        rows = extract_bilibili_metric_rows(payload)

        self.assertEqual(rows[0]["episode"], "嵌套标题稿件")
        self.assertEqual(rows[0]["views"], 88)
        self.assertEqual(rows[0]["likes"], 5)
        self.assertEqual(rows[0]["comments"], 1)


if __name__ == "__main__":
    unittest.main()
