import json
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


if __name__ == "__main__":
    unittest.main()
