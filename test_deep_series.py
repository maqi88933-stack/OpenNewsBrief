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

    def test_build_search_keywords_adds_short_company_abf_terms(self):
        keywords = deep_series.build_search_keywords(
            {"title": "AI时代的隐形地基"},
            {
                "title": "味之素：味精公司为什么成了高端芯片底座",
                "question": "味之素为什么能成为 AI 芯片供应链里的隐形公司？重点探讨 ABF 绝缘材料如何支撑高性能 CPU、GPU 的封装基板。",
            },
            attempt=2,
        )

        joined = "\n".join(keywords)
        self.assertIn("味之素 ABF", joined)
        self.assertIn("Ajinomoto ABF", joined)
        self.assertIn("Ajinomoto Build-up Film", joined)
        self.assertLess(max(len(item) for item in keywords[:4]), 60)

    def test_retry_search_uses_audit_gaps_and_keeps_previous_sources(self):
        episode = {
            "title": "味之素：味精公司为什么成了高端芯片底座",
            "question": "味之素为什么能成为 AI 芯片供应链里的隐形公司？重点探讨 ABF 绝缘材料。",
        }
        series = {"title": "AI时代的隐形地基"}
        first_sources = [
            {"title": "来源一", "link": "https://example.com/1", "content": "资料一"},
            {"title": "来源二", "link": "https://example.com/2", "content": "资料二"},
        ]
        second_sources = [
            {"title": "味之素官方 ABF", "link": "https://example.com/official", "content": "官方资料"},
        ]
        audit_gap = "有效来源不足，缺少味之素官方资料、FC-BGA 封装基板资料和行业报告。"

        with patch("deep_series.collect_research_sources", side_effect=[first_sources, second_sources]) as mock_collect, \
                patch("deep_series.generate_research_report", return_value="研究报告") as mock_report, \
                patch("deep_series.audit_research", side_effect=[audit_gap, "事实审校通过"]):
            review = deep_series.run_research_review_loop(series, episode)

        # 第二轮必须带上上一轮审校指出的缺口，避免继续原样搜索同一批关键词。
        self.assertEqual(mock_collect.call_args_list[1].kwargs["audit_report"], audit_gap)
        self.assertIn("有效来源不足", "；".join(mock_collect.call_args_list[1].kwargs["quality"]["reasons"]))
        # 第二轮生成报告应使用累计后的 3 条来源，而不是丢掉第一轮已经抓到的 2 条。
        self.assertEqual(len(mock_report.call_args_list[1].args[2]), 3)
        self.assertFalse(review["blocked"])
        self.assertEqual(review["quality"]["source_count"], 3)

    def test_research_quality_blocks_sparse_sources_but_keeps_fact_warnings_soft(self):
        sources = [{"title": "弱来源", "link": "https://example.com/1", "content": "只有一条资料"}]

        report = deep_series.assess_research_quality(sources, "事实支撑不够具体，缺少官方来源。")

        self.assertTrue(report["blocked"])
        self.assertIn("有效来源不足", "；".join(report["reasons"]))
        self.assertNotIn("事实支撑不足", "；".join(report["reasons"]))
        self.assertIn("事实支撑不足", "；".join(report["warnings"]))

    def test_research_quality_allows_fact_warning_when_sources_are_enough(self):
        sources = [
            {"title": "来源一", "link": "https://example.com/1", "content": "资料一"},
            {"title": "来源二", "link": "https://example.com/2", "content": "资料二"},
            {"title": "来源三", "link": "https://example.com/3", "content": "资料三"},
        ]

        report = deep_series.assess_research_quality(sources, "事实支撑不足，需要保守表达。")

        self.assertFalse(report["blocked"])
        self.assertEqual(report["reasons"], [])
        self.assertIn("事实支撑不足", report["warnings"])

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

        # call_llm 在函数内部延迟导入 util.llm，这里直接注入模块，避免包属性未挂载导致 mock 失败。
        with patch.dict(sys.modules, {"util.llm": types.SimpleNamespace(LLmFactory=fake_factory)}):
            result = deep_series.call_llm("提示词", "资料正文")

        # Responses API 可能返回内容块列表，深度系列只需要其中可见的文本块。
        self.assertEqual(result, "第一段研究报告。\n第二段研究报告。")

    def test_call_llm_streams_chunks_then_returns_full_text(self):
        fake_llm = types.SimpleNamespace(
            stream=MagicMock(
                return_value=[
                    types.SimpleNamespace(content="第一段\n"),
                    types.SimpleNamespace(content="第二段"),
                ]
            ),
            invoke=MagicMock(),
        )
        fake_factory = MagicMock()
        fake_factory.return_value.getDeepseek.return_value = fake_llm

        # call_llm 在函数内部延迟导入 util.llm，这里直接注入模块，避免包属性未挂载导致 mock 失败。
        with patch.dict(sys.modules, {"util.llm": types.SimpleNamespace(LLmFactory=fake_factory)}):
            result = deep_series.call_llm("提示词", "资料正文")

        # 深度系列内部流式接收，但对调用方仍一次性返回完整文本，避免改动上层调用契约。
        self.assertEqual(result, "第一段\n第二段")
        fake_llm.stream.assert_called_once()
        fake_llm.invoke.assert_not_called()

    def test_call_llm_retries_retryable_timeout_error(self):
        fake_llm = types.SimpleNamespace(invoke=MagicMock(side_effect=[
            RuntimeError("Error code: 524 - retryable timeout"),
            MagicMock(content="重试后成功"),
        ]))
        fake_factory = MagicMock()
        fake_factory.return_value.getDeepseek.return_value = fake_llm

        # call_llm 在函数内部延迟导入 util.llm，这里直接注入模块，避免包属性未挂载导致 mock 失败。
        with patch.dict(sys.modules, {"util.llm": types.SimpleNamespace(LLmFactory=fake_factory)}), \
                patch("deep_series.time.sleep") as mock_sleep:
            result = deep_series.call_llm("提示词", "资料正文")

        self.assertEqual(result, "重试后成功")
        self.assertEqual(fake_llm.invoke.call_count, 2)
        mock_sleep.assert_called_once()

    @patch("deep_series.call_llm", return_value="男：先给结论。\n女：为什么？")
    def test_generate_dialogue_script_prompt_locks_first_30_seconds(self, mock_llm):
        deep_series.generate_dialogue_script(
            {"title": "AI 如何重构企业"},
            {"title": "未来公司可能只需要 3 个人", "question": "未来公司可能只需要 3 个人"},
            "研究报告",
            "审校意见",
        )

        prompt = mock_llm.call_args_list[0].args[0]
        # 留存优化必须写进脚本提示词，而不是只靠人工写稿时记住。
        self.assertIn("120-180秒", prompt)
        self.assertIn("前3秒", prompt)
        self.assertIn("前30秒", prompt)
        self.assertIn("优先使用尖锐疑问句或反常识结论", prompt)
        self.assertIn("冲突、代价或反直觉", prompt)
        self.assertIn("反常识结论", prompt)
        self.assertIn("前4句", prompt)
        self.assertIn("冲突感", prompt)
        self.assertIn("损失感", prompt)
        self.assertIn("反问", prompt)
        self.assertIn("前15秒", prompt)
        self.assertIn("不要写成 4 个口号式短句", prompt)
        self.assertIn("35 到 60 个汉字", prompt)
        self.assertNotIn("每句不超过22个字", prompt)
        self.assertIn("每次发言", prompt)
        self.assertIn("2 到 4 句", prompt)
        self.assertIn("不要连续输出同一个主持人的多行发言", prompt)
        self.assertIn("不要使用“你有没有想过”", prompt)
        self.assertIn("不要使用“想象一下”", prompt)
        self.assertIn("不要使用“今天我们探讨”", prompt)
        # 深度系列只保留两个主持人的聊天感，避免“主持人对话 + 旁白”的第三角色混入。
        self.assertNotIn("旁白", prompt)
        self.assertIn("女：/男：", prompt)

    def test_assess_opening_hook_flags_stiff_first_seconds(self):
        script = (
            "女：一家味精公司，凭什么卡进AI芯片供应链？反常识结论是：只盯着晶圆，你可能漏掉真正的封装代价。\n"
            "男：味精和GPU有什么关系？芯片不是英伟达设计、台积电制造就完了吗？\n"
            "女：核心答案是ABF，味之素积层绝缘膜。"
        )

        report = deep_series.assess_opening_hook(script)

        self.assertTrue(report["blocked"])
        self.assertIn("不自然", "；".join(report["reasons"]))
        self.assertIn("前3秒", report["scope"])

    def test_polish_opening_hook_replaces_first_four_dialogue_lines_only(self):
        script = (
            "女：一家味精公司，凭什么卡进AI芯片供应链？反常识结论是：只盯着晶圆，你可能漏掉真正的封装代价。\n"
            "男：味精和GPU有什么关系？芯片不是英伟达设计、台积电制造就完了吗？\n"
            "女：核心答案是ABF，味之素积层绝缘膜。\n"
            "男：ABF到底藏在哪？它是GPU的一部分吗？\n"
            "女：不是。晶圆厂做出裸芯片后，还要靠封装基板连接主板、电源和外部信号。"
        )
        polished_hook = (
            "女：做味精的公司，怎么会托住AI芯片的底座？答案藏在GPU下面那层封装基板里。\n"
            "男：等等，味精和GPU中间到底隔着多少层关系？\n"
            "女：关键是ABF，味之素积层绝缘膜。它不是芯片，却决定高端基板能不能把信号和供电稳稳接出去。\n"
            "男：所以我们平时只看晶圆，其实漏掉了封装材料这一层？"
        )

        with patch("deep_series.call_llm", return_value=polished_hook) as mock_llm:
            revised, report = deep_series.polish_opening_hook_with_review(
                {"title": "AI时代的隐形地基"},
                {"title": "味之素：味精公司为什么成了高端芯片底座"},
                script,
                "研究报告",
                "审校意见",
            )

        self.assertIn("做味精的公司，怎么会托住AI芯片的底座", revised)
        self.assertIn("晶圆厂做出裸芯片后", revised)
        self.assertNotIn("凭什么卡进", revised)
        self.assertEqual(report["attempts"], 1)
        prompt = mock_llm.call_args_list[0].args[0]
        self.assertIn("前3秒", prompt)
        self.assertIn("前4句", prompt)
        self.assertIn("不要使用“凭什么”", prompt)

    def test_generate_dialogue_script_with_duration_guard_rewrites_long_script(self):
        long_line = "女：" + "这是一个过长观点。" * 140
        short_line = "女：第一句先给反常识结论。\n男：为什么这件事会影响普通公司？"

        with patch("deep_series.call_llm", side_effect=[long_line, short_line]) as mock_llm, \
                patch("deep_series.polish_opening_hook_with_review", return_value=(short_line, {"attempts": 0})), \
                patch("deep_series.polish_script_with_retention_review", return_value=(short_line, {"blocked": False, "attempts": 0})):
            script, report = deep_series.generate_dialogue_script_with_duration_guard(
                {"title": "AI 如何重构企业"},
                {"title": "测试主题", "question": "测试问题"},
                "研究报告",
                "审校意见",
            )

        self.assertEqual(script, short_line)
        self.assertFalse(report["blocked"])
        self.assertEqual(report["attempts"], 2)
        self.assertLessEqual(report["estimated_seconds"], deep_series.DEEP_TARGET_MAX_SECONDS)
        self.assertIn("压缩", mock_llm.call_args.args[0])

    def test_generate_dialogue_script_with_duration_guard_polishes_opening(self):
        first_script = (
            "女：一家味精公司，凭什么卡进AI芯片供应链？反常识结论是：只盯着晶圆，你可能漏掉真正的封装代价。\n"
            "男：味精和GPU有什么关系？\n"
            "女：核心答案是ABF。\n"
            "男：ABF到底藏在哪？\n"
            "女：后文继续解释。"
        )
        polished_hook = (
            "女：做味精的公司，怎么会托住AI芯片的底座？答案藏在GPU下面那层封装基板里。\n"
            "男：等等，味精和GPU中间到底隔着多少层关系？\n"
            "女：关键是ABF，味之素积层绝缘膜。它不是芯片，却负责高端基板里的绝缘和连接秩序。\n"
            "男：所以真正容易被低估的，是封装材料这一层？"
        )

        with patch("deep_series.call_llm", side_effect=[first_script, polished_hook]), \
                patch("deep_series.polish_script_with_retention_review", return_value=(polished_hook, {"blocked": False, "attempts": 0})):
            script, report = deep_series.generate_dialogue_script_with_duration_guard(
                {"title": "AI时代的隐形地基"},
                {"title": "味之素：味精公司为什么成了高端芯片底座", "question": "味之素为什么能成为 AI 芯片供应链里的隐形公司？"},
                "研究报告",
                "审校意见",
            )

        self.assertIn("做味精的公司，怎么会托住AI芯片的底座", script)
        self.assertNotIn("凭什么卡进", script)
        self.assertEqual(report["hook_review"]["attempts"], 1)

    def test_generate_dialogue_script_runs_full_retention_review(self):
        script = (
            "女：做味精的公司，怎么会托住AI芯片的底座？答案藏在GPU下面那层封装基板里。\n"
            "男：等等，味精和GPU中间到底隔着多少层关系？\n"
            "女：关键是ABF，味之素积层绝缘膜。它不是芯片，却负责高端基板里的绝缘和连接秩序。\n"
            "男：所以真正容易被低估的，是封装材料这一层？"
        )
        review_json = '{"passed":true,"score":8,"reasons":[],"suggestions":["节奏可以继续保持"]}'

        with patch("deep_series.call_llm", side_effect=[script, review_json]) as mock_llm, \
                patch("deep_series.polish_opening_hook_with_review", return_value=(script, {"attempts": 0})):
            result_script, report = deep_series.generate_dialogue_script_with_duration_guard(
                {"title": "AI时代的隐形地基"},
                {"title": "味之素：味精公司为什么成了高端芯片底座"},
                "研究报告",
                "审校意见",
            )

        self.assertEqual(result_script, script)
        self.assertIn("retention_review", report)
        self.assertFalse(report["retention_review"]["blocked"])
        self.assertIn("整体留存", mock_llm.call_args.args[0])
        self.assertIn("用户是否愿意听完", mock_llm.call_args.args[0])

    def test_generate_dialogue_script_rewrites_when_retention_review_fails(self):
        weak_script = "女：今天我们来讲一个产业知识。\n男：好，继续。"
        better_script = (
            "女：AI芯片真正容易被低估的，不是晶圆，而是GPU下面那层封装材料。\n"
            "男：材料怎么会影响算力释放？这听起来比芯片设计还绕。\n"
            "女：因为供电、信号和高密度连接都要从封装基板出去，ABF就是其中一层关键绝缘材料。"
        )
        bad_review = '{"passed":false,"score":4,"reasons":["开头弱","中段缺少悬念"],"suggestions":["加强冲突和追问"]}'
        good_review = '{"passed":true,"score":8,"reasons":[],"suggestions":[]}'

        with patch("deep_series.call_llm", side_effect=[weak_script, bad_review, better_script, good_review]), \
                patch("deep_series.polish_opening_hook_with_review", side_effect=[(weak_script, {"attempts": 0}), (better_script, {"attempts": 0})]):
            result_script, report = deep_series.generate_dialogue_script_with_duration_guard(
                {"title": "AI时代的隐形地基"},
                {"title": "味之素：味精公司为什么成了高端芯片底座"},
                "研究报告",
                "审校意见",
            )

        self.assertEqual(result_script, better_script)
        self.assertEqual(report["retention_review"]["attempts"], 2)
        self.assertFalse(report["retention_review"]["blocked"])

    def test_validate_audio_duration_warns_oversized_timing_without_blocking(self):
        audio_path = os.path.join(self.tmpdir, "dialogue.mp3")
        with open(audio_path, "wb") as f:
            f.write(b"audio")
        with open(audio_path + ".timing.json", "w", encoding="utf-8") as f:
            f.write('{"total_duration": 180.0, "segments": []}')

        report = deep_series.validate_deep_audio_duration(audio_path)

        self.assertFalse(report["blocked"])
        self.assertEqual(report["actual_seconds"], 180.0)

        with open(audio_path + ".timing.json", "w", encoding="utf-8") as f:
            f.write('{"total_duration": 181.0, "segments": []}')

        report = deep_series.validate_deep_audio_duration(audio_path)

        self.assertTrue(report["blocked"])
        self.assertEqual(report["actual_seconds"], 181.0)
        self.assertIn("超过", "；".join(report["reasons"]))

    def test_clean_script_output_strips_headers_and_keeps_dialogue(self):
        raw = "---\n\n### 标题\n女：搜索正在变成答案入口。\n\n男：未来的软件入口会变成 AI 对话。\n"
        cleaned = deep_series.clean_script_output(raw)

        self.assertFalse(cleaned.startswith("---"))
        self.assertNotIn("###", cleaned)
        self.assertEqual(cleaned, "女：搜索正在变成答案入口。\n\n男：未来的软件入口会变成 AI 对话。")

    def test_clean_script_output_merges_adjacent_same_speaker_lines(self):
        raw = (
            "女：没错，所以只能说是信号。\n"
            "女：安卓、国产手机、Google和微软，也都在抢这层入口。\n\n"
            "女：PC可能先成熟。\n"
            "女：手机决定规模。\n"
            "女：家庭设备会成为分支。\n\n"
            "男：家庭设备也算操作系统？\n"
            "女：不算传统OS。\n"
            "女：但可能成为家庭AI中枢。\n"
        )

        cleaned = deep_series.clean_script_output(raw)

        # 清洗脚本时就合并同主持人的连续发言，避免保存到 script.md 后看起来像碎片列表。
        self.assertEqual(
            cleaned,
            "女：没错，所以只能说是信号。安卓、国产手机、Google和微软，也都在抢这层入口。PC可能先成熟。手机决定规模。家庭设备会成为分支。\n\n"
            "男：家庭设备也算操作系统？\n"
            "女：不算传统OS。但可能成为家庭AI中枢。",
        )
        self.assertNotIn("女：PC可能先成熟。\n女：手机决定规模。", cleaned)

    def test_parse_dialogue_script_extracts_speakers(self):
        script = "女：搜索正在变成答案入口。\n男：未来的软件入口会变成 AI 对话。\n旁白：这就是变化。"

        segments = deep_series.parse_dialogue_script(script)

        self.assertEqual([item["speaker"] for item in segments], ["female", "male"])
        self.assertIn("未来的软件入口会变成 AI 对话", segments[1]["text"])
        self.assertIn("这就是变化", segments[1]["text"])
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
        self.assertEqual([item["speaker"] for item in segments], ["female", "male"])
        self.assertIn("2026 年", segments[0]["text"])
        self.assertIn("对话框", segments[1]["text"])
        self.assertIn("这不是简单替代", segments[1]["text"])
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
        self.assertEqual(len(segment_paths), 5)
        self.assertTrue(segment_paths[0].endswith("_opening_silence.mp3"))
        self.assertTrue(segment_paths[2].endswith("_pause.mp3"))
        self.assertTrue(segment_paths[-1].endswith("_ending_silence.mp3"))
        timing_segments = mock_timing.call_args.args[1]
        self.assertAlmostEqual(
            timing_segments[0]["duration"],
            1.0 + deep_series.DEEP_OPENING_SILENCE_SECONDS + deep_series.DEEP_DIALOGUE_PAUSE_SECONDS,
        )
        self.assertAlmostEqual(timing_segments[1]["duration"], 1.0 + deep_series.DEEP_FINAL_SILENCE_SECONDS)

    def test_convert_dialogue_to_audio_adds_opening_silence_before_first_line(self):
        script_path = os.path.join(self.tmpdir, "script.md")
        output_path = os.path.join(self.tmpdir, "dialogue.mp3")
        with open(script_path, "w", encoding="utf-8") as f:
            f.write("女：第一句。")

        async def fake_save_tts(_text, output_path, _voice, role="narrator"):
            with open(output_path, "wb") as f:
                f.write(role.encode("utf-8"))

        silence_calls = []

        def fake_create_silence(path, duration):
            silence_calls.append((os.path.basename(path), duration))
            return path

        with patch.object(deep_series, "_save_tts", side_effect=fake_save_tts), \
                patch.object(deep_series, "create_silence_audio", side_effect=fake_create_silence), \
                patch("audioContent.news_to_audio.get_audio_duration", return_value=1.0), \
                patch("audioContent.news_to_audio.concat_audio_files", return_value=output_path) as mock_concat, \
                patch("audioContent.news_to_audio.write_timing_file", return_value=output_path + ".timing.json") as mock_timing:
            deep_series.convert_dialogue_to_audio(script_path, output_path)

        # 首句前的静音要真实进入拼接列表和 timing，视频才会在开口前留出自然空白。
        self.assertEqual(silence_calls[0], ("000_opening_silence.mp3", 0.8))
        self.assertTrue(mock_concat.call_args.args[0][0].endswith("_opening_silence.mp3"))
        timing_segments = mock_timing.call_args.args[1]
        self.assertAlmostEqual(timing_segments[0]["duration"], 1.0 + 0.8 + deep_series.DEEP_FINAL_SILENCE_SECONDS)

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
        self.assertTrue(segment_paths[0].endswith("_opening_silence.mp3"))
        self.assertEqual(len(segment_paths), 3)
        timing_segments = mock_timing.call_args.args[1]
        self.assertEqual([item["text"] for item in timing_segments], [long_text])
        self.assertAlmostEqual(
            timing_segments[0]["duration"],
            1.0 + deep_series.DEEP_OPENING_SILENCE_SECONDS + deep_series.DEEP_FINAL_SILENCE_SECONDS,
        )

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
        self.assertEqual(
            segment_paths,
            ["000_opening_silence.mp3", "000_male.mp3", "000_pause.mp3", "001_female.mp3", "002_ending_silence.mp3"],
        )
        timing_segments = mock_timing.call_args.args[1]
        self.assertEqual([item["role"] for item in timing_segments], ["male", "female"])
        self.assertAlmostEqual(
            timing_segments[0]["duration"],
            1.0 + deep_series.DEEP_OPENING_SILENCE_SECONDS + deep_series.DEEP_DIALOGUE_PAUSE_SECONDS,
        )
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

    @patch("deep_series.create_deep_cover_options", return_value=["cover1.png", "cover2.png", "cover3.png"])
    @patch("deep_series.create_deep_cover_image", return_value="cover.png")
    @patch(
        "deep_series.call_llm",
        side_effect=[
            '{"title":"搜索入口正在消失","desc":"简介","tags":"AI,深度,口播","cover_text":"深度解析","cover_prompt":"封面提示","comment_question":"你更赞同 A 还是 B？为什么","title_options":["搜索不再是入口","AI正在改写搜索","答案入口变了"],"cover_options":["封面一","封面二","封面三"]}',
            '{"passed":true,"score":8,"reasons":[],"suggestions":[]}',
        ],
    )
    def test_generate_publish_assets_writes_ai_metadata(self, mock_llm, _cover, _cover_options):
        script_path = os.path.join(self.tmpdir, "script.md")
        with open(script_path, "w", encoding="utf-8") as f:
            f.write("女：第一句。\n男：第二句。")
        result = {"script_path": script_path, "video_path": os.path.join(self.tmpdir, "demo.mp4")}
        series = {"title": "AI 未来三年系列"}
        episode = {"title": "AI 为什么会替代搜索？"}

        assets = deep_series.generate_publish_assets(series, episode, result)

        prompt = mock_llm.call_args_list[0].args[0]
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
        self.assertIn("关键词", assets["desc"])
        self.assertIn("publish_review", assets)
        self.assertTrue(assets["cover_path"])
        self.assertEqual(len(assets["cover_option_paths"]), 3)
        self.assertTrue(os.path.exists(os.path.join(self.tmpdir, "publish_assets.json")))

    @patch("deep_series.create_deep_cover_options", return_value=["cover1.png", "cover2.png", "cover3.png"])
    @patch("deep_series.create_deep_cover_image", return_value="cover.png")
    def test_generate_publish_assets_rewrites_weak_title_and_desc(self, _cover, _cover_options):
        script_path = os.path.join(self.tmpdir, "script.md")
        with open(script_path, "w", encoding="utf-8") as f:
            f.write("女：AI芯片真正容易被低估的，是GPU下面那层封装材料。")
        result = {"script_path": script_path, "video_path": os.path.join(self.tmpdir, "demo.mp4")}
        series = {"title": "AI时代的隐形地基"}
        episode = {"title": "味之素：味精公司为什么成了高端芯片底座", "question": "味之素为什么能成为 AI 芯片供应链里的隐形公司？"}

        with patch(
            "deep_series.call_llm",
            side_effect=[
                '{"title":"味之素介绍","desc":"本期介绍味之素。","tags":"AI","cover_text":"味之素","cover_prompt":"封面","comment_question":"你怎么看","title_options":["味之素介绍"],"cover_options":["味之素"]}',
                '{"passed":false,"score":4,"reasons":["标题点击欲不足","简介缺少搜索关键词"],"suggestions":["增加AI芯片、ABF、封装基板等关键词"]}',
                '{"title":"味精公司卡住AI芯片？","desc":"从味之素、ABF绝缘膜、高端FC-BGA封装基板讲清AI芯片供应链的隐形地基。","tags":"AI芯片,味之素,ABF,封装基板","cover_text":"AI芯片底座","cover_prompt":"封面","comment_question":"你觉得封装材料会成为AI瓶颈吗","title_options":["味精公司卡住AI芯片？","AI芯片底座是谁","GPU底下这层材料"],"cover_options":["AI芯片底座"]}',
                '{"passed":true,"score":9,"reasons":[],"suggestions":[]}',
            ],
        ) as mock_llm:
            assets = deep_series.generate_publish_assets(series, episode, result)

        self.assertEqual(assets["title"], "味精公司卡住AI芯片？")
        self.assertIn("ABF", assets["desc"])
        self.assertIn("封装基板", assets["desc"])
        self.assertIn("关键词", assets["desc"])
        self.assertFalse(assets["publish_review"]["blocked"])
        self.assertEqual(assets["publish_review"]["attempts"], 2)
        review_prompt = mock_llm.call_args_list[1].args[0]
        self.assertIn("标题党式点击欲", review_prompt)
        self.assertIn("搜索关键词", review_prompt)

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
            "女：旧开头，凭什么卡住供应链？\n男：这和普通人有什么关系？\n女：核心答案是材料。\n男：所以不是只有芯片？",
            "女：真正卡住AI芯片的，可能不是晶圆，而是藏在封装基板里的材料。\n男：材料怎么会比芯片本身还容易被低估？\n女：关键是连接和稳定性，算力最后也要靠基板把信号送出去。\n男：所以开头要先看芯片下面那一层？",
            '{"passed":true,"score":8,"reasons":[],"suggestions":[]}',
            "脚本备注",
            "纪录片包",
        ],
    )
    @patch("deep_series.collect_research_sources", return_value=[
        {"title": "Source 1", "link": "https://example.com/1", "content": "AI search source"},
        {"title": "Source 2", "link": "https://example.com/2", "content": "AI search source"},
        {"title": "Source 3", "link": "https://example.com/3", "content": "AI search source"},
    ])
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

    def test_run_episode_pipeline_retries_three_times_then_uses_safe_fallback(self):
        episode = {"title": "AI 为什么会替代搜索？", "question": "AI 为什么会替代搜索？"}
        series = {"title": "AI 未来三年系列", "description": "测试说明"}
        weak_sources = [[{"title": "弱来源", "link": "https://example.com/1", "content": "资料不足"}]] * 3

        with patch("deep_series.collect_research_sources", side_effect=weak_sources) as mock_collect, \
                patch("deep_series.generate_research_report", side_effect=["研究1", "研究2", "研究3"]), \
                patch("deep_series.audit_research", return_value="事实支撑不够具体，缺少官方来源。"), \
                patch("deep_series.generate_dialogue_script_with_duration_guard", return_value=("女：保守写稿。", {"blocked": False, "estimated_seconds": 30.0, "attempts": 1})) as mock_script, \
                patch("deep_series.generate_script_notes", return_value="脚本备注"), \
                patch("deep_series.generate_documentary_package", return_value="纪录片包"):
            result = deep_series.run_episode_pipeline(series, episode, base_dir=self.tmpdir)

        self.assertEqual(mock_collect.call_count, 3)
        self.assertFalse(result["review_blocked"])
        self.assertTrue(result["review_ready"])
        self.assertTrue(result["fallback_used"])
        self.assertIn("保守写稿", result["quality_block_reason"])
        self.assertTrue(os.path.exists(result["quality_report_path"]))
        self.assertTrue(os.path.exists(result["script_path"]))
        safe_audit = mock_script.call_args.args[3]
        self.assertIn("不要写入缺少来源支撑的具体数据", safe_audit)

    def test_run_episode_pipeline_keeps_script_duration_warning_non_blocking(self):
        episode = {"title": "AI 为什么会替代搜索？", "question": "AI 为什么会替代搜索？"}
        series = {"title": "AI 未来三年系列", "description": "测试说明"}

        with patch("deep_series.collect_research_sources", return_value=[
                {"title": "来源一", "link": "https://example.com/1", "content": "资料一"},
                {"title": "来源二", "link": "https://example.com/2", "content": "资料二"},
                {"title": "来源三", "link": "https://example.com/3", "content": "资料三"},
        ]), \
                patch("deep_series.generate_research_report", return_value="研究报告"), \
                patch("deep_series.audit_research", return_value="事实支撑不足，需要保守表达。"), \
                patch("deep_series.generate_dialogue_script_with_duration_guard", return_value=("女：偏长脚本。", {"blocked": True, "estimated_seconds": 185.0, "reasons": ["脚本预计 185 秒，超过 180 秒"], "attempts": 3})), \
                patch("deep_series.generate_script_notes", return_value="脚本备注"), \
                patch("deep_series.generate_documentary_package", return_value="纪录片包"):
            result = deep_series.run_episode_pipeline(series, episode, base_dir=self.tmpdir)

        self.assertFalse(result["review_blocked"])
        self.assertTrue(result["review_ready"])
        self.assertIn("脚本预计", result["quality_block_reason"])
        self.assertTrue(os.path.exists(result["script_notes_path"]))

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

    def test_generate_episode_video_continues_when_review_blocked_but_script_exists(self):
        script_path = os.path.join(self.tmpdir, "script.md")
        with open(script_path, "w", encoding="utf-8") as f:
            f.write("女：已有脚本。\n男：继续生成。")
        config = {
            "series": [
                {
                    "title": "Series A",
                    "description": "Demo",
                    "episodes": [{
                        "title": "Episode A",
                        "question": "Question A",
                        "script_path": script_path,
                        "review_blocked": True,
                        "quality_block_reason": "脚本预计 185 秒，超过 180 秒",
                    }],
                }
            ]
        }
        deep_series.save_config(config, self.config_path)

        with patch("deep_series.CONFIG_PATH", self.config_path), \
                patch("deep_series.generate_video_from_script", return_value={"script_path": script_path, "video_path": os.path.join(self.tmpdir, "demo.mp4")}), \
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
            result = deep_series.generate_episode_video_by_titles("Series A", "Episode A")

        self.assertTrue(result["video_path"].endswith("demo.mp4"))
        latest = deep_series.load_config(self.config_path)
        episode = latest["series"][0]["episodes"][0]
        self.assertFalse(episode["review_blocked"])
        self.assertTrue(episode["generated"])

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
        self.assertEqual(episode["quality_block_reason"], "")
        self.assertFalse(episode["fallback_used"])

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

    def test_classify_deep_visual_card_adds_information_labels(self):
        self.assertEqual(deep_series.classify_deep_visual_card("它在供应链里处于设备材料环节。"), "产业链位置")
        self.assertEqual(deep_series.classify_deep_visual_card("2026 年收入可能继续增长。"), "关键数字")
        self.assertEqual(deep_series.classify_deep_visual_card("最大风险是客户认证周期太长。"), "风险判断")
        self.assertEqual(deep_series.classify_deep_visual_card("但反方观点是它未必能直接受益。"), "正反观点")

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

    def test_split_visual_segment_avoids_tiny_phrase_cards_and_balances_duration(self):
        long_text = (
            "结论是，未来三年更可能出现“个人 AI 操作层”，不是新的 Windows、iOS 或 Android。"
            "它重要，是因为它可能把搜索框、App 图标、邮箱、日历和浏览器上面的入口权重新洗牌。"
            "后面只看三件事：它长什么样，谁最可能做出来，以及为什么它现在还不能完全放手执行。"
        )

        slide_plan = deep_series._split_visual_segment(
            {"speaker": "female", "duration": 19.16, "text": long_text}
        )
        texts = [item["text"] for item in slide_plan]

        # 真实生成样本里曾出现“结论是，”独占一张卡片的问题，这里固定住拆卡下限。
        self.assertNotIn("结论是，", texts)
        self.assertTrue(all(len(text) >= 12 for text in texts), texts)
        self.assertAlmostEqual(sum(item["duration"] for item in slide_plan), 19.16)

        # 拆成多张卡时，长文本卡要拿到更长展示时间，避免画面和口播节奏明显错位。
        lengths = [len(text) for text in texts]
        durations = [item["duration"] for item in slide_plan]
        shortest_index = min(range(len(texts)), key=lambda index: lengths[index])
        longest_index = max(range(len(texts)), key=lambda index: lengths[index])
        self.assertGreater(durations[longest_index], durations[shortest_index])

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
