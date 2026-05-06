import unittest

import main


class TestKeywordConfig(unittest.TestCase):
    def test_keywords_include_high_signal_ai_terms(self):
        keywords = set(main.TOPICS[0]["keywords"])

        expected = {
            "OpenAI Codex 最新消息",
            "GPT-5.5 最新消息",
            "Claude Code 最新消息",
            "Claude 4 最新消息",
            "Google DeepMind 最新动态",
            "AI coding agent latest news",
            "AI developer tools latest news",
            "豆包 最新动态",
            "DeepSeek 最新消息",
            "NVIDIA Blackwell latest news",
            "AMD Instinct 最新消息",
            "AI 编程智能体 最新消息",
        }
        for item in expected:
            self.assertIn(item, keywords)

    def test_keywords_remove_low_signal_or_stale_terms(self):
        keywords = set(main.TOPICS[0]["keywords"])

        removed = {
            "GPT 最新消息",
            "OpenAI 最新消息",
            "ChatGPT 最新消息",
            "ChatGPT Images 最新消息",
            "Sora 最新消息",
            "Claude 最新消息",
            "Google AI最新进展",
            "Gemini 最新消息",
            "Meta AI最新动态",
            "Grok 最新消息",
            "AI Agent 最新突破",
            "智能体 最新应用",
            "AutoGPT 最新消息",
            "GitHub Copilot 最新消息",
            "Qwen 最新消息",
            "Hunyuan 最新消息",
            "Doubao 最新消息",
            "Kimi 最新动态",
            "MiniMax 最新消息",
            "Stepfun 最新消息",
            "英伟达 最新进展",
        }
        for item in removed:
            self.assertNotIn(item, keywords)


if __name__ == "__main__":
    unittest.main()
