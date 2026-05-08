import json
import os
import tempfile
import unittest
from unittest.mock import patch

import ui


class FakeProcess:
    def __init__(self, lines):
        self.stdout = lines

    def wait(self):
        return 0


class TestUiEncoding(unittest.TestCase):
    def test_worker_subprocess_forces_utf8_output(self):
        app = object.__new__(ui.NewsBriefApp)
        app.worker_python = "python.exe"
        app.worker_script = "ui_worker.py"
        app.append_log = lambda _text: None

        payload = {"topic": "AI 每日简报"}
        lines = ["中文日志\n", "__RESULT__" + json.dumps(payload, ensure_ascii=False) + "\n"]

        with patch("ui.subprocess.Popen", return_value=FakeProcess(lines)) as mock_popen:
            result = app.run_topic_subprocess("AI 每日简报")

        command = mock_popen.call_args.args[0]
        kwargs = mock_popen.call_args.kwargs

        self.assertEqual(command[:3], ["python.exe", "-X", "utf8"])
        self.assertEqual(kwargs["encoding"], "utf-8")
        self.assertEqual(kwargs["env"]["PYTHONIOENCODING"], "utf-8")
        self.assertEqual(kwargs["env"]["PYTHONUTF8"], "1")
        self.assertEqual(result, payload)

    def test_biliup_command_uses_latest_video_metadata(self):
        app = object.__new__(ui.NewsBriefApp)
        video_path = os.path.join("D:\\output", "AI标题.mp4")
        app.biliup_command = "biliup"

        with patch.dict(os.environ, {"BILIUP_USER_COOKIE": "D:\\cookies.json"}):
            with patch("ui.os.path.exists", return_value=True):
                command = app.build_biliup_upload_command(video_path)

        self.assertEqual(command[:4], ["biliup", "--user-cookie", "D:\\cookies.json", "upload"])
        self.assertIn("--copyright", command)
        self.assertIn("1", command)
        self.assertIn("--title", command)
        self.assertEqual(command[command.index("--title") + 1], "AI标题")
        self.assertIn("--desc", command)
        self.assertEqual(command[command.index("--desc") + 1], ui.BILIBILI_DESC)
        self.assertEqual(command[-1], video_path)

    def test_resolve_biliup_command_finds_bbup_app_binary(self):
        app = object.__new__(ui.NewsBriefApp)
        expected = os.path.join("C:\\Users\\Lenovo\\AppData\\Local", "bbup-app", "binaries", "biliup.exe")

        with patch("ui.shutil.which", return_value=None):
            with patch.dict(os.environ, {"LOCALAPPDATA": "C:\\Users\\Lenovo\\AppData\\Local"}):
                with patch("ui.os.path.exists", return_value=True):
                    self.assertEqual(app.resolve_biliup_command(), expected)

    def test_resolve_biliup_cookie_copies_bbup_app_cookie(self):
        app = object.__new__(ui.NewsBriefApp)
        source = os.path.join("C:\\Users\\Lenovo\\AppData\\Local", "bbup-app", "data", "1274586220.json")
        expected = os.path.join(tempfile.gettempdir(), "OpenNewsBrief_biliup_cookie.json")

        with patch.dict(os.environ, {"LOCALAPPDATA": "C:\\Users\\Lenovo\\AppData\\Local"}, clear=False):
            with patch("ui.glob.glob", return_value=[source]):
                with patch("ui.shutil.copyfile") as mock_copy:
                    self.assertEqual(app.resolve_biliup_cookie_path(), expected)

        mock_copy.assert_called_once_with(source, expected)


if __name__ == "__main__":
    unittest.main()
