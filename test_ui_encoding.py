import json
import os
import tempfile
import unittest
import queue
import threading
from unittest.mock import patch
import types

import ui


class FakeProcess:
    def __init__(self, lines):
        self.stdout = lines

    def wait(self):
        return 0


class TestUiEncoding(unittest.TestCase):
    def test_get_deep_episode_status_returns_published_pending_and_ungenerated(self):
        app = object.__new__(ui.NewsBriefApp)

        self.assertEqual(app.get_deep_episode_status({"review_blocked": True}), "审核阻断")
        self.assertEqual(app.get_deep_episode_status({"generated": True, "video_path": "demo.mp4", "published": True}), "已发布")
        self.assertEqual(app.get_deep_episode_status({"generated": True, "video_path": "demo.mp4"}), "待发布")
        self.assertEqual(app.get_deep_episode_status({"review_ready": True, "script_path": "demo.md", "audio_path": "demo.mp3", "actual_seconds": 181.0}), "音频超时")
        self.assertEqual(app.get_deep_episode_status({"review_ready": True, "script_path": "demo.md", "audio_path": "demo.mp3"}), "待合成视频")
        self.assertEqual(app.get_deep_episode_status({"review_ready": True, "script_path": "demo.md"}), "待合成TTS")
        self.assertEqual(app.get_deep_episode_status({}), "未生成")

    def test_format_deep_quality_shows_source_duration_and_risk(self):
        app = object.__new__(ui.NewsBriefApp)
        episode = {
            "source_count": 4,
            "estimated_seconds": 118.4,
            "quality_block_reason": "",
        }

        self.assertEqual(app.format_deep_quality(episode), "源4 / 118秒 / 通过")

        episode["review_blocked"] = True
        episode["quality_block_reason"] = "有效来源不足"
        self.assertEqual(app.format_deep_quality(episode), "源4 / 118秒 / 阻断")

        episode["review_blocked"] = False
        episode["actual_seconds"] = 181.0
        self.assertEqual(app.format_deep_quality(episode), "源4 / 181秒 / 超时")

    def test_get_deep_publish_episodes_filters_by_tab(self):
        app = object.__new__(ui.NewsBriefApp)
        series = {
            "title": "AI未来三年系列",
            "episodes": [
                {"title": "A", "generated": True, "video_path": "a.mp4", "published": False},
                {"title": "B", "generated": True, "video_path": "b.mp4", "published": True},
                {"title": "C", "generated": False, "video_path": "c.mp4", "published": False},
            ],
        }

        with patch("ui.os.path.exists", return_value=True):
            pending = app.get_deep_publish_episodes(series, "pending")
            published = app.get_deep_publish_episodes(series, "published")

        self.assertEqual([episode.get("title") for _series, episode in pending], ["A"])
        self.assertEqual([episode.get("title") for _series, episode in published], ["B"])

    def test_mark_deep_episode_published_updates_config(self):
        app = object.__new__(ui.NewsBriefApp)
        app.deep_config = {
            "series": [{
                "title": "AI未来三年系列",
                "episodes": [{"title": "AI 为什么会替代搜索？", "generated": True, "video_path": "demo.mp4"}],
            }]
        }

        with patch("ui.deep_series.save_config") as mock_save:
            app.mark_deep_episode_published("AI未来三年系列", "AI 为什么会替代搜索？")

        episode = app.deep_config["series"][0]["episodes"][0]
        self.assertTrue(episode["published"])
        self.assertIn("published_at", episode)
        mock_save.assert_called_once_with(app.deep_config)

    def test_build_deep_publish_result_exposes_published_flag(self):
        app = object.__new__(ui.NewsBriefApp)
        app.append_log = lambda _text: None
        app.deep_config = {"series": []}
        series = {"title": "AI未来三年系列"}
        episode = {
            "title": "AI 为什么会替代搜索？",
            "video_path": "demo.mp4",
            "publish_desc": "已有发布简介",
            "published": True,
        }

        result = app.build_deep_publish_result(series, episode)

        self.assertTrue(result["published"])

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
        self.assertEqual(kwargs["env"]["PYTHONUNBUFFERED"], "1")
        self.assertEqual(kwargs["env"]["OPENNEWSBRIEF_WAVEFORM_COLOR"], ui.WAVEFORM_COLOR)
        self.assertEqual(result, payload)

    def test_deep_worker_subprocess_uses_deep_mode(self):
        app = object.__new__(ui.NewsBriefApp)
        app.worker_python = "python.exe"
        app.worker_script = "ui_worker.py"
        app.append_log = lambda _text: None

        payload = {"video_path": "deep.mp4"}
        lines = ["__RESULT__" + json.dumps(payload, ensure_ascii=False) + "\n"]

        with patch("ui.subprocess.Popen", return_value=FakeProcess(lines)) as mock_popen:
            result = app.run_worker_subprocess(["--deep", "AI未来三年系列", "AI 为什么会替代搜索？"])

        command = mock_popen.call_args.args[0]
        self.assertEqual(command, [
            "python.exe",
            "-X",
            "utf8",
            "ui_worker.py",
            "--deep",
            "AI未来三年系列",
            "AI 为什么会替代搜索？",
        ])
        self.assertEqual(result, payload)

    def test_deep_generate_video_runs_in_worker_subprocess(self):
        app = object.__new__(ui.NewsBriefApp)
        app.latest_result = {}
        logs = []
        # 这里直接验证深度视频改走子进程，避免把长任务留在 UI 进程里。
        app.append_log = logs.append
        app.update_result_panel = lambda _result: None
        app.reload_deep_config = lambda *_args, **_kwargs: None
        app.finish_run = lambda: None

        payload = {"video_path": "deep.mp4"}
        with patch.object(app, "run_worker_subprocess", return_value=payload) as mock_run, \
                patch("ui.deep_series.generate_episode_video_by_titles") as mock_generate:
            app.run_deep_generate_video("AI未来三年系列", "AI 为什么会替代搜索？")

        mock_run.assert_called_once_with(["--deep-generate-video", "AI未来三年系列", "AI 为什么会替代搜索？"])
        mock_generate.assert_not_called()
        self.assertEqual(app.latest_result, payload)
        self.assertTrue(any("开始合成深度视频" in item for item in logs))

    def test_deep_generate_tts_runs_in_worker_subprocess(self):
        app = object.__new__(ui.NewsBriefApp)
        app.latest_result = {}
        logs = []
        app.append_log = logs.append
        app.update_result_panel = lambda _result: None
        app.reload_deep_config = lambda *_args, **_kwargs: None
        app.finish_run = lambda: None

        payload = {"audio_path": "deep.mp3"}
        with patch.object(app, "run_worker_subprocess", return_value=payload) as mock_run:
            app.run_deep_generate_tts("AI未来三年系列", "AI 为什么会替代搜索？")

        mock_run.assert_called_once_with(["--deep-generate-tts", "AI未来三年系列", "AI 为什么会替代搜索？"])
        self.assertEqual(app.latest_result, payload)
        self.assertTrue(any("开始合成深度TTS" in item for item in logs))

    def test_post_ui_queues_background_thread_callbacks(self):
        class FakeRoot:
            # 这里只保留最小的 after 能力，模拟界面主循环继续调度队列处理。
            def after(self, _delay, _func):
                return None

        app = object.__new__(ui.NewsBriefApp)
        app.root = FakeRoot()
        app.ui_queue = queue.Queue()
        calls = []

        def worker():
            # 后台线程里调用时，_post_ui 不能直接碰 Tk，只能先进入队列。
            app._post_ui(lambda: calls.append("done"))

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join()

        self.assertEqual(calls, [])
        self.assertEqual(app.ui_queue.qsize(), 1)

        app.process_ui_queue()

        self.assertEqual(calls, ["done"])

    def test_reload_deep_config_keeps_selected_series_and_episode(self):
        class FakeVar:
            # 这里用最小可用的变量对象，模拟界面里的 StringVar。
            def __init__(self):
                self.value = ""

            def set(self, value):
                self.value = value

        class FakeTreeview:
            # 这里用最小可用的树表对象，模拟刷新时的选择和定位逻辑。
            def __init__(self):
                self.rows = []
                self._selection = []

            def delete(self, *args):
                self.rows = []
                self._selection = []

            def get_children(self):
                return [row["iid"] for row in self.rows]

            def insert(self, _parent, _index, iid=None, values=()):
                self.rows.append({"iid": iid, "values": values})

            def selection_set(self, iid):
                self._selection = [iid]

            def focus(self, iid):
                self.focused = iid

            def selection(self):
                return tuple(self._selection)

        app = object.__new__(ui.NewsBriefApp)
        app.root = types.SimpleNamespace(after=lambda _delay, func: func())
        app.deep_series_table = FakeTreeview()
        app.deep_episode_table = FakeTreeview()
        app.deep_series_title_var = FakeVar()
        app.deep_series_desc_var = FakeVar()
        app.deep_episode_title_var = FakeVar()
        app.deep_episode_question_var = FakeVar()
        app.refresh_deep_publish_list = lambda: None
        app.deep_config = {
            "series": [
                {"title": "Series A", "description": "A", "episodes": [{"title": "Topic A1"}]},
                {
                    "title": "Series B",
                    "description": "B",
                    "episodes": [{"title": "Topic B1"}, {"title": "Topic B2"}],
                },
            ]
        }

        # 重新加载后的配置可以变化，但只要标题还在，界面就要回到原来的选中项。
        refreshed_config = {
            "series": [
                {"title": "Series A", "description": "A", "episodes": [{"title": "Topic A1"}]},
                {
                    "title": "Series B",
                    "description": "B",
                    "episodes": [
                        {"title": "Topic B1", "generated": True},
                        {"title": "Topic B2", "generated": True},
                    ],
                },
            ]
        }

        with patch("ui.deep_series.load_config", return_value=refreshed_config):
            app.reload_deep_config("Series B", "Topic B2")

        self.assertEqual(app.deep_series_table.selection(), ("s1",))
        self.assertEqual(app.deep_episode_table.selection(), ("e1_1",))
        self.assertEqual(app.deep_series_title_var.value, "Series B")
        self.assertEqual(app.deep_episode_title_var.value, "Topic B2")

    def test_biliup_command_uses_latest_video_metadata(self):
        app = object.__new__(ui.NewsBriefApp)
        video_path = os.path.join("D:\\output", "AI标题.mp4")
        app.biliup_command = "biliup"
        app.latest_result = {}

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

    def test_biliup_command_uses_deep_series_metadata(self):
        app = object.__new__(ui.NewsBriefApp)
        video_path = os.path.join("D:\\output", "深度主题.mp4")
        app.biliup_command = "biliup"
        app.latest_result = {
            "series": "AI未来三年系列",
            "episode": "AI 为什么会替代搜索？",
            "publish_title": "AI搜索替代深度解析",
            "publish_desc": "AI生成的发布简介",
            "publish_tags": "AI,搜索,科技",
        }

        with patch.dict(os.environ, {"BILIUP_USER_COOKIE": "D:\\cookies.json"}):
            with patch("ui.os.path.exists", return_value=True):
                command = app.build_biliup_upload_command(video_path)

        self.assertEqual(command[command.index("--title") + 1], "AI未来三年系列：AI搜索替代深度解析")
        self.assertEqual(command[command.index("--tag") + 1], "AI,搜索,科技")
        self.assertEqual(command[command.index("--desc") + 1], "AI生成的发布简介")

    def test_biliup_command_limits_tags_for_bilibili_rule(self):
        # 失败日志里的标签超过 B站数量限制，这里固定住命令侧的裁剪结果。
        app = object.__new__(ui.NewsBriefApp)
        video_path = os.path.join("D:\\output", "deep.mp4")
        app.biliup_command = "biliup"
        app.resolve_biliup_cookie_path = lambda: ""
        app.latest_result = {
            "series": "Series",
            "episode": "Episode",
            "publish_title": "Title",
            "publish_desc": "Desc",
            "publish_tags": "tag01,tag02,tag03,tag04,tag05,tag06,tag07,tag08,tag09,tag10,tag11,tag12,tag13",
        }

        command = app.build_biliup_upload_command(video_path)

        self.assertEqual(command[command.index("--tag") + 1], "tag01,tag02,tag03,tag04,tag05,tag06,tag07,tag08,tag09,tag10,tag11,tag12")

    def test_publish_video_once_rejects_nonzero_bilibili_response_code(self):
        # biliup 上传文件成功不代表投稿成功，ResponseData 非 0 时不能更新发布状态。
        app = object.__new__(ui.NewsBriefApp)
        app.biliup_command = "biliup"
        app.latest_result = {}
        app.resolve_biliup_cookie_path = lambda: ""
        logs = []
        app.append_log = logs.append

        output = [
            'ResponseData { code: 21005, data: None, message: "Tag不能为空，总数量不能超过12个， 并且单个不能超过20个字", ttl: Some(1) }\n'
        ]
        with patch("ui.subprocess.Popen", return_value=FakeProcess(output)):
            published = app.publish_video_once("demo.mp4")

        self.assertFalse(published)
        self.assertNotIn("Traceback", "".join(logs))

    def test_biliup_command_prefixes_deep_series_title(self):
        app = object.__new__(ui.NewsBriefApp)
        video_path = os.path.join("D:\\output", "深度主题.mp4")
        app.biliup_command = "biliup"
        app.latest_result = {
            "series": "AI未来三年系列",
            "episode": "AI 为什么会替代搜索？",
            "publish_title": "AI搜索替代深度解析",
            "publish_desc": "AI生成的发布简介",
            "publish_tags": "AI,搜索,科技",
        }

        with patch.dict(os.environ, {"BILIUP_USER_COOKIE": "D:\\cookies.json"}):
            with patch("ui.os.path.exists", return_value=True):
                command = app.build_biliup_upload_command(video_path)

        self.assertEqual(command[command.index("--title") + 1], "AI未来三年系列：AI搜索替代深度解析")

    def test_biliup_command_does_not_duplicate_deep_series_title_prefix(self):
        app = object.__new__(ui.NewsBriefApp)
        video_path = os.path.join("D:\\output", "深度主题.mp4")
        app.biliup_command = "biliup"
        app.latest_result = {
            "series": "AI未来三年系列",
            "episode": "AI 为什么会替代搜索？",
            "publish_title": "AI未来三年系列：AI搜索替代深度解析",
            "publish_desc": "AI生成的发布简介",
            "publish_tags": "AI,搜索,科技",
        }

        with patch.dict(os.environ, {"BILIUP_USER_COOKIE": "D:\\cookies.json"}):
            with patch("ui.os.path.exists", return_value=True):
                command = app.build_biliup_upload_command(video_path)

        self.assertEqual(command[command.index("--title") + 1], "AI未来三年系列：AI搜索替代深度解析")

    def test_build_deep_publish_preview_text_reads_assets_file(self):
        app = object.__new__(ui.NewsBriefApp)
        assets_path = os.path.join(tempfile.gettempdir(), "opennewsbrief_publish_assets_test.json")
        with open(assets_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "title": "味精公司卡住AI芯片？",
                    "desc": "从味之素、ABF绝缘膜、封装基板讲清AI芯片供应链。",
                    "tags": "AI芯片,味之素,ABF,封装基板",
                },
                f,
                ensure_ascii=False,
            )

        try:
            text = app.build_deep_publish_preview_text(
                {"title": "AI时代的隐形地基"},
                {"title": "味之素", "publish_assets_path": assets_path},
            )
        finally:
            os.remove(assets_path)

        self.assertIn("标题：AI时代的隐形地基：味精公司卡住AI芯片？", text)
        self.assertIn("简介：", text)
        self.assertIn("ABF绝缘膜", text)
        self.assertIn("标签：AI芯片,味之素,ABF,封装基板", text)

    def test_show_deep_publish_opens_preview_dialog(self):
        app = object.__new__(ui.NewsBriefApp)
        series = {"title": "AI时代的隐形地基"}
        episode = {
            "title": "味之素",
            "publish_title": "味精公司卡住AI芯片？",
            "publish_desc": "发布简介",
            "publish_tags": "AI芯片,ABF",
        }
        app.get_selected_deep_target = lambda: (series, episode)
        opened = {}
        app.show_text_dialog = lambda title, content, path: opened.update({"title": title, "content": content, "path": path})

        app.show_deep_publish()

        self.assertEqual(opened["title"], "发布标题和介绍")
        self.assertIn("味精公司卡住AI芯片", opened["content"])
        self.assertEqual(opened["path"], "")

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

    def test_publish_to_bilibili_marks_latest_deep_video_as_published_on_success(self):
        app = object.__new__(ui.NewsBriefApp)
        app.latest_result = {
            "series": "AI未来三年系列",
            "episode": "AI 为什么会替代搜索？",
            "video_path": "demo.mp4",
        }
        app.publish_video_once = lambda _video_path: True
        app.finish_publish = lambda: setattr(app, "_finished", True)
        app.mark_deep_episode_published = lambda series, episode: setattr(app, "_published_mark", (series, episode))

        app.publish_to_bilibili("demo.mp4")

        self.assertEqual(app._published_mark, ("AI未来三年系列", "AI 为什么会替代搜索？"))
        self.assertTrue(app._finished)


if __name__ == "__main__":
    unittest.main()
