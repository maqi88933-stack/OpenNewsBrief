# -*- coding: utf-8 -*-
import os
import unittest
from unittest.mock import patch

from langchain_core.messages import HumanMessage

from util.llm import LLmFactory


class TestLlmConfig(unittest.TestCase):
    def test_gpt5_model_uses_responses_api_payload(self):
        with patch.dict(
            os.environ,
            {
                "DEEPSEEK_BASE_URL": "https://example.com/v1",
                "DEEPSEEK_API_KEY": "test-key",
                "DEEPSEEK_MODEL_NAME": "gpt-5.4",
            },
            clear=False,
        ):
            llm = LLmFactory().getDeepseek()
            payload = llm._get_request_payload([HumanMessage(content="test")])

        # GPT-5 系列在当前代理上更适合走 Responses API，避免 chat/completions 参数不兼容。
        self.assertTrue(llm.use_responses_api)
        self.assertIn("input", payload)
        self.assertIn("max_output_tokens", payload)
        self.assertNotIn("messages", payload)
        self.assertNotIn("max_completion_tokens", payload)
        self.assertNotIn("temperature", payload)

    def test_deepseek_model_keeps_chat_completions_payload(self):
        with patch.dict(
            os.environ,
            {
                "DEEPSEEK_BASE_URL": "https://example.com/v1",
                "DEEPSEEK_API_KEY": "test-key",
                "DEEPSEEK_MODEL_NAME": "deepseek-chat",
            },
            clear=False,
        ):
            llm = LLmFactory().getDeepseek()
            payload = llm._get_request_payload([HumanMessage(content="test")])

        # 非 GPT-5 模型继续保持原来的 Chat Completions 路径，避免影响既有 DeepSeek 配置。
        self.assertFalse(bool(llm.use_responses_api))
        self.assertIn("messages", payload)
        self.assertIn("max_completion_tokens", payload)
        self.assertNotIn("input", payload)


if __name__ == "__main__":
    unittest.main()
