# -*- coding: utf-8 -*-
import base64
import os
from types import SimpleNamespace
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

    def test_openai_image_model_returns_png_bytes_from_image_api(self):
        png_bytes = b"\x89PNG\r\n\x1a\nfake-image"
        fake_result = SimpleNamespace(
            data=[SimpleNamespace(b64_json=base64.b64encode(png_bytes).decode("ascii"))]
        )
        with patch.dict(
            os.environ,
            {
                "OPENAI_IMAGE_BASE_URL": "https://fast.youkeduo.site",
                "OPENAI_IMAGE_API_KEY": "image-test-key",
                "OPENAI_IMAGE_MODEL_NAME": "gpt-5.5",
                "OPENAI_IMAGE_WIRE_API": "/v1/images/generations",
            },
            clear=False,
        ), patch("util.llm.OpenAI") as fake_openai:
            fake_client = fake_openai.return_value
            fake_client.images.generate.return_value = fake_result

            image_data = LLmFactory().get_openai_image_model_image(
                prompt="生成一张封面",
                size="1024x1024",
                quality="high",
                output_format="png",
            )

        # 图片模型走 images.generate，返回值直接是可写入 PNG 文件的 bytes。
        self.assertEqual(image_data, png_bytes)
        fake_openai.assert_called_once_with(api_key="image-test-key", base_url="https://fast.youkeduo.site/v1")
        fake_client.images.generate.assert_called_once_with(
            model="gpt-5.5",
            prompt="生成一张封面",
            size="1024x1024",
            quality="high",
            output_format="png",
            n=1,
        )


if __name__ == "__main__":
    unittest.main()
