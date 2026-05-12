import asyncio
import os
import shutil
import sys
import tempfile
import types
import unittest
from unittest.mock import MagicMock, patch

import deep_series


class FakeCommunicate:
    calls = []

    def __init__(self, text, voice, rate=None):
        self.text = text
        self.voice = voice
        self.rate = rate
        self.output_path = ""
        FakeCommunicate.calls.append(self)

    async def save(self, output_path):
        self.output_path = output_path


class TestDeepSeries(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.config_path = os.path.join(self.tmpdir, "deep_series_config.json")
        self.original_tts_engine = deep_series.DEEP_TTS_ENGINE
        FakeCommunicate.calls.clear()

    def tearDown(self):
        deep_series.DEEP_TTS_ENGINE = self.original_tts_engine
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_load_config_creates_ai_future_series(self):
        config = deep_series.load_config(self.config_path)

        self.assertTrue(config["series"][0]["title"])
        self.assertTrue(config["series"][0]["episodes"])
        self.assertTrue(os.path.exists(self.config_path))

    def test_save_config_round_trips_new_series_and_episode(self):
        config = {
            "series": [{
                "title": "新系列",
                "description": "测试",
                "episodes": [{"title": "新主题", "question": "为什么？"}],
            }]
        }

        deep_series.save_config(config, self.config_path)
        loaded = deep_series.load_config(self.config_path)

        self.assertEqual(loaded, config)

    def test_build_search_keywords_covers_multiple_research_angles(self):
        keywords = deep_series.build_search_keywords(
            {"title": "AI未来三年系列"},
            {"title": "AI 为什么会替代搜索？", "question": "AI 为什么会替代搜索？"},
        )
        joined = "\n".join(keywords)

        self.assertGreaterEqual(len(keywords), 10)
        self.assertIn("用户行为", joined)
        self.assertIn("商业模式", joined)
        self.assertIn("技术限制", joined)
        self.assertIn("反方观点", joined)
        self.assertIn("监管风险", joined)
        self.assertIn("赢家输家", joined)

    @patch("deep_series.call_llm", return_value="研究报告")
    def test_generate_research_report_requires_multi_angle_synthesis(self, mock_llm):
        deep_series.generate_research_report(
            {"title": "AI未来三年系列"},
            {"title": "AI 为什么会替代搜索？", "question": "AI 为什么会替代搜索？"},
            [{"title": "Source", "link": "https://example.com", "content": "content"}],
        )

        prompt = mock_llm.call_args.args[0]
        self.assertIn("多角度整理加工", prompt)
        self.assertIn("正方观点", prompt)
        self.assertIn("反方观点", prompt)
        self.assertIn("技术角度", prompt)
        self.assertIn("商业模式", prompt)
        self.assertIn("用户行为", prompt)
        self.assertIn("监管风险", prompt)
        self.assertIn("利益相关方", prompt)

    def test_parse_dialogue_script_extracts_speakers(self):
        script = "女：为什么 Agent 会重构软件？\n男：因为软件会从按钮变成目标驱动。\n旁白：结尾总结。"

        segments = deep_series.parse_dialogue_script(script)

        self.assertEqual([item["speaker"] for item in segments], ["female", "male", "narrator"])
        self.assertIn("目标驱动", segments[1]["text"])

    def test_parse_dialogue_script_handles_markdown_bold_roles_and_preamble(self):
        script = (
            "好的，各位观众，这是为您生成的最终版解说脚本。\n\n"
            "### AI未来三年系列：AI 内容工厂会出现吗？\n\n"
            "**女：** 2026年5月，一个消息像炸弹一样扔进了影视圈。\n\n"
            "**男：** 所以，一个最直接的问题砸在我们脸上：AI内容工厂真的要来了吗？\n\n"
            "**女：** **第一，技术。** 目前公开资料还不足以证明AI已经突破根本瓶颈。"
        )

        cleaned = deep_series.clean_script_output(script)
        segments = deep_series.parse_dialogue_script(script)

        self.assertNotIn("好的，各位观众", cleaned)
        self.assertNotIn("###", cleaned)
        self.assertEqual([item["speaker"] for item in segments], ["female", "male", "female"])
        self.assertIn("2026年5月", segments[0]["text"])
        self.assertIn("第一，技术。", segments[2]["text"])
        self.assertNotIn("女：", segments[0]["text"])
        self.assertNotIn("男：", segments[1]["text"])

    def test_clean_script_output_strips_markdown_rule_header(self):
        raw = "---\n\n女：你有没有想过，如果AI不只是聊天，而是能直接替你干活，那该多爽？\n\n男：比如呢？"

        cleaned = deep_series.clean_script_output(raw)

        self.assertFalse(cleaned.startswith("---"))
        self.assertEqual(
            cleaned,
            "女：你有没有想过，如果AI不只是聊天，而是能直接替你干活，那该多爽？\n\n男：比如呢？",
        )

    def test_save_tts_uses_chattts_for_gendered_dialogue_when_chattts_enabled(self):
        fake_chattts = types.SimpleNamespace(synthesize_text=MagicMock())
        fake_edge_tts = types.SimpleNamespace(Communicate=FakeCommunicate)
        deep_series.DEEP_TTS_ENGINE = "chattts"

        with patch.dict(sys.modules, {
            "audioContent.chattts_engine": fake_chattts,
            "edge_tts": fake_edge_tts,
        }):
            asyncio.run(deep_series._save_tts("第一句女声", "female.mp3", deep_series.FEMALE_VOICE, role="female"))
            asyncio.run(deep_series._save_tts("第一句男声", "male.mp3", deep_series.MALE_VOICE, role="male"))

        fake_chattts.synthesize_text.assert_any_call("第一句女声", "female.mp3", role="female")
        fake_chattts.synthesize_text.assert_any_call("第一句男声", "male.mp3", role="male")
        self.assertEqual(fake_chattts.synthesize_text.call_count, 2)
        self.assertEqual(FakeCommunicate.calls, [])

    def test_save_tts_keeps_chattts_for_narrator(self):
        fake_chattts = types.SimpleNamespace(synthesize_text=MagicMock())
        fake_edge_tts = types.SimpleNamespace(Communicate=FakeCommunicate)
        deep_series.DEEP_TTS_ENGINE = "chattts"

        with patch.dict(sys.modules, {
            "audioContent.chattts_engine": fake_chattts,
            "edge_tts": fake_edge_tts,
        }):
            asyncio.run(deep_series._save_tts("旁白总结", "narrator.mp3", deep_series.MALE_VOICE, role="narrator"))

        fake_chattts.synthesize_text.assert_called_once_with("旁白总结", "narrator.mp3", role="narrator")
        self.assertEqual(FakeCommunicate.calls, [])

    def test_convert_dialogue_to_audio_adds_pause_between_speakers(self):
        script_path = os.path.join(self.tmpdir, "script.md")
        output_path = os.path.join(self.tmpdir, "dialogue.mp3")
        with open(script_path, "w", encoding="utf-8") as f:
            f.write("女：第一句。\n男：第二句。")

        async def fake_save_tts(_text, output_path, _voice, role="narrator"):
            with open(output_path, "wb") as f:
                f.write(role.encode("utf-8"))

        with patch.object(deep_series, "_save_tts", side_effect=fake_save_tts), \
                patch.object(deep_series, "create_silence_audio", side_effect=lambda path, _duration: path), \
                patch("audioContent.news_to_audio.get_audio_duration", return_value=1.0), \
                patch("audioContent.news_to_audio.concat_audio_files", return_value=output_path) as mock_concat, \
                patch("audioContent.news_to_audio.write_timing_file", return_value=output_path + ".timing.json") as mock_timing:
            deep_series.convert_dialogue_to_audio(script_path, output_path)

        segment_paths = mock_concat.call_args.args[0]
        self.assertEqual(len(segment_paths), 3)
        self.assertTrue(segment_paths[1].endswith("_pause.mp3"))
        timing_segments = mock_timing.call_args.args[1]
        self.assertAlmostEqual(timing_segments[0]["duration"], 1.0 + deep_series.DEEP_DIALOGUE_PAUSE_SECONDS)
        self.assertEqual(timing_segments[1]["duration"], 1.0)

    def test_convert_dialogue_to_audio_cleans_stale_segments_and_uses_roles(self):
        script_path = os.path.join(self.tmpdir, "script.md")
        output_path = os.path.join(self.tmpdir, "dialogue.mp3")
        segment_dir = os.path.join(self.tmpdir, "dialogue_segments")
        os.makedirs(segment_dir, exist_ok=True)
        old_path = os.path.join(segment_dir, "999_old.mp3")
        with open(old_path, "wb") as f:
            f.write(b"old")
        with open(script_path, "w", encoding="utf-8") as f:
            f.write("### 标题\n\n**女：** 第一句。\n**男：** 第二句。")

        async def fake_save_tts(_text, output_path, _voice, role="narrator"):
            with open(output_path, "wb") as f:
                f.write(role.encode("utf-8"))

        with patch.object(deep_series, "_save_tts", side_effect=fake_save_tts), \
                patch.object(deep_series, "create_silence_audio", side_effect=lambda path, _duration: path), \
                patch("audioContent.news_to_audio.get_audio_duration", return_value=1.0), \
                patch("audioContent.news_to_audio.concat_audio_files", return_value=output_path) as mock_concat, \
                patch("audioContent.news_to_audio.write_timing_file", return_value=output_path + ".timing.json") as mock_timing:
            deep_series.convert_dialogue_to_audio(script_path, output_path)

        self.assertFalse(os.path.exists(old_path))
        segment_paths = [os.path.basename(path) for path in mock_concat.call_args.args[0]]
        self.assertIn("000_female.mp3", segment_paths)
        self.assertIn("001_male.mp3", segment_paths)
        self.assertNotIn("000_narrator.mp3", segment_paths)
        timing_segments = mock_timing.call_args.args[1]
        self.assertEqual([item["role"] for item in timing_segments], ["female", "male"])

    def test_create_text_card_uses_dark_documentary_ios_canvas(self):
        image_path = os.path.join(self.tmpdir, "slide.png")

        deep_series.create_text_card("标题", "系列 · 女主持", "这是一段用于检查版式的正文。", image_path)

        from PIL import Image
        with Image.open(image_path) as image:
            self.assertEqual(image.getpixel((40, 40)), (16, 16, 20))
            self.assertNotEqual(image.getpixel((540, 540)), (245, 245, 247))

    def test_step_video_disables_transition_clicks_for_deep_series(self):
        audio_path = os.path.join(self.tmpdir, "dialogue.mp3")
        image_path = os.path.join(self.tmpdir, "slide.png")
        with open(audio_path, "wb") as f:
            f.write(b"audio")
        with open(image_path, "wb") as f:
            f.write(b"image")

        with patch("deep_series.main.load_slide_durations_from_timing", return_value=[1.0]), \
                patch("video.Audio2Video.create_video", return_value=os.path.join(self.tmpdir, "out.mp4")) as mock_create:
            deep_series.step_video(audio_path, "测试视频", [image_path])

        self.assertFalse(mock_create.call_args.kwargs["transition_clicks"])

    def test_mark_episode_generated_records_video_state(self):
        config = deep_series.load_config(self.config_path)
        series_title = config["series"][0]["title"]
        episode_title = config["series"][0]["episodes"][0]["title"]
        episode = config["series"][0]["episodes"][0]
        episode["published"] = True
        episode["published_at"] = "2026-05-11 10:00:00"
        result = {"video_path": "D:\\output\\demo.mp4", "script_path": "script.md"}

        deep_series.mark_episode_generated(config, series_title, episode_title, result)

        episode = config["series"][0]["episodes"][0]
        self.assertTrue(episode["generated"])
        self.assertEqual(episode["video_path"], "D:\\output\\demo.mp4")
        self.assertEqual(episode["script_path"], "script.md")
        self.assertFalse(episode["published"])
        self.assertEqual(episode["published_at"], "")
        self.assertIn("generated_at", episode)

    @patch("deep_series.call_llm", return_value='{"title":"深度标题","desc":"深度简介","tags":"AI,科技"}')
    def test_generate_publish_assets_writes_ai_metadata(self, _llm):
        script_path = os.path.join(self.tmpdir, "script.md")
        with open(script_path, "w", encoding="utf-8") as f:
            f.write("女：AI 为什么会替代搜索？\n男：用户要的是答案。")
        result = {"script_path": script_path, "video_path": os.path.join(self.tmpdir, "demo.mp4")}
        series = {"title": "AI未来三年系列"}
        episode = {"title": "AI 为什么会替代搜索？"}

        assets = deep_series.generate_publish_assets(series, episode, result)

        self.assertEqual(assets["title"], "深度标题")
        self.assertEqual(assets["desc"], "深度简介")
        self.assertEqual(assets["tags"], "AI,科技")
        self.assertTrue(os.path.exists(os.path.join(self.tmpdir, "publish_assets.json")))

    @patch("deep_series.step_video", return_value="demo.mp4")
    @patch("deep_series.create_deep_slide_images", return_value=["cover.png", "section.png"])
    @patch("deep_series.convert_dialogue_to_audio", return_value="demo.mp3")
    @patch("deep_series.call_llm", side_effect=[
        "研究报告",
        "事实核查：通过",
        "反方审稿：通过",
        "结构审核：通过",
        "多角度审稿：通过",
        "女：AI 为什么会替代搜索？\n男：因为用户要答案，不只是链接。",
        "写稿意见：已采纳审稿建议",
        "# 新版标题\n1. AI正在杀死搜索\n\n# 分镜脚本\n- handheld documentary shot",
    ])
    @patch("deep_series.collect_research_sources", return_value=[
        {"title": "Source 1", "link": "https://example.com/1", "content": "AI search source"},
    ])
    def test_run_episode_pipeline_returns_review_artifacts(self, _sources, _llm, _audio, _slides, _video):
        episode = {"title": "AI 为什么会替代搜索？", "question": "AI 为什么会替代搜索？"}
        series = {"title": "AI未来三年系列", "description": "测试系列"}

        result = deep_series.run_episode_pipeline(series, episode, base_dir=self.tmpdir)

        self.assertEqual(result["video_path"], "")
        self.assertEqual(result["audio_path"], "")
        self.assertTrue(result["script_path"].endswith("script.md"))
        self.assertTrue(os.path.exists(result["research_path"]))
        self.assertTrue(os.path.exists(result["script_notes_path"]))
        self.assertTrue(os.path.exists(result["documentary_package_path"]))
        self.assertTrue(os.path.exists(result["agent_log_path"]))
        _audio.assert_not_called()
        _slides.assert_not_called()
        _video.assert_not_called()

    def test_generate_episode_video_preserves_config_changes_during_generation(self):
        config = {
            "series": [
                {
                    "title": "Series A",
                    "description": "Demo",
                    "episodes": [{"title": "Episode A", "question": "Question A", "script_path": "script.md"}],
                }
            ]
        }
        deep_series.save_config(config, self.config_path)

        def generate_side_effect(_series, _episode, result):
            latest = deep_series.load_config(self.config_path)
            latest["series"].append({"title": "Series B", "description": "", "episodes": []})
            deep_series.save_config(latest, self.config_path)
            result["video_path"] = os.path.join(self.tmpdir, "demo.mp4")
            return result

        with patch("deep_series.CONFIG_PATH", self.config_path), \
                patch("deep_series.generate_video_from_script", side_effect=generate_side_effect), \
                patch("deep_series.generate_publish_assets", return_value={
                    "path": os.path.join(self.tmpdir, "publish_assets.json"),
                    "title": "Title",
                    "desc": "Desc",
                    "tags": "AI",
                }):
            deep_series.generate_episode_video_by_titles("Series A", "Episode A")

        latest = deep_series.load_config(self.config_path)
        self.assertEqual([item["title"] for item in latest["series"]], ["Series A", "Series B"])
        self.assertTrue(latest["series"][0]["episodes"][0]["generated"])

    def test_run_episode_by_titles_preserves_config_changes_during_generation(self):
        config = {
            "series": [
                {
                    "title": "Series A",
                    "description": "Demo",
                    "episodes": [{"title": "Episode A", "question": "Question A"}],
                }
            ]
        }
        deep_series.save_config(config, self.config_path)

        def pipeline_side_effect(_series, _episode):
            latest = deep_series.load_config(self.config_path)
            latest["series"].append({"title": "Series B", "description": "", "episodes": []})
            deep_series.save_config(latest, self.config_path)
            return {"research_path": "research.md", "audit_path": "audit.md", "script_path": "script.md"}

        with patch("deep_series.CONFIG_PATH", self.config_path), \
                patch("deep_series.run_episode_pipeline", side_effect=pipeline_side_effect):
            deep_series.run_episode_by_titles("Series A", "Episode A")

        latest = deep_series.load_config(self.config_path)
        self.assertEqual([item["title"] for item in latest["series"]], ["Series A", "Series B"])
        episode = latest["series"][0]["episodes"][0]
        self.assertTrue(episode["review_ready"])
        self.assertEqual(episode["script_path"], "script.md")

    @patch("deep_series.call_llm", return_value='{"title":"AI正在杀死搜索入口","desc":"desc","tags":"AI,搜索","cover_text":"搜索正在死亡","cover_prompt":"左边是搜索框，右边是AI对话框","title_options":["AI正在杀死搜索"],"cover_options":["搜索正在死亡"]}')
    def test_generate_publish_assets_adds_documentary_cover_fields(self, mock_llm):
        script_path = os.path.join(self.tmpdir, "script.md")
        with open(script_path, "w", encoding="utf-8") as f:
            f.write("女：搜索正在变成答案。\n男：用户不再想看十个链接。")
        result = {"script_path": script_path, "video_path": os.path.join(self.tmpdir, "demo.mp4")}
        series = {"title": "AI未来三年系列"}
        episode = {"title": "AI 为什么会替代搜索？"}

        assets = deep_series.generate_publish_assets(series, episode, result)

        prompt = mock_llm.call_args.args[0]
        self.assertIn("12到24个字", prompt)
        self.assertIn("封面文案", prompt)
        self.assertEqual(assets["title"], "AI正在杀死搜索入口")
        self.assertEqual(assets["cover_text"], "搜索正在死亡")
        self.assertTrue(assets["cover_prompt"])
        self.assertTrue(os.path.exists(assets["cover_path"]))
        self.assertTrue(os.path.exists(os.path.join(self.tmpdir, "publish_assets.json")))

    @patch("deep_series.call_llm", return_value="女：搜索，可能正在死亡。\n男：我最近越来越强烈地感觉，入口正在换人。")
    def test_generate_dialogue_script_uses_documentary_prompt_rules(self, mock_llm):
        deep_series.generate_dialogue_script(
            {"title": "AI未来三年系列"},
            {"title": "AI 为什么会替代搜索？", "question": "AI 为什么会替代搜索？"},
            "研究报告",
            "审稿意见",
        )

        prompt = mock_llm.call_args.args[0]
        self.assertIn("科技纪录片", prompt)
        self.assertIn("冲突开场", prompt)
        self.assertIn("旧世界是什么", prompt)
        self.assertIn("第一人称", prompt)
        self.assertIn("正反双方", prompt)
        self.assertIn("至少四个角度", prompt)
        self.assertIn("技术、商业、用户、风险", prompt)
        self.assertIn("不能只给单一结论", prompt)
        self.assertIn("禁止“今天我们来聊”", prompt)
        self.assertIn("不要写成第一点、第二点", prompt)

    @patch("deep_series.call_llm", return_value="# 新版标题\n1. AI正在杀死搜索\n\n# 分镜脚本\n- handheld documentary shot\n\n# Shorts切片方案\n- 0-30秒")
    def test_generate_documentary_package_writes_growth_assets(self, _mock_llm):
        result = {"documentary_package_path": os.path.join(self.tmpdir, "documentary_package.md")}

        package = deep_series.generate_documentary_package(
            {"title": "AI未来三年系列"},
            {"title": "AI 为什么会替代搜索？"},
            "研究报告",
            "审稿意见",
            "女：搜索，可能正在死亡。",
            result,
        )

        self.assertIn("新版标题", package)
        self.assertTrue(os.path.exists(result["documentary_package_path"]))
        with open(result["documentary_package_path"], "r", encoding="utf-8") as f:
            saved = f.read()
        self.assertIn("分镜脚本", saved)
        self.assertIn("Shorts切片方案", saved)

    def test_create_deep_slide_images_splits_long_segments_and_writes_durations(self):
        script_path = os.path.join(self.tmpdir, "script.md")
        audio_path = os.path.join(self.tmpdir, "dialogue.mp3")
        with open(script_path, "w", encoding="utf-8") as f:
            f.write("男：搜索正在变成答案入口。用户不再想看十个链接。未来的软件入口会从搜索框变成AI对话。")
        with open(audio_path + ".timing.json", "w", encoding="utf-8") as f:
            f.write('{"segments":[{"role":"male","slide_index":0,"duration":10.0,"text":"搜索正在变成答案入口。用户不再想看十个链接。未来的软件入口会从搜索框变成AI对话。"}]}')

        image_paths = deep_series.create_deep_slide_images(
            {"title": "AI未来三年系列"},
            {"title": "AI 为什么会替代搜索？"},
            script_path,
            audio_path,
        )

        self.assertGreaterEqual(len(image_paths), 3)
        durations = deep_series.load_deep_slide_durations(image_paths)
        self.assertAlmostEqual(sum(durations), 10.0)
        self.assertLessEqual(max(durations), 4.0)
        self.assertTrue(os.path.exists(os.path.join(os.path.dirname(image_paths[0]), "slide_durations.json")))

    def test_step_video_uses_deep_slide_duration_sidecar(self):
        audio_path = os.path.join(self.tmpdir, "dialogue.mp3")
        slide_dir = os.path.join(self.tmpdir, "deep_slides")
        os.makedirs(slide_dir, exist_ok=True)
        image_paths = [os.path.join(slide_dir, "slide_000.png"), os.path.join(slide_dir, "slide_001.png")]
        with open(audio_path, "wb") as f:
            f.write(b"audio")
        for image_path in image_paths:
            with open(image_path, "wb") as f:
                f.write(b"image")
        with open(os.path.join(slide_dir, "slide_durations.json"), "w", encoding="utf-8") as f:
            f.write("[3.0, 3.0]")

        with patch("deep_series.main.load_slide_durations_from_timing", return_value=[6.0]), \
                patch("video.Audio2Video.create_video", return_value=os.path.join(self.tmpdir, "out.mp4")) as mock_create:
            deep_series.step_video(audio_path, "测试视频", image_paths)

        self.assertEqual(mock_create.call_args.kwargs["slide_durations"], [3.0, 3.0])


if __name__ == "__main__":
    unittest.main()
