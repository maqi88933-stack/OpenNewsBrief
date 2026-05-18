import io
import json
import unittest
from unittest.mock import patch

import ui_worker


class TestUiWorker(unittest.TestCase):
    def test_main_cli_requires_topic_argument(self):
        # 这里直接验证最外层参数兜底，确保 UI 子进程在空参数时能稳定返回错误码。
        # 这样主界面即使误调用了 worker，也能拿到一致的失败语义，而不是抛出未处理异常。
        stderr = io.StringIO()
        with patch("sys.stderr", stderr):
            code = ui_worker.main_cli([])

        self.assertEqual(code, 2)
        self.assertIn("请传入主题标题", stderr.getvalue())

    @patch("ui_worker.deep_series.run_episode_by_titles", return_value={"script_path": "demo.md"})
    @patch("builtins.print")
    def test_main_cli_dispatches_deep_mode(self, mock_print, mock_run_episode):
        # 深度写稿模式必须固定走 deep_series.run_episode_by_titles。
        # 这个测试同时校验返回值会带上统一的结果前缀，保证主进程能正确解析结果。
        code = ui_worker.main_cli(["--deep", "AI未来三年系列", "AI 为什么会替代搜索？"])

        self.assertEqual(code, 0)
        mock_run_episode.assert_called_once_with("AI未来三年系列", "AI 为什么会替代搜索？")
        mock_print.assert_called_once_with(
            "__RESULT__" + json.dumps({"script_path": "demo.md"}, ensure_ascii=False),
            flush=True,
        )

    @patch("ui_worker.deep_series.generate_episode_video_by_titles", return_value={"video_path": "demo.mp4"})
    @patch("builtins.print")
    def test_main_cli_dispatches_deep_generate_video_mode(self, mock_print, mock_generate_video):
        # 深度补生成视频模式和深度写稿模式共用同一套参数协议，但落到不同的执行函数。
        # 单独测这一支，能防止以后重构时把两个模式误合并到同一个实现里。
        code = ui_worker.main_cli(["--deep-generate-video", "AI未来三年系列", "AI 为什么会替代搜索？"])

        self.assertEqual(code, 0)
        mock_generate_video.assert_called_once_with("AI未来三年系列", "AI 为什么会替代搜索？")
        mock_print.assert_called_once_with(
            "__RESULT__" + json.dumps({"video_path": "demo.mp4"}, ensure_ascii=False),
            flush=True,
        )

    @patch("ui_worker.main.run_topic_pipeline", return_value={"topic": "AI 每日简报"})
    @patch("builtins.print")
    def test_main_cli_dispatches_daily_topic_mode(self, mock_print, mock_run_topic_pipeline):
        # 每日简报模式仍然通过主题标题反查 main.TOPICS，再进入主流水线。
        # 这里显式校验传入的是完整主题配置，而不是只把字符串标题直接往下透传。
        code = ui_worker.main_cli(["AI 每日简报"])

        self.assertEqual(code, 0)
        self.assertEqual(mock_run_topic_pipeline.call_count, 1)
        self.assertEqual(mock_run_topic_pipeline.call_args.args[0]["title"], "AI 每日简报")
        mock_print.assert_called_once_with(
            "__RESULT__" + json.dumps({"topic": "AI 每日简报"}, ensure_ascii=False),
            flush=True,
        )


if __name__ == "__main__":
    unittest.main()
