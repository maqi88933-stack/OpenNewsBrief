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
    # 这里用一个假的 edge_tts 通信对象，方便测试 _save_tts 的分支。
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

    def test_load_config_creates_default_series(self):
        config = deep_series.load_config(self.config_path)

        self.assertTrue(config["series"][0]["title"])
        self.assertTrue(config["series"][0]["episodes"])
        self.assertTrue(os.path.exists(self.config_path))

    def test_save_config_round_trips_new_series_and_episode(self):
        config = {
            "series": [
                {
                    "title": "测试系列",
                    "description": "测试说明",
                    "episodes": [{"title": "测试主题", "question": "测试问题"}],
                }
            ]
        }

        deep_series.save_config(config, self.config_path)
        loaded = deep_series.load_config(self.config_path)

        self.assertEqual(loaded, config)

    def test_build_search_keywords_contains_multiple_angles(self):
        keywords = deep_series.build_search_keywords(
            {"title": "AI 未来三年系列"},
            {"title": "AI 为什么会替代搜索？", "question": "AI 为什么会替代搜索？"},
        )

        self.assertGreaterEqual(len(keywords), 10)
        self.assertIn("核心机制", "\n".join(keywords))
        self.assertIn("商业模式", "\n".join(keywords))
        self.assertIn("监管 风险", "\n".join(keywords))
        self.assertIn("case study", "\n".join(keywords))

    @patch("deep_series.call_llm", return_value="研究报告正文")
    def test_generate_research_report_includes_series_and_question(self, mock_llm):
        deep_series.generate_research_report(
            {"title": "AI 未来三年系列"},
            {"title": "AI 为什么会替代搜索？", "question": "AI 为什么会替代搜索？"},
            [{"title": "Source", "link": "https://example.com", "content": "content"}],
        )

        prompt = mock_llm.call_args.args[0]
        self.assertIn("AI 未来三年系列", prompt)
        self.assertIn("AI 为什么会替代搜索？", prompt)
        self.assertIn("核心问题", prompt)
        self.assertIn("背景", prompt)
        self.assertIn("争议", prompt)

    def test_call_llm_accepts_responses_api_content_blocks(self):
        fake_llm = types.SimpleNamespace(
            invoke=MagicMock(
                return_value=types.SimpleNamespace(
                    content=[
                        {"type": "text", "text": "第一段研究报告。"},
                        {"type": "reasoning", "summary": "内部推理不应进入正文"},
                        {"type": "text", "text": "第二段研究报告。"},
                    ]
                )
            )
        )
        fake_factory = MagicMock()
        fake_factory.return_value.getDeepseek.return_value = fake_llm

        with patch("util.llm.LLmFactory", fake_factory):
            result = deep_series.call_llm("提示词", "资料正文")

        # Responses API 可能返回内容块列表，深度系列只需要其中可见的文本块。
        self.assertEqual(result, "第一段研究报告。\n第二段研究报告。")

    @patch("deep_series.call_llm", return_value="男：先给结论。\n女：为什么？")
    def test_generate_dialogue_script_prompt_locks_first_30_seconds(self, mock_llm):
        deep_series.generate_dialogue_script(
            {"title": "AI 如何重构企业"},
            {"title": "未来公司可能只需要 3 个人", "question": "未来公司可能只需要 3 个人"},
            "研究报告",
            "审校意见",
        )

        prompt = mock_llm.call_args.args[0]
        # 留存优化必须写进脚本提示词，而不是只靠人工写稿时记住。
        self.assertIn("90-120秒", prompt)
        self.assertIn("前3秒", prompt)
        self.assertIn("前30秒", prompt)
        self.assertIn("反常识结论", prompt)
        self.assertIn("前4句", prompt)
        self.assertIn("冲突感", prompt)
        self.assertIn("损失感", prompt)
        self.assertIn("反问", prompt)
        self.assertIn("每句不超过22个字", prompt)
        self.assertIn("不要使用“想象一下”", prompt)
        self.assertIn("不要使用“今天我们探讨”", prompt)
        # 深度系列只保留两个主持人的聊天感，避免“主持人对话 + 旁白”的第三角色混入。
        self.assertNotIn("旁白", prompt)
        self.assertIn("女：/男：", prompt)

    def test_clean_script_output_strips_headers_and_keeps_dialogue(self):
        raw = "---\n\n### 标题\n女：搜索正在变成答案入口。\n\n男：未来的软件入口会变成 AI 对话。\n"
        cleaned = deep_series.clean_script_output(raw)

        self.assertFalse(cleaned.startswith("---"))
        self.assertNotIn("###", cleaned)
        self.assertEqual(cleaned, "女：搜索正在变成答案入口。\n\n男：未来的软件入口会变成 AI 对话。")

    def test_parse_dialogue_script_extracts_speakers(self):
        script = "女：搜索正在变成答案入口。\n男：未来的软件入口会变成 AI 对话。\n旁白：这就是变化。"

        segments = deep_series.parse_dialogue_script(script)

        self.assertEqual([item["speaker"] for item in segments], ["female", "male", "male"])
        self.assertIn("未来的软件入口会变成 AI 对话", segments[1]["text"])
        self.assertNotIn("旁白", deep_series.clean_script_output(script))

    def test_parse_dialogue_script_handles_markdown_bold_roles_and_preamble(self):
        script = (
            "### AI 未来三年系列\n"
            "**女：** 2026 年，搜索会被重新定义。\n"
            "**男：** 入口会从搜索框转向对话框。\n"
            "**旁白：** 这不是简单替代，而是重构。\n"
        )

        cleaned = deep_series.clean_script_output(script)
        segments = deep_series.parse_dialogue_script(script)

        self.assertNotIn("###", cleaned)
        self.assertEqual([item["speaker"] for item in segments], ["female", "male", "male"])
        self.assertIn("2026 年", segments[0]["text"])
        self.assertIn("对话框", segments[1]["text"])
        self.assertNotIn("女：", segments[0]["text"])
        self.assertNotIn("旁白", cleaned)

    def test_save_tts_uses_chattts_for_gendered_dialogue_when_chattts_enabled(self):
        fake_chattts = types.SimpleNamespace(synthesize_text=MagicMock())
        fake_edge_tts = types.SimpleNamespace(Communicate=FakeCommunicate)
        deep_series.DEEP_TTS_ENGINE = "chattts"

        with patch.dict(
            sys.modules,
            {
                "audioContent.chattts_engine": fake_chattts,
                "edge_tts": fake_edge_tts,
            },
        ):
            asyncio.run(deep_series._save_tts("女：你好。", "female.mp3", deep_series.FEMALE_VOICE, role="female"))
            asyncio.run(deep_series._save_tts("男：你好。", "male.mp3", deep_series.MALE_VOICE, role="male"))

        fake_chattts.synthesize_text.assert_any_call("女：你好。", "female.mp3", role="female")
        fake_chattts.synthesize_text.assert_any_call("男：你好。", "male.mp3", role="male")
        self.assertEqual(fake_chattts.synthesize_text.call_count, 2)
        self.assertEqual(FakeCommunicate.calls, [])

    def test_save_tts_keeps_chattts_for_narrator(self):
        fake_chattts = types.SimpleNamespace(synthesize_text=MagicMock())
        fake_edge_tts = types.SimpleNamespace(Communicate=FakeCommunicate)
        deep_series.DEEP_TTS_ENGINE = "chattts"

        with patch.dict(
            sys.modules,
            {
                "audioContent.chattts_engine": fake_chattts,
                "edge_tts": fake_edge_tts,
            },
        ):
            asyncio.run(deep_series._save_tts("旁白：开场。", "narrator.mp3", deep_series.MALE_VOICE, role="narrator"))

        fake_chattts.synthesize_text.assert_called_once_with("旁白：开场。", "narrator.mp3", role="narrator")
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
        self.assertEqual(len(segment_paths), 4)
        self.assertTrue(segment_paths[1].endswith("_pause.mp3"))
        self.assertTrue(segment_paths[-1].endswith("_ending_silence.mp3"))
        timing_segments = mock_timing.call_args.args[1]
        self.assertAlmostEqual(timing_segments[0]["duration"], 1.0 + deep_series.DEEP_DIALOGUE_PAUSE_SECONDS)
        self.assertAlmostEqual(timing_segments[1]["duration"], 1.0 + deep_series.DEEP_FINAL_SILENCE_SECONDS)

    def test_convert_dialogue_to_audio_cleans_stale_segments_and_uses_roles(self):
        script_path = os.path.join(self.tmpdir, "script.md")
        output_path = os.path.join(self.tmpdir, "dialogue.mp3")
        segment_dir = os.path.join(self.tmpdir, "dialogue_segments")
        os.makedirs(segment_dir, exist_ok=True)
        old_path = os.path.join(segment_dir, "999_old.mp3")
        with open(old_path, "wb") as f:
            f.write(b"old")
        with open(script_path, "w", encoding="utf-8") as f:
            f.write("### 标题\n**女：** 第一段。\n**男：** 第二段。")

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
        timing_segments = mock_timing.call_args.args[1]
        self.assertEqual([item["role"] for item in timing_segments], ["female", "male"])

    def test_convert_dialogue_to_audio_keeps_one_dialogue_line_as_one_tts_clip(self):
        script_path = os.path.join(self.tmpdir, "script.md")
        output_path = os.path.join(self.tmpdir, "dialogue.mp3")
        long_text = "当分工、协调、复盘都能被智能体接管时，人类在公司里，剩下的核心价值到底是什么？"
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(f"男：{long_text}")

        saved_texts = []

        async def fake_save_tts(text, output_path, _voice, role="narrator"):
            saved_texts.append(text)
            with open(output_path, "wb") as f:
                f.write(role.encode("utf-8"))

        with patch.object(deep_series, "_save_tts", side_effect=fake_save_tts), \
                patch.object(deep_series, "create_silence_audio", side_effect=lambda path, _duration: path), \
                patch("audioContent.news_to_audio.get_audio_duration", return_value=1.0), \
                patch("audioContent.news_to_audio.concat_audio_files", return_value=output_path) as mock_concat, \
                patch("audioContent.news_to_audio.write_timing_file", return_value=output_path + ".timing.json") as mock_timing:
            deep_series.convert_dialogue_to_audio(script_path, output_path)

        # 同一行台词保持一次 TTS，避免多段音频边界造成语气重置和卡顿感。
        self.assertEqual(saved_texts, [long_text])
        segment_paths = mock_concat.call_args.args[0]
        pause_paths = [path for path in segment_paths if path.endswith("_pause.mp3")]
        self.assertEqual(pause_paths, [])
        self.assertEqual(len(segment_paths), 2)
        timing_segments = mock_timing.call_args.args[1]
        self.assertEqual([item["text"] for item in timing_segments], [long_text])
        self.assertAlmostEqual(timing_segments[0]["duration"], 1.0 + deep_series.DEEP_FINAL_SILENCE_SECONDS)

    def test_convert_dialogue_to_audio_merges_adjacent_same_speaker_lines(self):
        script_path = os.path.join(self.tmpdir, "script.md")
        output_path = os.path.join(self.tmpdir, "dialogue.mp3")
        with open(script_path, "w", encoding="utf-8") as f:
            f.write("男：第一句。\n男：第二句。\n女：回应。")

        saved_texts = []

        async def fake_save_tts(text, output_path, _voice, role="narrator"):
            saved_texts.append((role, text))
            with open(output_path, "wb") as f:
                f.write(role.encode("utf-8"))

        with patch.object(deep_series, "_save_tts", side_effect=fake_save_tts), \
                patch.object(deep_series, "create_silence_audio", side_effect=lambda path, _duration: path), \
                patch("audioContent.news_to_audio.get_audio_duration", return_value=1.0), \
                patch("audioContent.news_to_audio.concat_audio_files", return_value=output_path) as mock_concat, \
                patch("audioContent.news_to_audio.write_timing_file", return_value=output_path + ".timing.json") as mock_timing:
            deep_series.convert_dialogue_to_audio(script_path, output_path)

        # 同一主持连续台词合成一次，减少短音频边界造成的停顿感。
        self.assertEqual(saved_texts, [("male", "第一句。第二句。"), ("female", "回应。")])
        segment_paths = [os.path.basename(path) for path in mock_concat.call_args.args[0]]
        self.assertEqual(segment_paths, ["000_male.mp3", "000_pause.mp3", "001_female.mp3", "002_ending_silence.mp3"])
        timing_segments = mock_timing.call_args.args[1]
        self.assertEqual([item["role"] for item in timing_segments], ["male", "female"])
        self.assertAlmostEqual(timing_segments[0]["duration"], 1.0 + deep_series.DEEP_DIALOGUE_PAUSE_SECONDS)
        self.assertAlmostEqual(timing_segments[1]["duration"], 1.0 + deep_series.DEEP_FINAL_SILENCE_SECONDS)

    def test_create_text_card_uses_widescreen_ios_canvas(self):
        image_path = os.path.join(self.tmpdir, "slide.png")
        deep_series.create_text_card("标题", "系列 · 女主持", "这是一段用于检查版式的正文。", image_path)

        from PIL import Image

        with Image.open(image_path) as image:
            self.assertEqual(image.size, (1920, 1080))
            self.assertEqual(image.getpixel((20, 20)), (245, 245, 247))
            self.assertEqual(image.getpixel((140, 150)), (0, 122, 255))
            self.assertEqual(image.getpixel((300, 300)), (255, 255, 255))

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

    @patch(
        "deep_series.call_llm",
        return_value='{"title":"搜索入口正在消失","desc":"简介","tags":"AI,深度,口播","cover_text":"深度解析","cover_prompt":"封面提示","comment_question":"你更赞同 A 还是 B？为什么","title_options":["搜索不再是入口","AI正在改写搜索","答案入口变了"],"cover_options":["封面一","封面二","封面三"]}',
    )
    def test_generate_publish_assets_writes_ai_metadata(self, mock_llm):
        script_path = os.path.join(self.tmpdir, "script.md")
        with open(script_path, "w", encoding="utf-8") as f:
            f.write("女：第一句。\n男：第二句。")
        result = {"script_path": script_path, "video_path": os.path.join(self.tmpdir, "demo.mp4")}
        series = {"title": "AI 未来三年系列"}
        episode = {"title": "AI 为什么会替代搜索？"}

        assets = deep_series.generate_publish_assets(series, episode, result)

        prompt = mock_llm.call_args.args[0]
        self.assertIn("短视频发布信息生成助手", prompt)
        self.assertIn("cover_text", prompt)
        self.assertIn("comment_question", prompt)
        # 发布标题、封面和开头承诺必须一致，减少点击后发现内容不符造成的秒退。
        self.assertIn("视频前3秒", prompt)
        self.assertIn("同一个承诺", prompt)
        # B站标题的主题部分可以改写，但必须基于原始主题且由上传侧统一补系列前缀。
        self.assertIn("原始主题标题", prompt)
        self.assertIn("不需要完全照抄", prompt)
        self.assertIn("更吸引眼球", prompt)
        self.assertIn("系列名称：主题名称", prompt)
        self.assertIn("不要把系列名称写进 title", prompt)
        self.assertIn("标题", prompt)
        self.assertIn("封面文案", prompt)
        self.assertEqual(assets["title"], "搜索入口正在消失")
        self.assertLessEqual(len(assets["title"]), 18)
        self.assertGreaterEqual(len(assets["cover_text"]), 6)
        self.assertLessEqual(len(assets["cover_text"]), 10)
        self.assertIn("互动问题", assets["desc"])
        self.assertTrue(assets["cover_path"])
        self.assertTrue(os.path.exists(assets["cover_path"]))
        self.assertTrue(os.path.exists(os.path.join(self.tmpdir, "publish_assets.json")))

    @patch("deep_series.step_video", return_value="demo.mp4")
    @patch("deep_series.create_deep_slide_images", return_value=["cover.png", "section.png"])
    @patch("deep_series.convert_dialogue_to_audio", return_value="demo.mp3")
    @patch(
        "deep_series.call_llm",
        side_effect=[
            "研究报告",
            "事实审校",
            "结构审校",
            "钩子审校",
            "对话脚本",
            "脚本备注",
            "纪录片包",
        ],
    )
    @patch("deep_series.collect_research_sources", return_value=[{"title": "Source 1", "link": "https://example.com/1", "content": "AI search source"}])
    def test_run_episode_pipeline_returns_review_artifacts(self, _sources, _llm, _audio, _slides, _video):
        episode = {"title": "AI 为什么会替代搜索？", "question": "AI 为什么会替代搜索？"}
        series = {"title": "AI 未来三年系列", "description": "测试说明"}

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
                patch(
                    "deep_series.generate_publish_assets",
                    return_value={
                        "path": os.path.join(self.tmpdir, "publish_assets.json"),
                        "title": "Title",
                        "desc": "Desc",
                        "tags": "AI",
                        "cover_path": os.path.join(self.tmpdir, "cover.png"),
                    },
                ):
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

    def test_create_deep_slide_images_keeps_dialogue_segments_and_writes_durations(self):
        script_path = os.path.join(self.tmpdir, "script.md")
        audio_path = os.path.join(self.tmpdir, "dialogue.mp3")
        with open(script_path, "w", encoding="utf-8") as f:
            f.write("女：搜索正在变成答案入口。\n男：未来的软件入口会从搜索框变成对话。")
        with open(audio_path + ".timing.json", "w", encoding="utf-8") as f:
            f.write(
                '{"segments":[{"role":"female","slide_index":0,"duration":4.0,"text":"搜索正在变成答案入口。"},{"role":"male","slide_index":1,"duration":6.0,"text":"未来的软件入口会从搜索框变成对话。"}]}'
            )

        image_paths = deep_series.create_deep_slide_images(
            {"title": "AI未来三年系列"},
            {"title": "AI 为什么会替代搜索？"},
            script_path,
            audio_path,
        )

        # 6 秒段会被拆成两张短卡，避免单张画面停留过久。
        self.assertEqual(len(image_paths), 3)
        durations = deep_series.load_deep_slide_durations(image_paths)
        self.assertEqual(durations, [4.0, 3.0, 3.0])
        self.assertAlmostEqual(sum(durations), 10.0)
        self.assertTrue(os.path.exists(os.path.join(os.path.dirname(image_paths[0]), "slide_durations.json")))

    def test_create_deep_slide_images_uses_soft_theme_colors(self):
        script_path = os.path.join(self.tmpdir, "soft_theme_script.md")
        audio_path = os.path.join(self.tmpdir, "soft_theme_audio.mp3")
        with open(script_path, "w", encoding="utf-8") as f:
            f.write("濂筹細A\n鐢凤細B")
        with open(audio_path + ".timing.json", "w", encoding="utf-8") as f:
            f.write(
                '{"segments":[{"role":"female","slide_index":0,"duration":4.0,"text":"A"},{"role":"male","slide_index":1,"duration":4.0,"text":"B"}]}'
            )

        captured = []

        def fake_create_text_card(title, subtitle, body, output_path, accent="#007AFF", slide_index=None, slide_total=None):
            # 这里只检查传入的主题色，不改画面逻辑。
            captured.append(accent)
            with open(output_path, "wb") as f:
                f.write(b"png")
            return output_path

        with patch("deep_series.create_text_card", side_effect=fake_create_text_card), \
                patch.object(deep_series, "write_deep_slide_durations", return_value="slide_durations.json"):
            deep_series.create_deep_slide_images(
                {"title": "AI未来三年系列"},
                {"title": "AI 为什么会替代搜索？"},
                script_path,
                audio_path,
            )

        self.assertIn("#C79AA8", captured)
        self.assertIn("#8FA8C1", captured)
        self.assertNotIn("#FF2D55", captured)
        self.assertNotIn("#007AFF", captured)

    def test_build_deep_visual_slide_plan_maps_legacy_narrator_to_male(self):
        script_path = os.path.join(self.tmpdir, "legacy_narrator_script.md")
        audio_path = os.path.join(self.tmpdir, "legacy_narrator_audio.mp3")
        with open(script_path, "w", encoding="utf-8") as f:
            f.write("旁白：旧脚本里的第三角色。")
        with open(audio_path + ".timing.json", "w", encoding="utf-8") as f:
            f.write('{"segments":[{"role":"narrator","duration":3.0,"text":"旧脚本里的第三角色。"}]}')

        slide_plan = deep_series.build_deep_visual_slide_plan(script_path, audio_path)

        # 旧 timing 里的 narrator 也要归并，避免重渲染时字幕重新出现“旁白”。
        self.assertEqual([item["speaker"] for item in slide_plan], ["male"])

    def test_build_deep_visual_slide_plan_splits_long_timing_segments(self):
        script_path = os.path.join(self.tmpdir, "script.md")
        audio_path = os.path.join(self.tmpdir, "dialogue.mp3")
        with open(script_path, "w", encoding="utf-8") as f:
            f.write("男：一段长口播。")
        long_text = "未来公司只需要三个人。第一个是定方向的人。第二个是搭系统的人。第三个是做增长的人。"
        with open(audio_path + ".timing.json", "w", encoding="utf-8") as f:
            f.write(
                '{"segments":[{"role":"male","slide_index":0,"duration":12.4,"text":"'
                + long_text
                + '"}]}'
            )

        slide_plan = deep_series.build_deep_visual_slide_plan(script_path, audio_path)

        # 单段长音频要拆成多张短卡，避免前30秒一直停在同一张文字页。
        self.assertGreaterEqual(len(slide_plan), 4)
        self.assertTrue(all(item["duration"] <= deep_series.DEEP_VISUAL_MAX_SECONDS for item in slide_plan))
        self.assertAlmostEqual(sum(item["duration"] for item in slide_plan), 12.4)
        self.assertEqual({item["speaker"] for item in slide_plan}, {"male"})
        self.assertLessEqual(max(len(item["text"]) for item in slide_plan[:3]), 24)

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
