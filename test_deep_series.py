import asyncio
import json
import os
import re
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

    def test_build_search_keywords_adds_generic_robot_research_terms_without_abf_noise(self):
        keywords = deep_series.build_search_keywords(
            {"title": "AI时代，机器人先学会打工"},
            {
                "title": "最难的不是会聊天，是会拿起一个杯子",
                "question": "为什么让机器人稳定抓取、行走和操作物体，比让 AI 写一段话更难？",
            },
        )

        joined = "\n".join(keywords)
        self.assertIn("机器人 抓取 操作 物体 难点", joined)
        self.assertIn("robot grasping manipulation real world reliability", joined)
        self.assertNotIn("杯子 ABF", joined)

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
        self.assertIn("[S1]", mock_llm.call_args.args[1])
        self.assertIn("来源编号", prompt)
        self.assertIn("[S1]", prompt)

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

    def test_call_llm_retries_stream_remote_protocol_error(self):
        fake_llm = types.SimpleNamespace(
            stream=MagicMock(side_effect=[
                RuntimeError("peer closed connection without sending complete message body (incomplete chunked read)"),
                [types.SimpleNamespace(content="重试后成功")],
            ]),
            invoke=MagicMock(),
        )
        fake_factory = MagicMock()
        fake_factory.return_value.getDeepseek.return_value = fake_llm

        # 流式 Responses API 偶发半包中断时，深度系列应重试同一次 LLM 任务，而不是直接让整集生成失败。
        with patch.dict(sys.modules, {"util.llm": types.SimpleNamespace(LLmFactory=fake_factory)}), \
                patch("deep_series.time.sleep") as mock_sleep:
            result = deep_series.call_llm("提示词", "资料正文")

        self.assertEqual(result, "重试后成功")
        self.assertEqual(fake_llm.stream.call_count, 2)
        fake_llm.invoke.assert_not_called()
        mock_sleep.assert_called_once()

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
        self.assertIn("120-150秒", prompt)
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
        self.assertIn("单一主线", prompt)
        self.assertIn("上一段", prompt)
        self.assertIn("所以这一段", prompt)
        self.assertIn("不要把中段写成并列清单", prompt)
        self.assertIn("不要连续输出同一个主持人的多行发言", prompt)
        self.assertIn("不要使用“你有没有想过”", prompt)
        self.assertIn("不要使用“想象一下”", prompt)
        self.assertIn("不要使用“今天我们探讨”", prompt)
        self.assertIn("克制互动埋点", prompt)
        self.assertIn("不要写“把绝了打在弹幕上”", prompt)
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
        self.assertTrue(report["duration_agent_optimized"])
        self.assertEqual(report["duration_agent_attempts"], 1)
        self.assertIn("脚本时长优化代理", mock_llm.call_args.args[0])

    def test_dialogue_duration_estimate_blocks_chattts_three_minute_script(self):
        script = "女：" + "这" * 820

        report = deep_series.assess_dialogue_duration(script)

        # ChatTTS 真实语速比原来的字符估算更慢，接近 3 分钟的稿子必须提前拦住压缩。
        self.assertTrue(report["blocked"])
        self.assertGreater(report["estimated_seconds"], deep_series.DEEP_TARGET_MAX_SECONDS)

    def test_dialogue_duration_estimate_blocks_above_two_and_half_minutes(self):
        script = "女：" + "这" * 690

        report = deep_series.assess_dialogue_duration(script)

        # 用户希望最终尽量落在 2 分半，所以脚本阶段要提前压住接近 150 秒的稿子。
        self.assertTrue(report["blocked"])
        self.assertEqual(deep_series.DEEP_TARGET_MAX_SECONDS, 150)
        self.assertGreater(report["estimated_seconds"], deep_series.DEEP_TARGET_MAX_SECONDS)

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
        self.assertIn("观点连贯性", mock_llm.call_args.args[0])
        self.assertIn("观点跳跃", mock_llm.call_args.args[0])
        self.assertIn("资料清单", mock_llm.call_args.args[0])

    def test_review_script_retention_blocks_unparseable_json(self):
        malformed_review = (
            '{"passed":true,"score":8,"reasons":["中段节奏略平均"],'
            '"suggestions":["结尾要回扣“拿杯子”。]}'
        )

        with patch("deep_series.call_llm", return_value=malformed_review):
            review = deep_series.review_script_retention(
                {"title": "AI时代，机器人先学会打工"},
                {"title": "最难的不是会聊天，是会拿起一个杯子"},
                "女：抓杯子之后突然跳到走路。\n男：为什么？",
                "研究报告",
                "审校意见",
            )

        self.assertTrue(review["blocked"])
        self.assertFalse(review["passed"])
        self.assertIn("JSON解析失败", "；".join(review["reasons"]))
        self.assertIn("观点承接", "；".join(review["suggestions"]))
        self.assertEqual(review["raw"], malformed_review)

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

    def test_generate_video_from_script_allows_audio_over_three_minutes(self):
        script_path = os.path.join(self.tmpdir, "script.md")
        with open(script_path, "w", encoding="utf-8") as f:
            f.write("女：这是一段已经生成好的长脚本。")

        def write_oversized_audio(_script_path, output_path):
            with open(output_path, "wb") as f:
                f.write(b"audio")
            with open(output_path + ".timing.json", "w", encoding="utf-8") as f:
                json.dump({"total_duration": 181.0, "segments": []}, f)
            return output_path

        with patch("deep_series.convert_dialogue_to_audio", side_effect=write_oversized_audio), \
                patch("deep_series.build_visual_design", return_value={}), \
                patch("deep_series.create_deep_slide_images", return_value=["slide.png"]) as mock_slides, \
                patch("deep_series.step_video", return_value=os.path.join(self.tmpdir, "demo.mp4")) as mock_video:
            result = deep_series.generate_video_from_script(
                {"title": "测试系列"},
                {"title": "测试主题"},
                {"script_path": script_path},
            )

        # 超过 3 分钟只作为质量提醒，视频阶段不能拦截，否则会让用户无法生成完整成片。
        self.assertEqual(result["actual_seconds"], 181.0)
        self.assertIn("超过", result["quality_block_reason"])
        self.assertTrue(result["video_path"].endswith("demo.mp4"))
        mock_slides.assert_called_once()
        mock_video.assert_called_once()

    def test_generate_video_from_audio_rebuilds_stale_system_visual_design(self):
        # 老版本已经落盘的错误视觉设计不能在重生成视频时继续复用，否则当前这期仍会画成味精/HBM/封装。
        script_path = os.path.join(self.tmpdir, "script.md")
        research_path = os.path.join(self.tmpdir, "research.md")
        audio_path = os.path.join(self.tmpdir, "dialogue.mp3")
        visual_design_path = os.path.join(self.tmpdir, "visual_design.json")
        with open(script_path, "w", encoding="utf-8") as f:
            f.write("女：AI 基建最怕系统跑不稳，要看运维、调度、供电冷却、网络故障和安全治理。")
        with open(research_path, "w", encoding="utf-8") as f:
            f.write("这意味着早期关注 GPU、HBM 和先进封装，但现在更难的是工程团队把复杂系统跑稳。")
        with open(audio_path, "wb") as f:
            f.write(b"audio")
        with open(audio_path + ".timing.json", "w", encoding="utf-8") as f:
            json.dump({"total_duration": 12.0, "segments": []}, f)
        with open(visual_design_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "main_elements": ["味精颗粒", "高带宽内存", "GPU芯片", "封装基板"],
                    "scene_cards": [{"keyword": "味精颗粒", "asset": "hero", "label": "味精颗粒"}],
                    "asset_paths": {},
                },
                f,
                ensure_ascii=False,
            )
        captured_designs = []

        def fake_create_slides(_series, _episode, _script_path, _audio_path, visual_design=None):
            captured_designs.append(visual_design)
            return ["slide.png"]

        result = {
            "script_path": script_path,
            "research_path": research_path,
            "audio_path": audio_path,
            "visual_design_path": visual_design_path,
        }
        with patch("deep_series.create_deep_slide_images", side_effect=fake_create_slides), \
                patch("deep_series.step_video", return_value=os.path.join(self.tmpdir, "demo.mp4")):
            deep_series.generate_video_from_audio(
                {"title": "AI时代最缺的不是芯片"},
                {
                    "title": "AI 最缺的可能是能把系统跑稳的人",
                    "question": "为什么 AI 基建最后还是缺工程人才？重点探讨数据中心运维、集群调度、供电冷却、网络故障、安全治理和跨领域工程能力。",
                },
                result,
            )

        self.assertIn("稳定运行", captured_designs[0]["main_elements"])
        self.assertNotIn("味精颗粒", captured_designs[0]["main_elements"])

    def test_generate_tts_from_script_keeps_oversized_audio_for_review(self):
        script_path = os.path.join(self.tmpdir, "script.md")
        with open(script_path, "w", encoding="utf-8") as f:
            f.write("女：这是一段完整脚本，哪怕超时也要先完整合成。")

        def write_oversized_audio(_script_path, output_path):
            with open(output_path, "wb") as f:
                f.write(b"audio")
            with open(output_path + ".timing.json", "w", encoding="utf-8") as f:
                json.dump({"total_duration": 181.0, "segments": []}, f)
            return output_path

        with patch("deep_series.convert_dialogue_to_audio", side_effect=write_oversized_audio):
            result = deep_series.generate_tts_from_script(
                {"title": "测试系列"},
                {"title": "测试主题"},
                {"script_path": script_path},
            )

        # TTS 阶段不截断、不丢弃超长音频，只把超时原因写回状态，留给用户缩稿后重合成。
        self.assertTrue(result["audio_path"].endswith("dialogue.mp3"))
        self.assertEqual(result["actual_seconds"], 181.0)
        self.assertIn("超过", result["quality_block_reason"])

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

    def test_edge_tts_default_rate_is_neutral(self):
        # Edge 备用路径也保持 1.0 倍语速，避免切换引擎后又恢复加速。
        fake_edge_tts = types.SimpleNamespace(Communicate=FakeCommunicate)
        deep_series.DEEP_TTS_ENGINE = "edge"

        with patch.dict(sys.modules, {"edge_tts": fake_edge_tts}):
            asyncio.run(deep_series._save_tts("默认语速测试", "edge.mp3", deep_series.MALE_VOICE, role="male"))

        self.assertEqual(deep_series.DEEP_TTS_RATE, "+0%")
        self.assertEqual(FakeCommunicate.calls[0].rate, "+0%")

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
            self.assertNotEqual(image.getpixel((960, 540)), (0, 0, 0))

    def test_create_text_card_redesign_removes_old_bars_and_progress(self):
        image_path = os.path.join(self.tmpdir, "redesigned_slide.png")
        deep_series.create_text_card(
            "味之素：味精公司为什么成了高端芯片底座",
            "深度观点 · AI时代的隐形地基 · 女主持",
            "一家卖味精的公司，怎么成了AI芯片封装里那层绝缘膜的关键？",
            image_path,
            accent="#C79AA8",
            slide_index=1,
            slide_total=58,
        )

        from PIL import Image

        image = Image.open(image_path).convert("RGB")
        # 旧版左侧有竖向色带、右侧竖面板和底部进度条；新版页面不允许这些结构继续出现。
        self.assertNotEqual(image.getpixel((142, 500)), (199, 154, 168))
        self.assertNotEqual(image.getpixel((190, 876)), (199, 154, 168))
        self.assertNotEqual(image.getpixel((1700, 390)), (199, 154, 168))

    def test_create_text_card_uses_single_outer_content_board(self):
        # 视频卡片保留一层内容底板提供结构，但不能再出现内层描边框和套娃大边距。
        from PIL import Image

        image_path = os.path.join(self.tmpdir, "single_board_slide.png")
        deep_series.create_text_card(
            "标题",
            "观点 · 系列 · 女主持",
            "正文内容",
            image_path,
            accent="#C79AA8",
            current_speaker="female",
        )

        image = Image.open(image_path).convert("RGB")
        self.assertEqual(image.getpixel((140, 90)), (255, 255, 255))
        self.assertNotEqual(image.getpixel((104, 150)), (229, 229, 234))

    def test_create_text_card_uses_background_and_one_foreground_svg(self):
        image_path = os.path.join(self.tmpdir, "multi_svg_slide.png")
        asset_paths = {
            "hero": os.path.join(self.tmpdir, "hero.svg"),
            "bridge": os.path.join(self.tmpdir, "bridge.svg"),
            "background": os.path.join(self.tmpdir, "background.svg"),
        }
        visual_design = {
            "main_elements": ["味精颗粒", "GPU封装基板", "ABF薄膜"],
            "asset_paths": asset_paths,
        }
        scene = {"asset": "bridge", "label": "ABF薄膜"}

        with patch("deep_series.paste_svg_asset", return_value=True) as mock_paste:
            deep_series.create_text_card(
                "味之素：味精公司为什么成了高端芯片底座",
                "深度观点 · AI时代的隐形地基 · 女主持",
                "ABF薄膜藏在GPU封装基板里。",
                image_path,
                visual_design=visual_design,
                scene=scene,
            )

        used_paths = [call.args[1] for call in mock_paste.call_args_list]
        # 视频页只贴命中的前景 SVG，避免多张主图互相遮挡；背景仍按独立纹理铺满。
        self.assertNotIn(asset_paths["hero"], used_paths)
        self.assertIn(asset_paths["bridge"], used_paths)
        self.assertIn(asset_paths["background"], used_paths)
        foreground_call = [call for call in mock_paste.call_args_list if call.args[1] == asset_paths["bridge"]][0]
        # 视频里 SVG 只当图形纹理使用，文字统一交给 iOS 卡片层，避免模型文字错位。
        self.assertFalse(foreground_call.kwargs["include_text"])

    def test_create_text_card_uses_background_svg_as_full_canvas_background(self):
        # 深度系列每张视频卡都应该先铺满本期 background.svg，再叠加 iOS 内容层。
        from PIL import Image

        background_path = os.path.join(self.tmpdir, "background.svg")
        with open(background_path, "w", encoding="utf-8") as f:
            f.write('<svg width="1920" height="1080"><rect x="0" y="0" width="1920" height="1080" fill="#FF9500"/></svg>')
        image_path = os.path.join(self.tmpdir, "card.png")

        deep_series.create_text_card(
            "标题",
            "系列 · 女主持",
            "正文内容",
            image_path,
            visual_design={"asset_paths": {"background": background_path}, "main_elements": ["背景测试"]},
            scene={"asset": "background", "label": "背景测试"},
        )

        image = Image.open(image_path).convert("RGB")
        self.assertEqual(image.getpixel((8, 8)), (255, 149, 0))

    def test_create_text_card_highlights_current_female_host_avatar(self):
        # 当前说话人是女主持时，只高亮小头像外圈，避免大面积 UI 面板显得像后台界面。
        from PIL import Image

        image_path = os.path.join(self.tmpdir, "female_host_slide.png")
        deep_series.create_text_card(
            "标题",
            "观点 · 系列 · 女主持",
            "这是一段用于检查主持人高亮的正文。",
            image_path,
            accent="#C79AA8",
            current_speaker="female",
        )

        image = Image.open(image_path).convert("RGB")
        self.assertEqual(image.getpixel((988, 918)), (199, 154, 168))
        self.assertNotEqual(image.getpixel((1160, 920)), (199, 154, 168))
        self.assertNotEqual(image.getpixel((1350, 918)), (199, 154, 168))

    def test_create_text_card_removes_template_brand_text_area(self):
        # 视频图卡不再显示 OpenNewsBrief 这类模板品牌文字，顶部旧胶囊区域应该保持干净白底。
        from PIL import Image

        image_path = os.path.join(self.tmpdir, "no_brand_slide.png")
        deep_series.create_text_card(
            "标题",
            "观点 · 系列 · 女主持",
            "正文内容",
            image_path,
            accent="#C79AA8",
            current_speaker="female",
        )

        image = Image.open(image_path).convert("RGB")
        self.assertNotEqual(image.getpixel((170, 160)), (199, 154, 168))

    def test_create_text_card_keeps_host_avatar_outside_svg_stage(self):
        # 主持人形象不能压到左侧 SVG 舞台区域，避免和本期视觉元素重叠。
        from PIL import Image

        image_path = os.path.join(self.tmpdir, "host_outside_stage_slide.png")
        deep_series.create_text_card(
            "标题",
            "观点 · 系列 · 女主持",
            "正文内容",
            image_path,
            accent="#C79AA8",
            current_speaker="female",
        )

        image = Image.open(image_path).convert("RGB")
        self.assertNotEqual(image.getpixel((210, 908)), (199, 154, 168))

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
        # B站标题直接使用自然标题，系列信息只放进简介和标签，避免信息流里像自动打卡。
        self.assertIn("原始主题标题", prompt)
        self.assertIn("不需要完全照抄", prompt)
        self.assertIn("更吸引眼球", prompt)
        self.assertIn("不要强制套用系列名前缀", prompt)
        self.assertIn("实体词或反差点尽量前置", prompt)
        # 标题生成要主动偏向轻标题党，避免自动生成成平铺直叙的说明句。
        self.assertIn("轻标题党", prompt)
        self.assertIn("反差", prompt)
        self.assertIn("悬念", prompt)
        self.assertIn("疑问句", prompt)
        self.assertIn("不能编造事实", prompt)
        self.assertIn("封面只保留一个核心反差词", prompt)
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
        # 审校阶段不能只指出标题弱还放行，否则后续仍会生成点击欲不足的平标题。
        self.assertIn("点击欲不足", review_prompt)
        self.assertIn("必须判为不通过", review_prompt)
        self.assertIn("搜索关键词", review_prompt)

    @patch("deep_series.create_deep_cover_options", return_value=["cover1.png", "cover2.png", "cover3.png"])
    @patch("deep_series.create_deep_cover_image", return_value="cover.png")
    def test_generate_publish_assets_uses_default_when_llm_connection_fails(self, _cover, _cover_options):
        script_path = os.path.join(self.tmpdir, "script.md")
        with open(script_path, "w", encoding="utf-8") as f:
            f.write("女：LLM 不可用时，也不能让已经合成的视频丢失状态。")
        result = {"script_path": script_path, "video_path": os.path.join(self.tmpdir, "demo.mp4")}
        series = {"title": "AI时代最缺的不是芯片"}
        episode = {"title": "AI 最缺的可能是能把系统跑稳的人"}

        with patch("deep_series.call_llm", side_effect=RuntimeError("Connection error.")):
            assets = deep_series.generate_publish_assets(series, episode, result)

        # 发布素材 LLM 失败时走本地默认文案，保证视频合成后的状态仍能写回配置。
        self.assertEqual(assets["title"], deep_series.normalize_publish_title(episode["title"], episode["title"], series["title"]))
        self.assertIn("关键词", assets["desc"])
        self.assertFalse(assets["publish_review"]["blocked"])
        self.assertTrue(os.path.exists(os.path.join(self.tmpdir, "publish_assets.json")))

    def test_sanitize_svg_removes_scripts_external_links_and_keeps_shapes(self):
        raw_svg = (
            '<svg width="100" height="100" onload="bad()">'
            '<script>alert(1)</script>'
            '<foreignObject><p>bad</p></foreignObject>'
            '<image href="https://example.com/a.png" />'
            '<rect x="10" y="10" width="80" height="30" fill="#007AFF" onclick="bad()" />'
            '<text x="12" y="70" fill="#1D1D1F">ABF</text>'
            '</svg>'
        )

        cleaned = deep_series.sanitize_svg(raw_svg)

        self.assertIn("<svg", cleaned)
        self.assertIn("<rect", cleaned)
        self.assertIn(">ABF<", cleaned)
        self.assertNotIn("script", cleaned.lower())
        self.assertNotIn("foreignObject", cleaned)
        self.assertNotIn("https://", cleaned)
        self.assertNotIn("onclick", cleaned)
        self.assertNotIn("onload", cleaned)

    def test_generate_visual_svg_asset_uses_topic_fallback_when_model_svg_is_invalid(self):
        # SVG 模型输出不可解析时，兜底图也要保留主题语义，不能退化成“视觉元素”通用图。
        design = {
            "palette": ["#F5F5F7", "#1D1D1F", "#007AFF", "#FF9500", "#8E8E93"],
            "main_elements": ["AI服务器", "企业采购", "竞争风险"],
        }

        with patch("deep_series.call_llm", return_value="不是有效 SVG"):
            path = deep_series.generate_visual_svg_asset(
                "risk_competition",
                "市场竞争风险图谱：NVIDIA依赖、大客户议价、竞品压力。",
                design,
                self.tmpdir,
                use_llm=True,
            )

        with open(path, "r", encoding="utf-8") as f:
            svg = f.read()
        self.assertIn("竞争风险图谱", svg)
        self.assertIn("客户议价", svg)
        self.assertNotIn("视觉元素", svg)

    def test_build_visual_design_only_calls_llm_for_design_not_svg_assets(self):
        # 视频生成阶段只让大模型做一次视觉规划，SVG 文件走本地简洁兜底，避免每张图反复调模型。
        script_path = os.path.join(self.tmpdir, "script.md")
        with open(script_path, "w", encoding="utf-8") as f:
            f.write("女：AI 基础设施不是只买 GPU，还要看整机交付。")
        result = {"script_path": script_path}
        raw_design = {
            "cover_title": "AI 基建",
            "subtitle": "整机交付",
            "style": "iOS 简洁科技风",
            "composition": "system_diagram",
            "palette": ["#F5F5F7", "#1D1D1F", "#007AFF", "#FF9500", "#8E8E93"],
            "main_elements": ["GPU", "服务器", "存储网络"],
            "svg_prompts": {
                "hero": "生成 GPU 与服务器的简洁科技 SVG。",
                "bridge": "生成整机交付链路 SVG。",
                "background": "生成浅色科技网格 SVG。",
            },
            "scene_cards": [{"keyword": "GPU", "asset": "hero", "label": "GPU 系统"}],
        }

        with patch("deep_series.call_llm", return_value=json.dumps(raw_design, ensure_ascii=False)) as mock_llm:
            design = deep_series.build_visual_design(
                {"title": "AI时代的隐形地基"},
                {"title": "GPU 不只是显卡"},
                result,
                use_llm=True,
            )

        self.assertEqual(mock_llm.call_count, 1)
        self.assertIn("hero", design["asset_paths"])
        self.assertTrue(os.path.exists(design["asset_paths"]["hero"]))

    def test_generate_visual_svg_asset_uses_chinese_topic_fallback_without_llm(self):
        # 离线或禁用 LLM 时，英文资产 key 也要生成可读中文主题图，而不是把 data_center_cooling 画到页面上。
        design = {
            "palette": ["#F5F5F7", "#1D1D1F", "#007AFF", "#FF9500", "#8E8E93"],
            "main_elements": ["数据中心", "冷却回路", "PUE"],
        }

        path = deep_series.generate_visual_svg_asset(
            "data_center_cooling",
            "AI 数据中心冷却系统：服务器机柜、冷却回路、PUE 仪表。",
            design,
            self.tmpdir,
            use_llm=False,
        )

        with open(path, "r", encoding="utf-8") as f:
            svg = f.read()
        self.assertIn("数据中心冷却", svg)
        self.assertIn("冷却回路", svg)
        self.assertNotIn("data_center_cooling", svg)
        self.assertNotIn("视觉元素", svg)

    def test_generate_visual_svg_asset_fallback_varies_non_text_shapes(self):
        # 视频合成会隐藏 SVG 内文字，所以兜底图的图形结构也必须随场景变化。
        design = {
            "palette": ["#F5F5F7", "#1D1D1F", "#007AFF", "#FF9500", "#8E8E93"],
            "main_elements": ["AI服务器", "企业采购", "数据管道"],
        }

        first_path = deep_series.generate_visual_svg_asset(
            "gpu_vs_system",
            "GPU 不是完整系统：服务器、存储、网络一起交付。",
            design,
            self.tmpdir,
            use_llm=False,
        )
        second_path = deep_series.generate_visual_svg_asset(
            "cio_procurement_dashboard",
            "企业采购面板：预算、交付、运维。",
            design,
            self.tmpdir,
            use_llm=False,
        )

        with open(first_path, "r", encoding="utf-8") as f:
            first_svg = f.read()
        with open(second_path, "r", encoding="utf-8") as f:
            second_svg = f.read()
        first_shapes = re.sub(r"<text[\s\S]*?</text>", "", first_svg)
        second_shapes = re.sub(r"<text[\s\S]*?</text>", "", second_svg)
        self.assertNotEqual(first_shapes, second_shapes)

    def test_generate_visual_svg_asset_robot_scenes_use_different_shapes(self):
        # 最近机器人主题的场景资产不能都退回三方块流程图；视频会隐藏 SVG 文字，所以图形本身必须有场景差异。
        design = {
            "palette": ["#F5F5F7", "#1D1D1F", "#007AFF", "#FF9500", "#8E8E93"],
            "main_elements": ["仓库搬运", "外卖配送", "商用清洁", "酒店送物"],
        }

        warehouse_path = deep_series.generate_visual_svg_asset(
            "isometric warehouse aisle with",
            "生成仓库搬运的短视频场景 SVG，突出关键词：warehouse_robot。",
            design,
            self.tmpdir,
            use_llm=False,
        )
        delivery_path = deep_series.generate_visual_svg_asset(
            "sidewalk or campus delivery ro",
            "生成外卖配送的短视频场景 SVG，突出关键词：delivery_robot。",
            design,
            self.tmpdir,
            use_llm=False,
        )
        cleaning_path = deep_series.generate_visual_svg_asset(
            "autonomous floor scrubber clea",
            "生成商用清洁的短视频场景 SVG，突出关键词：commercial_cleaning。",
            design,
            self.tmpdir,
            use_llm=False,
        )

        with open(warehouse_path, "r", encoding="utf-8") as f:
            warehouse_svg = f.read()
        with open(delivery_path, "r", encoding="utf-8") as f:
            delivery_svg = f.read()
        with open(cleaning_path, "r", encoding="utf-8") as f:
            cleaning_svg = f.read()

        warehouse_shapes = re.sub(r"<text[\s\S]*?</text>", "", warehouse_svg)
        delivery_shapes = re.sub(r"<text[\s\S]*?</text>", "", delivery_svg)
        cleaning_shapes = re.sub(r"<text[\s\S]*?</text>", "", cleaning_svg)
        self.assertIn("仓库搬运", warehouse_svg)
        self.assertIn("外卖配送", delivery_svg)
        self.assertIn("商用清洁", cleaning_svg)
        self.assertNotEqual(warehouse_shapes, delivery_shapes)
        self.assertNotEqual(warehouse_shapes, cleaning_shapes)
        self.assertNotEqual(delivery_shapes, cleaning_shapes)

    def test_generate_visual_svg_asset_fallback_keeps_detail_text_contrast(self):
        # 详情文字不能再用白字压在白色面板上，Dell 场景 SVG 会因此看不清。
        design = {
            "palette": ["#F5F5F7", "#1D1D1F", "#007AFF", "#FF9500", "#8E8E93"],
            "main_elements": ["企业采购", "交付", "运维"],
        }

        path = deep_series.generate_visual_svg_asset(
            "cio_procurement_dashboard",
            "企业采购面板：预算、交付、运维。",
            design,
            self.tmpdir,
            use_llm=False,
        )

        with open(path, "r", encoding="utf-8") as f:
            svg = f.read()
        self.assertNotIn('y="158" fill="#FFFFFF"', svg)
        self.assertIn('fill="#1D1D1F"', svg)
        self.assertIn('预算', svg)

    def test_generate_visual_svg_asset_uses_storage_pipeline_label(self):
        # Dell 的存储数据管道资产要显示产业词，不能把 prompt 里的“生成……”当成标题。
        design = {
            "palette": ["#F5F5F7", "#1D1D1F", "#007AFF", "#FF9500", "#8E8E93"],
            "main_elements": ["存储网络", "数据供给", "GPU等待"],
        }

        path = deep_series.generate_visual_svg_asset(
            "storage_data_pipeline",
            "生成数据供不上，GPU 等待的短视频场景 SVG，突出关键词：存储瓶颈。",
            design,
            self.tmpdir,
            use_llm=False,
        )

        with open(path, "r", encoding="utf-8") as f:
            svg = f.read()
        self.assertIn("数据供给瓶颈", svg)
        self.assertNotIn("生成数据供不上", svg)

    def test_generate_visual_svg_asset_cleans_system_ops_prompt_labels(self):
        # 系统跑稳主题的 SVG 要画运维状态图，不能把生成任务描述残片直接显示到图里。
        design = {
            "palette": ["#F5F5F7", "#1D1D1F", "#007AFF", "#FF9500", "#8E8E93"],
            "main_elements": ["稳定运行", "集群调度", "供电冷却", "网络排障", "安全治理"],
        }

        path = deep_series.generate_visual_svg_asset(
            "hero",
            "生成稳定运行、集群调度、供电冷却的简洁科技SVG，适合短视频封面。",
            design,
            self.tmpdir,
            use_llm=False,
        )

        with open(path, "r", encoding="utf-8") as f:
            svg = f.read()
        self.assertIn(">集群调度<", svg)
        self.assertIn(">供电冷却<", svg)
        self.assertIn(">网络排障<", svg)
        self.assertIn('points="88,170 136,146 184,158 232,124 292,142 340,112"', svg)
        self.assertNotIn('x="34" y="34" width="352" height="54"', svg)
        self.assertNotIn('y="60"', svg)
        self.assertNotIn(">稳定运行<", svg)
        self.assertNotIn("生成稳定运行", svg)
        self.assertNotIn("供电冷却的简洁", svg)
        self.assertNotIn("适合短视频封面", svg)

    def test_generate_visual_svg_asset_server_rack_prefers_rack_shape(self):
        # 服务器整机场景即使包含“交付”，也应该优先画机柜堆栈，而不是采购仪表盘。
        design = {
            "palette": ["#F5F5F7", "#1D1D1F", "#007AFF", "#FF9500", "#8E8E93"],
            "main_elements": ["AI服务器整机", "机柜", "交付"],
        }

        path = deep_series.generate_visual_svg_asset(
            "server_rack_glow",
            "生成 AI 服务器整机的短视频场景 SVG，突出关键词：PowerEdge 服务器。",
            design,
            self.tmpdir,
            use_llm=False,
        )

        with open(path, "r", encoding="utf-8") as f:
            svg = f.read()
        self.assertIn('x="88" y="104"', svg)
        self.assertNotIn('x="44" y="112"', svg)

    def test_build_visual_design_generates_design_json_and_svg_assets(self):
        series = {"title": "AI时代的隐形地基"}
        episode = {
            "title": "味之素：味精公司为什么成了高端芯片底座",
            "question": "味之素为什么能成为 AI 芯片供应链里的隐形公司？重点探讨 ABF 绝缘材料如何支撑 GPU 封装基板。",
        }
        result = {
            "script_path": os.path.join(self.tmpdir, "script.md"),
            "research_path": os.path.join(self.tmpdir, "research.md"),
        }
        with open(result["script_path"], "w", encoding="utf-8") as f:
            f.write("女：一家卖味精的公司，怎么成了AI芯片封装里那层绝缘膜的关键？")
        with open(result["research_path"], "w", encoding="utf-8") as f:
            f.write("ABF 绝缘薄膜用于高端 FC-BGA 封装基板。")

        design_json = json.dumps(
            {
                "cover_title": "味精厂卡进GPU底座",
                "style": "科技财经、强反差、iOS干净排版",
                "composition": "center_bridge",
                "palette": ["#F5F5F7", "#1D1D1F", "#007AFF", "#FF9500"],
                "main_elements": ["味精颗粒", "GPU封装基板", "ABF薄膜"],
                "svg_prompts": {
                    "hero": "生成味精颗粒和GPU封装基板对照的简洁SVG",
                    "bridge": "生成一层发光ABF薄膜SVG",
                    "background": "生成浅色科技网格SVG",
                },
                "scene_cards": [
                    {"keyword": "ABF", "asset": "bridge", "label": "ABF薄膜"},
                    {"keyword": "GPU", "asset": "hero", "label": "GPU底座"},
                ],
            },
            ensure_ascii=False,
        )
        svg = '<svg width="420" height="260"><rect x="20" y="80" width="380" height="80" fill="#007AFF"/><text x="40" y="130">ABF</text></svg>'

        with patch("deep_series.call_llm", side_effect=[design_json, svg, svg, svg]):
            design = deep_series.build_visual_design(series, episode, result)

        self.assertEqual(design["cover_title"], "味精厂卡进GPU底座")
        self.assertEqual(design["composition"], "center_bridge")
        self.assertIn("ABF薄膜", design["main_elements"])
        self.assertTrue(os.path.exists(result["visual_design_path"]))
        self.assertGreaterEqual(len(result["visual_asset_paths"]), 3)
        for path in result["visual_asset_paths"].values():
            self.assertTrue(os.path.exists(path))

    def test_build_visual_design_generates_missing_scene_card_assets(self):
        # 场景卡片如果没有自己的 SVG，真实渲染会反复回退 hero，导致 Dell 这类页面看起来没有变化。
        series = {"title": "AI时代老树开新花"}
        episode = {
            "title": "Dell：卖电脑的公司为什么吃到 AI 服务器红利",
            "question": "Dell 为什么能从 PC 客户基础切到 AI 服务器整机交付？",
        }
        result = {
            "script_path": os.path.join(self.tmpdir, "script.md"),
            "research_path": os.path.join(self.tmpdir, "research.md"),
        }
        with open(result["script_path"], "w", encoding="utf-8") as f:
            f.write("女：企业买 AI 基础设施，不是只买 GPU，而是买整套能交付的系统。")
        with open(result["research_path"], "w", encoding="utf-8") as f:
            f.write("Dell 的 AI 服务器业务覆盖整机、存储、网络、散热和运维。")

        design_json = json.dumps(
            {
                "cover_title": "Dell吃到AI服务器红利",
                "svg_prompts": {
                    "hero": "生成 Dell AI 服务器主视觉",
                    "bridge": "生成整机交付桥接图",
                    "background": "生成数据中心背景",
                },
                "scene_cards": [
                    {"keyword": "AI 基础设施", "asset": "ai_infrastructure_stack", "label": "AI 基础设施"},
                ],
            },
            ensure_ascii=False,
        )
        svg = '<svg width="420" height="260"><rect x="20" y="80" width="380" height="80" fill="#007AFF"/><text x="40" y="130">AI</text></svg>'

        with patch("deep_series.call_llm", side_effect=[design_json, svg, svg, svg, svg]):
            design = deep_series.build_visual_design(series, episode, result)

        self.assertIn("ai_infrastructure_stack", design["asset_paths"])
        self.assertTrue(os.path.exists(design["asset_paths"]["ai_infrastructure_stack"]))
        self.assertEqual(result["visual_asset_paths"]["ai_infrastructure_stack"], design["asset_paths"]["ai_infrastructure_stack"])

    def test_ensure_scene_card_svg_assets_refreshes_stale_generic_bridge_svg(self):
        # 旧机器人产物里已经落盘的三方块流程图要能被刷新，否则重渲染仍然复用错误 SVG。
        asset_dir = os.path.join(self.tmpdir, "visual_assets")
        os.makedirs(asset_dir, exist_ok=True)
        stale_path = os.path.join(asset_dir, "isometric_warehouse_aisle_with.svg")
        with open(stale_path, "w", encoding="utf-8") as f:
            f.write(
                '<svg width="420" height="260" viewBox="0 0 420 260" xmlns="http://www.w3.org/2000/svg">'
                '<rect x="46" y="116" width="96" height="72" rx="18" fill="#007AFF"/>'
                '<rect x="162" y="116" width="96" height="72" rx="18" fill="#FF9500"/>'
                '<rect x="278" y="116" width="96" height="72" rx="18" fill="#FFFFFF" stroke="#007AFF" stroke-width="5"/>'
                '<polyline points="64,214 142,202 210,214 286,196 356,210" fill="transparent" stroke="#007AFF" stroke-width="5"/>'
                '</svg>'
            )
        design = {
            "palette": ["#F5F5F7", "#1D1D1F", "#007AFF", "#FF9500", "#8E8E93"],
            "main_elements": ["仓库搬运", "外卖配送", "商用清洁"],
            "asset_paths": {"isometric warehouse aisle with": stale_path},
            "scene_cards": [
                {
                    "keyword": "warehouse_robot",
                    "asset": "isometric warehouse aisle with",
                    "label": "仓库搬运",
                }
            ],
            "svg_prompts": {
                "isometric warehouse aisle with": "生成仓库搬运的短视频场景 SVG，突出关键词：warehouse_robot。"
            },
        }

        updated = deep_series.ensure_scene_card_svg_assets(design, self.tmpdir, use_llm=False)

        with open(updated["asset_paths"]["isometric warehouse aisle with"], "r", encoding="utf-8") as f:
            refreshed_svg = f.read()
        self.assertIn("仓库搬运", refreshed_svg)
        self.assertIn('x="58" y="92"', refreshed_svg)
        self.assertNotIn('points="64,214 142,202 210,214 286,196 356,210"', refreshed_svg)

    def test_ensure_scene_card_svg_assets_refreshes_stale_top_level_hero_svg(self):
        # 本期大量图卡会回退到 hero；旧 hero.svg 如果还是三方块流程图，重渲染后左侧仍然看不出变化。
        asset_dir = os.path.join(self.tmpdir, "visual_assets")
        os.makedirs(asset_dir, exist_ok=True)
        hero_path = os.path.join(asset_dir, "hero.svg")
        with open(hero_path, "w", encoding="utf-8") as f:
            f.write(
                '<svg width="420" height="260" viewBox="0 0 420 260" xmlns="http://www.w3.org/2000/svg">'
                '<rect x="46" y="116" width="96" height="72" rx="18" fill="#007AFF"/>'
                '<rect x="162" y="116" width="96" height="72" rx="18" fill="#FF9500"/>'
                '<rect x="278" y="116" width="96" height="72" rx="18" fill="#FFFFFF" stroke="#007AFF" stroke-width="5"/>'
                '<polyline points="64,214 142,202 210,214 286,196 356,210" fill="transparent" stroke="#007AFF" stroke-width="5"/>'
                '</svg>'
            )
        design = {
            "palette": ["#F5F5F7", "#1D1D1F", "#007AFF", "#FF9500", "#8E8E93"],
            "main_elements": ["仓库搬运", "外卖配送", "商用清洁"],
            "asset_paths": {"hero": hero_path},
            "scene_cards": [],
            "svg_prompts": {
                "hero": "生成服务机器人在商用运营场景工作的主题 SVG，包含仓库、外卖配送和商用清洁。"
            },
        }

        updated = deep_series.ensure_scene_card_svg_assets(design, self.tmpdir, use_llm=False)

        with open(updated["asset_paths"]["hero"], "r", encoding="utf-8") as f:
            refreshed_svg = f.read()
        self.assertIn("机器人作业", refreshed_svg)
        self.assertIn('points="250,132 292,146 252,162"', refreshed_svg)
        self.assertNotIn('points="64,214 142,202 210,214 286,196 356,210"', refreshed_svg)

    def test_build_visual_design_always_generates_background_svg(self):
        # 即使模型返回很多其它 SVG prompt，也必须为深度视频生成整页背景 background.svg。
        series = {"title": "AI时代老树开新花"}
        episode = {"title": "Dell：卖电脑的公司为什么吃到 AI 服务器红利"}
        result = {
            "script_path": os.path.join(self.tmpdir, "script.md"),
            "research_path": os.path.join(self.tmpdir, "research.md"),
        }
        with open(result["script_path"], "w", encoding="utf-8") as f:
            f.write("女：Dell 的 AI 服务器红利来自企业级基础设施。")
        with open(result["research_path"], "w", encoding="utf-8") as f:
            f.write("AI 服务器需要 GPU、存储、网络、供电和散热。")
        design_json = json.dumps(
            {
                "cover_title": "Dell吃到AI服务器红利",
                "svg_prompts": {
                    "hero": "主视觉",
                    "bridge": "桥接图",
                    "cost_stack": "价值分布",
                    "risk_competition": "竞争风险",
                    "gpu_vs_system": "GPU 与系统",
                    "server_rack_glow": "服务器整机",
                },
            },
            ensure_ascii=False,
        )
        svg = '<svg width="420" height="260"><rect x="0" y="0" width="420" height="260" fill="#007AFF"/></svg>'

        with patch("deep_series.call_llm", side_effect=[design_json, svg, svg, svg, svg, svg, svg, svg, svg]):
            design = deep_series.build_visual_design(series, episode, result)

        self.assertIn("background", design["asset_paths"])
        self.assertTrue(os.path.exists(design["asset_paths"]["background"]))

    def test_ensure_scene_card_svg_assets_backfills_missing_background_svg(self):
        # 旧产物复用视觉设计时，也要补齐 background.svg，避免只有新生成主题才有整页背景。
        design = {
            "palette": ["#F5F5F7", "#1D1D1F", "#007AFF", "#FF9500"],
            "svg_prompts": {"hero": "主视觉"},
            "asset_paths": {},
            "scene_cards": [],
        }

        updated = deep_series.ensure_scene_card_svg_assets(design, self.tmpdir, use_llm=False)

        self.assertIn("background", updated["asset_paths"])
        self.assertTrue(os.path.exists(updated["asset_paths"]["background"]))

    def test_create_deep_cover_image_uses_visual_design_assets(self):
        output_dir = self.tmpdir
        asset_dir = os.path.join(output_dir, "visual_assets")
        os.makedirs(asset_dir, exist_ok=True)
        hero_path = os.path.join(asset_dir, "hero.svg")
        with open(hero_path, "w", encoding="utf-8") as f:
            f.write('<svg width="420" height="260"><rect x="20" y="80" width="380" height="80" fill="#007AFF"/><text x="40" y="130">GPU</text></svg>')
        assets = {
            "title": "味精厂卡位AI芯片",
            "cover_text": "味精厂造芯底",
            "visual_design": {
                "cover_title": "味精厂卡进GPU底座",
                "subtitle": "味之素 · ABF薄膜 · AI芯片封装",
                "style": "科技财经、强反差、iOS干净排版",
                "composition": "center_bridge",
                "palette": ["#F5F5F7", "#1D1D1F", "#007AFF", "#FF9500"],
                "main_elements": ["味精颗粒", "GPU封装基板", "ABF薄膜"],
                "asset_paths": {"hero": hero_path},
            },
        }

        cover_path = deep_series.create_deep_cover_image(
            {"title": "AI时代的隐形地基"},
            {"title": "味之素：味精公司为什么成了高端芯片底座"},
            assets,
            output_dir,
        )

        self.assertTrue(os.path.exists(cover_path))
        self.assertGreater(os.path.getsize(cover_path), 1000)

    def test_create_deep_cover_image_places_large_hook_in_upper_half(self):
        from PIL import Image

        assets = {
            "title": "味精厂卡位AI芯片",
            "cover_text": "味精厂造芯底",
            "visual_design": {
                "cover_title": "味精厂造芯片？",
                "subtitle": "味之素 · ABF薄膜 · AI芯片封装",
                "style": "科技财经、强反差、iOS干净排版",
                "composition": "single_subject",
                "palette": ["#F5F5F7", "#1D1D1F", "#007AFF", "#FF9500"],
                "main_elements": ["味之素", "ABF薄膜", "GPU封装"],
                "asset_paths": {},
            },
        }

        cover_path = deep_series.create_deep_cover_image(
            {"title": "AI时代的隐形地基"},
            {"title": "味之素：味精公司为什么成了高端芯片底座"},
            assets,
            self.tmpdir,
        )

        image = Image.open(cover_path).convert("RGB")
        upper_title_crop = image.crop((112, 190, 600, 245))
        dark_pixels = sum(1 for r, g, b in upper_title_crop.getdata() if r < 80 and g < 80 and b < 80)
        # 信息流封面要让反差钩子尽早进入视野，不能把大标题压到画面中部。
        self.assertGreater(dark_pixels, 40)

    def test_create_deep_cover_image_removes_template_brand_text(self):
        # 封面也不再绘制 OpenNewsBrief 固定字样，避免生成物继续带模板品牌感。
        from PIL import Image

        assets = {
            "title": "味精厂卡位AI芯片",
            "cover_text": "味精厂造芯底",
            "visual_design": {
                "cover_title": "味精厂造芯片？",
                "subtitle": "味之素 · ABF薄膜 · AI芯片封装",
                "style": "科技财经、强反差、iOS干净排版",
                "composition": "single_subject",
                "palette": ["#F5F5F7", "#1D1D1F", "#007AFF", "#FF9500"],
                "main_elements": ["味之素", "ABF薄膜", "GPU封装"],
                "asset_paths": {},
            },
        }

        cover_path = deep_series.create_deep_cover_image(
            {"title": "AI时代的隐形地基"},
            {"title": "味之素：味精公司为什么成了高端芯片底座"},
            assets,
            self.tmpdir,
        )

        image = Image.open(cover_path).convert("RGB")
        brand_crop = image.crop((100, 105, 360, 155))
        blue_pixels = sum(1 for r, g, b in brand_crop.getdata() if b > 160 and r < 120 and g < 170)
        self.assertEqual(blue_pixels, 0)

    def test_create_deep_cover_image_does_not_paste_background_outside_card(self):
        from PIL import Image

        output_dir = self.tmpdir
        asset_dir = os.path.join(output_dir, "visual_assets")
        os.makedirs(asset_dir, exist_ok=True)
        background_path = os.path.join(asset_dir, "background.svg")
        with open(background_path, "w", encoding="utf-8") as f:
            f.write('<svg width="1080" height="1080"><rect x="0" y="0" width="1080" height="1080" fill="#FF9500"/></svg>')
        assets = {
            "title": "味精厂卡位AI芯片",
            "cover_text": "味精厂造芯底",
            "visual_design": {
                "cover_title": "味精厂卡进GPU底座",
                "subtitle": "味之素 · ABF薄膜 · AI芯片封装",
                "style": "科技财经、强反差、iOS干净排版",
                "composition": "center_bridge",
                "palette": ["#F5F5F7", "#1D1D1F", "#007AFF", "#FF9500"],
                "main_elements": ["味精颗粒", "GPU封装基板", "ABF薄膜"],
                "asset_paths": {"background": background_path},
            },
        }

        cover_path = deep_series.create_deep_cover_image(
            {"title": "AI时代的隐形地基"},
            {"title": "味之素：味精公司为什么成了高端芯片底座"},
            assets,
            output_dir,
        )

        image = Image.open(cover_path).convert("RGB")
        self.assertEqual(image.getpixel((8, 8)), (245, 245, 247))

    def test_cover_quality_blocks_overlong_cover_title_before_publish(self):
        assets = {
            "cover_text": "AI深度解析",
            "visual_design": {
                "cover_title": "HOYA：眼镜公司为什么掌握光刻入口",
                "main_elements": ["光学材料", "mask blanks"],
            },
        }

        report = deep_series.assess_cover_quality(assets)

        self.assertTrue(report["blocked"])
        self.assertIn("封面主标题过长", "；".join(report["reasons"]))

    def test_publish_gate_blocks_overtime_sparse_sources_and_cover_risk(self):
        episode = {
            "title": "HOYA：眼镜公司为什么掌握光刻入口",
            "actual_seconds": 181.0,
            "source_count": 2,
            "cover_quality": {"blocked": True, "reasons": ["封面主标题过长，容易裁切"]},
        }

        gate = deep_series.assess_publish_gate({"title": "AI时代的隐形地基"}, episode)

        self.assertTrue(gate["blocked"])
        joined = "；".join(gate["reasons"])
        self.assertIn("超过150秒", joined)
        self.assertIn("有效来源不足", joined)
        self.assertIn("封面", joined)

    def test_publish_gate_allows_short_sourced_episode(self):
        episode = {
            "title": "味之素：味精公司为什么成了高端芯片底座",
            "actual_seconds": 128.0,
            "source_count": 4,
            "cover_quality": {"blocked": False, "reasons": []},
        }

        gate = deep_series.assess_publish_gate({"title": "AI时代的隐形地基"}, episode)

        self.assertFalse(gate["blocked"])
        self.assertEqual(gate["reasons"], [])

    def test_cover_style_label_removes_template_words(self):
        # 封面上的风格胶囊不能暴露 iOS/View 这类模板词，只保留能描述内容的频道标签。
        self.assertEqual(deep_series.cover_style_label("iOS View、科技财经"), "科技财经")
        self.assertEqual(deep_series.cover_style_label("iOS干净排版、强反差"), "强反差")
        self.assertEqual(
            deep_series.cover_style_label("{'visual_tone': '工业科技、冷色理性', 'design_language': '半透明剖面'}"),
            "工业科技",
        )

    def test_visual_element_label_cleans_dict_and_truncated_name_placeholders(self):
        self.assertEqual(deep_series.visual_element_label({"name": "AI 数据中心机柜"}), "AI 数据中心机柜")
        self.assertEqual(deep_series.visual_element_label("{'name': 'AI 数据中心机柜'"), "AI 数据中心机柜")
        self.assertEqual(deep_series.visual_element_label("{'label': '冷却桥梁', 'role': 'bridge'}"), "冷却桥梁")

    def test_normalize_visual_design_cleans_structured_element_names(self):
        design = deep_series.normalize_visual_design(
            {"title": "AI时代的隐形地基"},
            {"title": "大金：空调公司为什么站在 AI 基建背后"},
            {
                "main_elements": [
                    {"name": "AI 数据中心机柜"},
                    "{'name': '冷却桥梁', 'role': 'bridge'}",
                ],
                "scene_cards": [
                    {"keyword": {"name": "机柜"}, "asset": "hero", "label": {"name": "AI 数据中心机柜"}},
                ],
            },
            "",
        )

        self.assertIn("AI 数据中心机柜", design["main_elements"])
        self.assertIn("冷却桥梁", design["main_elements"])
        self.assertEqual(design["scene_cards"][0]["keyword"], "机柜")
        self.assertEqual(design["scene_cards"][0]["label"], "AI 数据中心机柜")

    def test_normalize_visual_design_turns_layout_metaphors_into_industry_clues(self):
        # Dell 这类产业线索展示给观众看，不能直接暴露“左侧/右侧/老树/根系”这类构图提示词。
        design = deep_series.normalize_visual_design(
            {"title": "AI时代老树开新花"},
            {"title": "Dell：卖电脑的公司为什么吃到 AI 服务器红利"},
            {
                "main_elements": ["左侧老树与 PC 根系", "中央工程桥", "右侧 AI 数据中心"],
                "scene_cards": [
                    {"keyword": "PC 老牌公司", "asset": "hero", "label": "老树：PC 与企业入口"},
                ],
            },
            "",
        )

        self.assertEqual(design["main_elements"][:3], ["PC客户基础", "整机集成交付", "AI数据中心"])
        self.assertEqual(design["scene_cards"][0]["label"], "PC企业入口")

    def test_fallback_visual_design_prioritizes_system_stability_theme(self):
        # 这期主题是系统跑稳，兜底视觉不能被背景段落里的“这意味着”、HBM、封装误导成味精或芯片供应链。
        context_text = (
            "这意味着 AI 基建不是买服务器这么简单。"
            "早期行业关注 GPU、HBM 和先进封装，但现在更难的是运维、调度、网络、供电冷却和安全治理。"
        )

        design = deep_series.normalize_visual_design(
            {"title": "AI时代最缺的不是芯片"},
            {
                "title": "AI 最缺的可能是能把系统跑稳的人",
                "question": "为什么 AI 基建最后还是缺工程人才？重点探讨数据中心运维、集群调度、供电冷却、网络故障、安全治理和跨领域工程能力。",
            },
            {},
            context_text,
        )

        elements = " ".join(design["main_elements"])
        self.assertIn("工程团队", elements)
        self.assertIn("稳定运行", elements)
        self.assertIn("集群调度", elements)
        self.assertIn("供电冷却", elements)
        self.assertIn("网络排障", elements)
        self.assertNotIn("味精颗粒", elements)
        self.assertNotIn("高带宽内存", elements)
        self.assertNotIn("封装基板", elements)

    def test_match_visual_scene_uses_industry_aliases_when_keyword_is_not_exact(self):
        # Dell 脚本里的自然表达不一定逐字等于 scene_cards.keyword，匹配要能靠产业别名命中场景。
        visual_design = {
            "main_elements": ["PC客户基础", "整机集成交付", "AI数据中心"],
            "scene_cards": [
                {"keyword": "PC 老牌公司", "asset": "old_pc_tree", "label": "PC企业入口"},
                {"keyword": "不是只买 GPU", "asset": "gpu_vs_system", "label": "GPU 不是完整系统"},
                {"keyword": "存储瓶颈", "asset": "storage_data_pipeline", "label": "数据供不上，GPU 等待"},
            ],
        }

        self.assertEqual(
            deep_series.match_visual_scene("Dell 不就是卖电脑的吗，", visual_design)["asset"],
            "old_pc_tree",
        )
        self.assertEqual(
            deep_series.match_visual_scene("企业要的不是一块显卡，而是一整套系统。", visual_design)["asset"],
            "gpu_vs_system",
        )
        visual_design["scene_cards"].append(
            {"keyword": "AI 基础设施", "asset": "ai_infrastructure_stack", "label": "算力、存储、网络、散热"}
        )
        self.assertEqual(
            deep_series.match_visual_scene("大模型还需要 CPU、内存、高速网络、存储、机柜、电力和散热。", visual_design)["asset"],
            "ai_infrastructure_stack",
        )
        self.assertEqual(
            deep_series.match_visual_scene("数据供不上时，GPU 只能等。", visual_design)["asset"],
            "storage_data_pipeline",
        )

    def test_match_visual_scene_uses_robot_work_scene_aliases(self):
        # 机器人脚本里的自然台词不会写 warehouse_robot，必须靠中文场景词命中不同 SVG 资产。
        visual_design = {
            "main_elements": ["仓库搬运", "外卖配送", "商用清洁", "酒店送物"],
            "scene_cards": [
                {"keyword": "warehouse_robot", "asset": "isometric warehouse aisle with", "label": "仓库搬运"},
                {"keyword": "delivery_robot", "asset": "sidewalk or campus delivery ro", "label": "外卖配送"},
                {"keyword": "commercial_cleaning", "asset": "autonomous floor scrubber clea", "label": "商用清洁"},
                {"keyword": "hotel_service", "asset": "hotel service robot delivering", "label": "酒店送物"},
                {"keyword": "labor_cost", "asset": "dashboard comparing staff shif", "label": "用工成本"},
                {"keyword": "bounded_scene", "asset": "warehouse map with marked rout", "label": "场景边界"},
            ],
        }

        self.assertEqual(
            deep_series.match_visual_scene("仓库机器人把货从A点送到B点。", visual_design)["asset"],
            "isometric warehouse aisle with",
        )
        self.assertEqual(
            deep_series.match_visual_scene("园区配送可以设置固定交接点。", visual_design)["asset"],
            "sidewalk or campus delivery ro",
        )
        self.assertEqual(
            deep_series.match_visual_scene("清洁机器人闭店后沿固定路线打扫。", visual_design)["asset"],
            "autonomous floor scrubber clea",
        )
        self.assertEqual(
            deep_series.match_visual_scene("酒店机器人按房号送水送外卖。", visual_design)["asset"],
            "hotel service robot delivering",
        )
        self.assertEqual(
            deep_series.match_visual_scene("少跑多少腿、少排多少班，都能进成本表。", visual_design)["asset"],
            "dashboard comparing staff shif",
        )
        self.assertEqual(
            deep_series.match_visual_scene("路线、货架、充电点和交接点都可以被管理。", visual_design)["asset"],
            "warehouse map with marked rout",
        )

    def test_render_svg_to_image_can_hide_model_text_labels(self):
        svg_path = os.path.join(self.tmpdir, "labelled.svg")
        with open(svg_path, "w", encoding="utf-8") as f:
            f.write('<svg width="100" height="100"><rect x="0" y="0" width="100" height="100" fill="#FFFFFF"/><text x="0" y="50" fill="#FF0000" font-size="60">TXT</text></svg>')

        image = deep_series.render_svg_to_image(svg_path, (100, 100), include_text=False)

        self.assertIsNotNone(image)
        colors = image.convert("RGB").getcolors(maxcolors=100000)
        red_pixels = sum(count for count, color in colors if color[0] > 180 and color[1] < 80 and color[2] < 80)
        self.assertEqual(red_pixels, 0)

    def test_render_svg_to_image_hides_text_but_keeps_opacity(self):
        # background.svg 常用低透明度纹理；隐藏文字时也必须保留 opacity，否则整张背景会被错误画白。
        svg_path = os.path.join(self.tmpdir, "opacity_background.svg")
        with open(svg_path, "w", encoding="utf-8") as f:
            f.write(
                '<svg width="100" height="100">'
                '<rect x="0" y="0" width="100" height="100" fill="#000000"/>'
                '<rect x="0" y="0" width="100" height="100" fill="#FFFFFF" opacity="0.1"/>'
                '<text x="0" y="60" fill="#FF0000" font-size="60">TXT</text>'
                '</svg>'
            )

        image = deep_series.render_svg_to_image(svg_path, (100, 100), include_text=False)

        self.assertIsNotNone(image)
        pixel = image.convert("RGB").getpixel((8, 8))
        self.assertLess(pixel[0], 80)
        colors = image.convert("RGB").getcolors(maxcolors=100000)
        red_pixels = sum(count for count, color in colors if color[0] > 180 and color[1] < 80 and color[2] < 80)
        self.assertEqual(red_pixels, 0)

    def test_create_deep_slide_images_reuses_visual_design_for_scene_cards(self):
        script_path = os.path.join(self.tmpdir, "script.md")
        audio_path = os.path.join(self.tmpdir, "dialogue.mp3")
        with open(script_path, "w", encoding="utf-8") as f:
            f.write("女：ABF薄膜藏在GPU封装基板里。")
        with open(audio_path + ".timing.json", "w", encoding="utf-8") as f:
            f.write('{"segments":[{"role":"female","duration":4.0,"text":"ABF薄膜藏在GPU封装基板里。"}]}')
        visual_design = {
            "composition": "center_bridge",
            "palette": ["#F5F5F7", "#1D1D1F", "#007AFF", "#FF9500"],
            "scene_cards": [{"keyword": "ABF", "asset": "bridge", "label": "ABF薄膜"}],
            "asset_paths": {"bridge": os.path.join(self.tmpdir, "bridge.svg")},
        }
        captured_designs = []

        def fake_create_card(title, subtitle, body, output_path, accent="#007AFF", slide_index=None, slide_total=None, visual_design=None, scene=None, **_kwargs):
            captured_designs.append((visual_design, scene))
            with open(output_path, "wb") as f:
                f.write(b"png")
            return output_path

        with patch("deep_series.create_text_card", side_effect=fake_create_card):
            image_paths = deep_series.create_deep_slide_images(
                {"title": "AI时代的隐形地基"},
                {"title": "味之素：味精公司为什么成了高端芯片底座"},
                script_path,
                audio_path,
                visual_design=visual_design,
            )

        self.assertEqual(len(image_paths), 1)
        self.assertIs(captured_designs[0][0], visual_design)
        self.assertEqual(captured_designs[0][1]["label"], "ABF薄膜")

    def test_create_text_card_shows_one_foreground_svg_without_svg_text(self):
        # 左侧视觉舞台只贴一张前景 SVG，但隐藏 SVG 内文字，避免错位文本压到视频画面里。
        hero_path = os.path.join(self.tmpdir, "hero.svg")
        bridge_path = os.path.join(self.tmpdir, "bridge.svg")
        background_path = os.path.join(self.tmpdir, "background.svg")
        for path in (hero_path, bridge_path, background_path):
            with open(path, "w", encoding="utf-8") as f:
                f.write('<svg width="420" height="260"><rect width="420" height="260" fill="#FFFFFF"/><text x="20" y="40">工程团队</text></svg>')
        calls = []

        def fake_paste(_image, svg_path, box, include_text=False):
            calls.append((os.path.basename(svg_path), box, include_text))
            return True

        with patch("deep_series.paste_svg_asset", side_effect=fake_paste):
            deep_series.create_text_card(
                "AI 最缺的可能是能把系统跑稳的人",
                "深度观点",
                "锅反而甩到排障的人身上？",
                os.path.join(self.tmpdir, "slide.png"),
                visual_design={
                    "main_elements": ["工程团队", "稳定运行", "集群调度"],
                    "asset_paths": {
                        "background": background_path,
                        "hero": hero_path,
                        "bridge": bridge_path,
                    },
                },
                scene={"asset": "bridge", "label": "稳定运行"},
            )

        foreground_calls = [item for item in calls if item[0] in ("hero.svg", "bridge.svg")]
        self.assertEqual(len(foreground_calls), 1)
        self.assertEqual(foreground_calls[0][0], "bridge.svg")
        self.assertFalse(foreground_calls[0][2])

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
        self.assertTrue(result["research_plan_path"].endswith("research_plan.md"))
        self.assertTrue(result["research_trace_path"].endswith("research_trace.json"))
        self.assertTrue(os.path.exists(result["research_path"]))
        self.assertTrue(os.path.exists(result["research_plan_path"]))
        self.assertTrue(os.path.exists(result["research_trace_path"]))
        self.assertTrue(os.path.exists(result["script_notes_path"]))
        self.assertTrue(os.path.exists(result["documentary_package_path"]))
        self.assertTrue(os.path.exists(result["agent_log_path"]))
        with open(result["research_trace_path"], "r", encoding="utf-8") as f:
            trace = json.load(f)
        self.assertEqual(trace["plan"]["series"], "AI 未来三年系列")
        self.assertEqual(trace["attempts"][0]["source_count"], 3)
        self.assertIn("AI 为什么会替代搜索？", trace["attempts"][0]["keywords"])
        _audio.assert_not_called()
        _slides.assert_not_called()
        _video.assert_not_called()

    def test_run_episode_pipeline_writes_and_logs_plan_before_search(self):
        episode = {"title": "AI 为什么会替代搜索？", "question": "AI 为什么会替代搜索？"}
        series = {"title": "AI 未来三年系列", "description": "测试说明"}
        events = []

        def fake_print(*args, **_kwargs):
            text = " ".join(str(arg) for arg in args)
            if "开始生成研究计划" in text:
                events.append("plan_log")
            if "第 1/3 轮检索资料" in text:
                events.append("search_log")

        def fake_collect(*_args, **_kwargs):
            # 检索一开始就应该已经能看到研究计划文件，方便长调研时先人工检查方向。
            plan_paths = []
            for root, _dirs, files in os.walk(self.tmpdir):
                if "research_plan.md" in files:
                    plan_paths.append(os.path.join(root, "research_plan.md"))
            self.assertTrue(plan_paths)
            with open(plan_paths[0], "r", encoding="utf-8") as f:
                plan_text = f.read()
            self.assertIn("# 研究计划", plan_text)
            self.assertIn("首轮检索词", plan_text)
            events.append("collect")
            return [
                {"title": "Source 1", "link": "https://example.com/1", "content": "AI search source"},
                {"title": "Source 2", "link": "https://example.com/2", "content": "AI search source"},
                {"title": "Source 3", "link": "https://example.com/3", "content": "AI search source"},
            ]

        with patch("builtins.print", side_effect=fake_print), \
                patch("deep_series.collect_research_sources", side_effect=fake_collect), \
                patch("deep_series.generate_research_report", return_value="研究报告"), \
                patch("deep_series.audit_research", return_value="事实审校通过"), \
                patch("deep_series.generate_dialogue_script_with_duration_guard", return_value=("女：计划先写。", {"blocked": False, "estimated_seconds": 30.0, "attempts": 1})), \
                patch("deep_series.generate_script_notes", return_value="脚本备注"), \
                patch("deep_series.generate_documentary_package", return_value="纪录片包"):
            deep_series.run_episode_pipeline(series, episode, base_dir=self.tmpdir)

        self.assertLess(events.index("plan_log"), events.index("search_log"))
        self.assertLess(events.index("search_log"), events.index("collect"))

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
        audio_path = os.path.join(self.tmpdir, "dialogue.mp3")
        with open(audio_path, "wb") as f:
            f.write(b"audio")
        config = {
            "series": [
                {
                    "title": "Series A",
                    "description": "Demo",
                    "episodes": [{
                        "title": "Episode A",
                        "question": "Question A",
                        "script_path": "script.md",
                        "audio_path": audio_path,
                    }],
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
                patch("deep_series.generate_video_from_audio", side_effect=generate_side_effect), \
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

    def test_generate_episode_tts_by_titles_writes_audio_without_video(self):
        script_path = os.path.join(self.tmpdir, "script.md")
        with open(script_path, "w", encoding="utf-8") as f:
            f.write("女：已有脚本。\n男：先合成音频。")
        audio_path = os.path.join(self.tmpdir, "dialogue.mp3")
        config = {
            "series": [
                {
                    "title": "Series A",
                    "description": "Demo",
                    "episodes": [{
                        "title": "Episode A",
                        "question": "Question A",
                        "script_path": script_path,
                    }],
                }
            ]
        }
        deep_series.save_config(config, self.config_path)

        def fake_convert(_script_path, output_path):
            self.assertEqual(output_path, os.path.join(self.tmpdir, "dialogue.mp3"))
            return audio_path

        with patch("deep_series.CONFIG_PATH", self.config_path), \
                patch("deep_series.convert_dialogue_to_audio", side_effect=fake_convert) as mock_convert, \
                patch("deep_series.validate_deep_audio_duration", return_value={"blocked": False, "actual_seconds": 42.0, "reasons": []}):
            result = deep_series.generate_episode_tts_by_titles("Series A", "Episode A")

        self.assertEqual(result["audio_path"], audio_path)
        self.assertEqual(result["actual_seconds"], 42.0)
        self.assertEqual(result.get("video_path", ""), "")
        mock_convert.assert_called_once_with(script_path, os.path.join(self.tmpdir, "dialogue.mp3"))
        latest = deep_series.load_config(self.config_path)
        episode = latest["series"][0]["episodes"][0]
        self.assertEqual(episode["audio_path"], audio_path)
        self.assertEqual(episode["actual_seconds"], 42.0)
        self.assertFalse(episode.get("generated", False))
        self.assertEqual(episode.get("video_path", ""), "")

    def test_generate_episode_tts_by_titles_records_oversized_audio_state(self):
        script_path = os.path.join(self.tmpdir, "script.md")
        with open(script_path, "w", encoding="utf-8") as f:
            f.write("女：已有脚本，完整合成后再判断是否超时。")
        audio_path = os.path.join(self.tmpdir, "dialogue.mp3")
        config = {
            "series": [
                {
                    "title": "Series A",
                    "description": "Demo",
                    "episodes": [{"title": "Episode A", "question": "Question A", "script_path": script_path}],
                }
            ]
        }
        deep_series.save_config(config, self.config_path)

        def fake_convert(_script_path, output_path):
            with open(output_path, "wb") as f:
                f.write(b"audio")
            return audio_path

        with patch("deep_series.CONFIG_PATH", self.config_path), \
                patch("deep_series.convert_dialogue_to_audio", side_effect=fake_convert), \
                patch("deep_series.validate_deep_audio_duration", return_value={"blocked": True, "actual_seconds": 181.0, "reasons": ["音频实际 181 秒，超过 180 秒"]}):
            result = deep_series.generate_episode_tts_by_titles("Series A", "Episode A")

        self.assertEqual(result["audio_path"], audio_path)
        self.assertEqual(result["actual_seconds"], 181.0)
        self.assertIn("超过", result["quality_block_reason"])
        latest = deep_series.load_config(self.config_path)
        episode = latest["series"][0]["episodes"][0]
        self.assertEqual(episode["audio_path"], audio_path)
        self.assertEqual(episode["actual_seconds"], 181.0)
        self.assertIn("超过", episode["quality_block_reason"])
        self.assertFalse(episode.get("generated", False))

    def test_generate_episode_video_uses_existing_tts_without_resynthesizing(self):
        script_path = os.path.join(self.tmpdir, "script.md")
        audio_path = os.path.join(self.tmpdir, "dialogue.mp3")
        with open(script_path, "w", encoding="utf-8") as f:
            f.write("女：已有脚本。\n男：继续生成。")
        with open(audio_path, "wb") as f:
            f.write(b"audio")
        with open(audio_path + ".timing.json", "w", encoding="utf-8") as f:
            f.write('{"total_duration": 42.0, "segments": []}')
        config = {
            "series": [
                {
                    "title": "Series A",
                    "description": "Demo",
                    "episodes": [{
                        "title": "Episode A",
                        "question": "Question A",
                        "script_path": script_path,
                        "audio_path": audio_path,
                    }],
                }
            ]
        }
        deep_series.save_config(config, self.config_path)

        with patch("deep_series.CONFIG_PATH", self.config_path), \
                patch("deep_series.convert_dialogue_to_audio", side_effect=AssertionError("不应在视频阶段重新合成 TTS")) as mock_convert, \
                patch("deep_series.build_visual_design", return_value={}), \
                patch("deep_series.create_deep_slide_images", return_value=["cover.png"]), \
                patch("deep_series.step_video", return_value=os.path.join(self.tmpdir, "demo.mp4")), \
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
        mock_convert.assert_not_called()
        latest = deep_series.load_config(self.config_path)
        episode = latest["series"][0]["episodes"][0]
        self.assertTrue(episode["generated"])

    def test_generate_episode_video_requires_existing_tts_audio(self):
        script_path = os.path.join(self.tmpdir, "script.md")
        with open(script_path, "w", encoding="utf-8") as f:
            f.write("女：已有脚本。")
        config = {
            "series": [
                {
                    "title": "Series A",
                    "description": "Demo",
                    "episodes": [{"title": "Episode A", "question": "Question A", "script_path": script_path}],
                }
            ]
        }
        deep_series.save_config(config, self.config_path)

        with patch("deep_series.CONFIG_PATH", self.config_path):
            with self.assertRaisesRegex(ValueError, "请先合成TTS"):
                deep_series.generate_episode_video_by_titles("Series A", "Episode A")

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

    def test_generate_deep_feedback_advice_writes_report_and_codex_prompt(self):
        metrics_path = os.path.join(self.tmpdir, "metrics.json")
        with open(metrics_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "videos": [
                        {
                            "series": "AI时代的隐形地基",
                            "episode": "HOYA：眼镜公司为什么掌握光刻入口",
                            "views": 320,
                            "avg_view_seconds": 41,
                            "completion_rate": 0.2,
                            "click_rate": 0.018,
                        }
                    ]
                },
                f,
                ensure_ascii=False,
            )
        config = {
            "series": [
                {
                    "title": "AI时代的隐形地基",
                    "episodes": [
                        {
                            "title": "HOYA：眼镜公司为什么掌握光刻入口",
                            "publish_title": "HOYA站上光刻入口",
                            "actual_seconds": 205.0,
                            "source_count": 2,
                            "quality_block_reason": "音频实际 205 秒，超过 180 秒",
                        }
                    ],
                }
            ]
        }
        with patch("deep_series.load_config", return_value=config), \
                patch("deep_series.call_llm", return_value="## 优化建议\n\n请先收紧时长和封面。") as mock_llm:
            result = deep_series.generate_deep_feedback_advice(metrics_path=metrics_path, output_dir=self.tmpdir)

        self.assertTrue(os.path.exists(result["report_path"]))
        self.assertTrue(os.path.exists(result["advice_path"]))
        with open(result["advice_path"], "r", encoding="utf-8") as f:
            advice = f.read()
        self.assertIn("可复制给 Codex 的执行提示词", advice)
        self.assertIn("请先收紧时长和封面", advice)
        prompt = mock_llm.call_args.args[0]
        self.assertIn("播放量", prompt)
        self.assertIn("完播率", prompt)

    def test_build_deep_feedback_report_matches_metrics_by_publish_title_without_series(self):
        metrics_path = os.path.join(self.tmpdir, "metrics.json")
        with open(metrics_path, "w", encoding="utf-8") as f:
            json.dump(
                {"videos": [{"episode": "HOYA站上光刻入口", "publish_title": "HOYA站上光刻入口", "views": 48}]},
                f,
                ensure_ascii=False,
            )
        config = {
            "series": [
                {
                    "title": "AI时代的隐形地基",
                    "episodes": [
                        {
                            "title": "HOYA：眼镜公司为什么掌握光刻入口",
                            "publish_title": "HOYA站上光刻入口",
                            "generated": True,
                        }
                    ],
                }
            ]
        }

        report = deep_series.build_deep_feedback_report(config, metrics_path=metrics_path)

        # B站接口不会返回本地系列名，所以要允许按发布标题匹配，否则真实播放数进不了回流报告。
        self.assertEqual(report["videos"][0]["metrics"]["views"], 48)

    def test_build_deep_feedback_report_matches_prefixed_and_rewritten_bilibili_titles(self):
        metrics_path = os.path.join(self.tmpdir, "metrics.json")
        with open(metrics_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "videos": [
                        {"publish_title": "AI时代的隐形地基：空调巨头的AI底牌", "views": 95},
                        {"publish_title": "HOYA站上光刻入口", "views": 48},
                    ]
                },
                f,
                ensure_ascii=False,
            )
        config = {
            "series": [
                {
                    "title": "AI时代的隐形地基",
                    "episodes": [
                        {"title": "大金：空调公司为什么站在 AI 基建背后", "publish_title": "空调巨头的AI底牌", "generated": True},
                        {"title": "HOYA：眼镜公司为什么掌握光刻入口", "publish_title": "卖眼镜的HOYA，卡在AI芯片光刻前一步？", "generated": True},
                    ],
                }
            ]
        }

        report = deep_series.build_deep_feedback_report(config, metrics_path=metrics_path)

        # B站标题常带系列名前缀或发布后改写，回流报告要尽量把这些真实指标匹配回来。
        self.assertEqual(report["videos"][0]["metrics"]["views"], 95)
        self.assertEqual(report["videos"][1]["metrics"]["views"], 48)

    def test_generate_deep_feedback_advice_auto_scrapes_bilibili_metrics(self):
        metrics_path = os.path.join(self.tmpdir, "deep_feedback_metrics.json")
        with open(metrics_path, "w", encoding="utf-8") as f:
            json.dump({"videos": [{"episode": "HOYA站上光刻入口", "views": 320}]}, f, ensure_ascii=False)
        config = {
            "series": [
                {
                    "title": "AI时代的隐形地基",
                    "episodes": [{"title": "HOYA站上光刻入口", "generated": True}],
                }
            ]
        }

        with patch("deep_series.load_config", return_value=config), \
                patch("deep_series.call_llm", return_value="## 优化建议"), \
                patch("crawler.bilibili_feedback.scrape_bilibili_article_metrics", return_value={
                    "metrics_path": metrics_path,
                    "metric_count": 1,
                    "source": "bilibili_detail_page",
                    "error": "",
                }) as mock_scrape:
            result = deep_series.generate_deep_feedback_advice(output_dir=self.tmpdir)

        mock_scrape.assert_called_once()
        self.assertEqual(result["metrics_path"], metrics_path)
        self.assertEqual(result["metric_count"], 1)
        self.assertEqual(result["metrics_source"], "bilibili_detail_page")
        self.assertEqual(result["metrics_error"], "")
        with open(result["report_path"], "r", encoding="utf-8") as f:
            report = json.load(f)
        self.assertEqual(report["summary"]["metric_count"], 1)

    def test_generate_deep_feedback_advice_stops_when_bilibili_scrape_fails(self):
        config = {
            "series": [
                {
                    "title": "AI时代的隐形地基",
                    "episodes": [{"title": "HOYA站上光刻入口", "generated": True}],
                }
            ]
        }

        with patch("deep_series.load_config", return_value=config), \
                patch("crawler.bilibili_feedback.scrape_bilibili_article_metrics", side_effect=RuntimeError("Chrome 用户目录被占用")), \
                patch("deep_series.call_llm", side_effect=RuntimeError("skip ai")) as mock_llm:
            # 数据抓取失败时不能继续用本地质量兜底生成建议，否则会把错误数据伪装成有效判断。
            with self.assertRaises(RuntimeError) as ctx:
                deep_series.generate_deep_feedback_advice(output_dir=self.tmpdir)

        self.assertIn("Chrome 用户目录被占用", str(ctx.exception))
        self.assertFalse(os.path.exists(os.path.join(self.tmpdir, deep_series.DEEP_FEEDBACK_REPORT_FILE)))
        self.assertFalse(os.path.exists(os.path.join(self.tmpdir, deep_series.DEEP_FEEDBACK_ADVICE_FILE)))
        mock_llm.assert_not_called()

    def test_generate_deep_feedback_advice_stops_when_bilibili_scrape_returns_empty_metrics(self):
        metrics_path = os.path.join(self.tmpdir, "deep_feedback_metrics.json")
        with open(metrics_path, "w", encoding="utf-8") as f:
            json.dump({"source": "bilibili_article_manager", "videos": []}, f, ensure_ascii=False)

        with patch("crawler.bilibili_feedback.scrape_bilibili_article_metrics", return_value={
            "metrics_path": metrics_path,
            "metric_count": 0,
            "source": "bilibili_article_manager",
            "error": "未从 B站创作中心页面捕获到稿件指标",
        }), patch("deep_series.call_llm", return_value="## 错误兜底建议") as mock_llm:
            # 抓到了页面但没有任何指标，同样属于不可判断的数据，不允许进入建议生成。
            with self.assertRaises(RuntimeError) as ctx:
                deep_series.generate_deep_feedback_advice(output_dir=self.tmpdir)

        self.assertIn("未从 B站创作中心页面捕获到稿件指标", str(ctx.exception))
        self.assertFalse(os.path.exists(os.path.join(self.tmpdir, deep_series.DEEP_FEEDBACK_REPORT_FILE)))
        mock_llm.assert_not_called()

    def test_collect_deep_feedback_metrics_rejects_old_bilibili_list_metrics(self):
        metrics_path = os.path.join(self.tmpdir, "old_list_metrics.json")
        with open(metrics_path, "w", encoding="utf-8") as f:
            json.dump(
                {"source": "bilibili_member_archives", "videos": [{"publish_title": "旧列表指标", "views": 48}]},
                f,
                ensure_ascii=False,
            )

        # 旧列表接口只有列表统计，不是点击“数据”后的详情页数据，不能继续进入分析。
        with self.assertRaises(RuntimeError) as ctx:
            deep_series.collect_deep_feedback_metrics(metrics_path=metrics_path)

        self.assertIn("详情页", str(ctx.exception))

    def test_generate_deep_feedback_advice_stops_when_ai_advice_fails(self):
        metrics_path = os.path.join(self.tmpdir, "metrics.json")
        with open(metrics_path, "w", encoding="utf-8") as f:
            json.dump({"videos": [{"episode": "HOYA站上光刻入口", "views": 320}]}, f, ensure_ascii=False)
        config = {
            "series": [
                {
                    "title": "AI时代的隐形地基",
                    "episodes": [{"title": "HOYA站上光刻入口", "generated": True}],
                }
            ]
        }

        with patch("deep_series.load_config", return_value=config), \
                patch("deep_series.call_llm", side_effect=RuntimeError("AI 不可用")):
            # 数据是真实的但 AI 建议失败时，也不能改用规则兜底产出看似完成的建议文件。
            with self.assertRaises(RuntimeError) as ctx:
                deep_series.generate_deep_feedback_advice(metrics_path=metrics_path, output_dir=self.tmpdir)

        self.assertIn("AI 不可用", str(ctx.exception))
        self.assertTrue(os.path.exists(os.path.join(self.tmpdir, deep_series.DEEP_FEEDBACK_REPORT_FILE)))
        self.assertFalse(os.path.exists(os.path.join(self.tmpdir, deep_series.DEEP_FEEDBACK_ADVICE_FILE)))

    def test_feedback_ai_prompt_prioritizes_metric_and_risk_rows(self):
        videos = [
            {"series": "旧系列", "episode": f"旧主题{i}", "actual_seconds": 90, "metrics": {}, "risks": []}
            for i in range(25)
        ]
        videos.append({
            "series": "AI时代的隐形地基",
            "episode": "高风险主题",
            "actual_seconds": 205,
            "metrics": {"views": 120, "completion_rate": 0.2},
            "risks": ["完播率偏低"],
        })

        prompt = deep_series.build_deep_feedback_ai_prompt({"summary": {}, "videos": videos})

        self.assertIn("高风险主题", prompt)
        self.assertNotIn("旧主题24", prompt)

    def test_normalize_feedback_metric_row_does_not_create_missing_detail_metrics(self):
        row = deep_series.normalize_feedback_metric_row({
            "source": "bilibili_detail_page",
            "publish_title": "3M凭什么卡住芯片良率？",
            "views": 93,
            "likes": 1,
            "metric_sections": ["data_overview"],
        })

        # 详情页没有返回的播放分析字段不能补 0，否则后续会把不存在的数据当成真实低指标分析。
        self.assertEqual(row["views"], 93)
        self.assertEqual(row["likes"], 1)
        self.assertEqual(row["metric_sections"], ["data_overview"])
        self.assertNotIn("avg_view_seconds", row)
        self.assertNotIn("completion_rate", row)
        self.assertNotIn("click_rate", row)

    def test_feedback_ai_prompt_omits_missing_detail_metric_fields(self):
        videos = [{
            "series": "AI时代的隐形地基",
            "episode": "3M凭什么卡住芯片良率？",
            "publish_title": "3M凭什么卡住芯片良率？",
            "actual_seconds": 120,
            "source_count": 3,
            "metrics": {"views": 93, "likes": 1, "metric_sections": ["data_overview"]},
            "risks": [],
        }]

        prompt = deep_series.build_deep_feedback_ai_prompt({"summary": {}, "videos": videos})
        prompt_rows = json.loads(prompt.split("视频数据：", 1)[1])

        # AI 只能看到详情页真实存在的字段；不存在的完播率、点击率、平均观看不进入视频数据。
        self.assertEqual(prompt_rows[0]["播放量"], 93)
        self.assertEqual(prompt_rows[0]["点赞"], 1)
        self.assertNotIn("平均观看秒数", prompt_rows[0])
        self.assertNotIn("完播率", prompt_rows[0])
        self.assertNotIn("点击率", prompt_rows[0])

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

        def fake_create_text_card(title, subtitle, body, output_path, accent="#007AFF", slide_index=None, slide_total=None, **_kwargs):
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

    def test_create_deep_slide_images_passes_current_speaker_to_cards(self):
        script_path = os.path.join(self.tmpdir, "speaker_avatar_script.md")
        audio_path = os.path.join(self.tmpdir, "speaker_avatar_audio.mp3")
        with open(script_path, "w", encoding="utf-8") as f:
            f.write("女：搜索入口正在变化。\n男：对话会变成新的软件入口。")
        with open(audio_path + ".timing.json", "w", encoding="utf-8") as f:
            f.write(
                '{"segments":[{"role":"female","slide_index":0,"duration":4.0,"text":"搜索入口正在变化。"},{"role":"male","slide_index":1,"duration":4.0,"text":"对话会变成新的软件入口。"}]}'
            )

        captured = []

        def fake_create_text_card(title, subtitle, body, output_path, **kwargs):
            # 图卡层要知道当前说话人，才能在视频里高亮对应的固定主持人形象。
            captured.append(kwargs.get("current_speaker"))
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

        self.assertEqual(captured, ["female", "male"])

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
