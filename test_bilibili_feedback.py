import json
import os
import tempfile
import unittest
from unittest.mock import Mock, patch

from crawler.bilibili_feedback import (
    build_bilibili_detail_metric_row,
    extract_bilibili_metric_rows,
    log_bilibili_detail_metric_row,
    scrape_bilibili_api_metrics,
)


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

    def test_scrape_bilibili_api_metrics_writes_member_archive_rows(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cookie_path = os.path.join(tmpdir, "cookie.json")
            output_path = os.path.join(tmpdir, "metrics.json")
            with open(cookie_path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "cookie_info": {
                            "cookies": [
                                {"name": "SESSDATA", "value": "session"},
                                {"name": "DedeUserID", "value": "1274586220"},
                            ]
                        },
                        "token_info": {"mid": 1274586220},
                    },
                    f,
                    ensure_ascii=False,
                )
            response = Mock()
            response.status_code = 200
            response.raise_for_status = Mock()
            response.json.return_value = {
                "code": 0,
                "data": {
                    "page": {"pn": 1, "ps": 10, "count": 1},
                    "arc_audits": [
                        {
                            "Archive": {"title": "HOYA站上光刻入口", "duration": 207},
                            "stat": {"view": 48, "like": 5, "reply": 1, "share": 2},
                        }
                    ],
                },
            }

            with patch("crawler.bilibili_feedback._requests_get", return_value=response) as mock_get:
                # 这个路径必须写入真实接口返回的指标行，而不是靠本地质量数据兜底。
                result = scrape_bilibili_api_metrics(output_path=output_path, cookie_path=cookie_path)

            self.assertEqual(result["metric_count"], 1)
            self.assertEqual(result["source"], "bilibili_member_archives")
            mock_get.assert_called_once()
            with open(output_path, "r", encoding="utf-8") as f:
                metrics = json.load(f)
            self.assertEqual(metrics["videos"][0]["publish_title"], "HOYA站上光刻入口")
            self.assertEqual(metrics["videos"][0]["views"], 48)
            self.assertEqual(metrics["videos"][0]["likes"], 5)

    def test_build_bilibili_detail_metric_row_only_keeps_existing_detail_fields(self):
        archive = {
            "aid": 116690805395778,
            "bvid": "BV19i7f6eELy",
            "title": "3M凭什么卡住芯片良率？",
        }
        overview_payloads = [
            {
                "data": {
                    "overview": {
                        "play": 93,
                        "like": 1,
                        "danmaku": 0,
                        "reply": 0,
                        "share": 0,
                    }
                }
            }
        ]
        play_payloads = [
            {
                "data": {
                    "play_analysis": {
                        "cover_click_rate": "2星",
                        "interaction_rate": "0%",
                    }
                }
            }
        ]

        row = build_bilibili_detail_metric_row(archive, overview_payloads, play_payloads)

        self.assertEqual(row["source"], "bilibili_detail_page")
        self.assertEqual(row["metric_sections"], ["data_overview", "play_analysis"])
        self.assertEqual(row["views"], 93)
        self.assertEqual(row["likes"], 1)
        self.assertEqual(row["danmaku"], 0)
        self.assertEqual(row["comments"], 0)
        self.assertEqual(row["shares"], 0)
        self.assertEqual(row["click_rate"], "2星")
        self.assertEqual(row["interaction_rate"], "0%")
        self.assertNotIn("avg_view_seconds", row)
        self.assertNotIn("completion_rate", row)
        self.assertNotIn("play_follower_rate", row)

    def test_build_bilibili_detail_metric_row_reads_real_diagnose_fields_and_skips_not_ready(self):
        archive = {
            "aid": 116690805395778,
            "bvid": "BV19i7f6eELy",
            "title": "3M凭什么卡住芯片良率？",
        }
        overview_payloads = [{
            "data": {
                "stat": {
                    "play": 93,
                    "like": 1,
                    "dm": 0,
                    "comment": 0,
                    "share": 0,
                    "fav": 0,
                    "coin": 0,
                    "fan": 0,
                    "unfollow": 0,
                }
            }
        }]
        play_payloads = [{
            "data": {
                "list": [{
                    "bvid": "BV19i7f6eELy",
                    "stat": {
                        "not_ready_field": ["full_play_ratio", "avg_play_time", "play_trans_fan_rate"],
                        "tm_rate": 414,
                        "full_play_ratio": 0,
                        "avg_play_time": 0,
                        "interact_rate": 0,
                        "play_trans_fan_rate": 0,
                    },
                }, {
                    "bvid": "BV12GVm62Ec5",
                    "stat": {
                        "full_play_ratio": 4887,
                        "avg_play_time": 41,
                        "play_trans_fan_rate": 9,
                    },
                }]
            }
        }]

        from crawler.bilibili_feedback import _filter_detail_payload_by_bvid
        filtered_play_payloads = [_filter_detail_payload_by_bvid(play_payloads[0], archive["bvid"])]
        row = build_bilibili_detail_metric_row(archive, overview_payloads, filtered_play_payloads)

        # B站详情接口里 not_ready_field 标记的字段不要写入报告，避免把未就绪数据当 0 分析。
        self.assertEqual(row["views"], 93)
        self.assertEqual(row["likes"], 1)
        self.assertEqual(row["click_rate"], 414)
        self.assertEqual(row["interaction_rate"], 0)
        self.assertNotIn("completion_rate", row)
        self.assertNotIn("avg_view_seconds", row)
        self.assertNotIn("play_follower_rate", row)

    def test_log_bilibili_detail_metric_row_prints_collected_json_line(self):
        row = {
            "source": "bilibili_detail_page",
            "publish_title": "3M凭什么卡住芯片良率？",
            "views": 93,
            "likes": 1,
            "metric_sections": ["data_overview"],
        }

        with patch("builtins.print") as mock_print:
            log_bilibili_detail_metric_row(row, index=2, total=5)

        # 数据回流界面日志要做到采集一条打印一条，并且后半段 JSON 可直接复制核对。
        mock_print.assert_called_once()
        message = mock_print.call_args.args[0]
        self.assertTrue(message.startswith("[B站数据回流] 已采集第 2/5 条详情数据："))
        payload = json.loads(message.split("：", 1)[1])
        self.assertEqual(payload["publish_title"], "3M凭什么卡住芯片良率？")
        self.assertEqual(payload["views"], 93)
        self.assertEqual(mock_print.call_args.kwargs["flush"], True)


if __name__ == "__main__":
    unittest.main()
