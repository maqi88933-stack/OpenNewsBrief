import os

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI


# 加载 .env 中的模型地址、密钥和模型名配置。
load_dotenv()


def _is_gpt5_responses_model(model_name):
    # GPT-5 非 chat 模型优先走 Responses API，避免代理端不接受 chat/completions 参数。
    lower_name = (model_name or "").lower()
    return lower_name.startswith("gpt-5") and "chat" not in lower_name


class LLmFactory:
    # 项目里的 LLM 都从这个工厂创建，避免不同流程读取到不一致的模型配置。
    def getDeepseek(self, model_name=None):
        # 从环境变量读取模型配置，默认值保留原来的 DeepSeek 兼容接口。
        base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
        api_key = os.getenv("DEEPSEEK_API_KEY")
        model_name = model_name or os.getenv("DEEPSEEK_MODEL_NAME", "deepseek-chat")

        if not api_key:
            raise ValueError("DEEPSEEK_API_KEY 未在环境变量或 .env 文件中设置")

        chat_kwargs = {
            "model": model_name,
            "api_key": api_key,
            "base_url": base_url,
            "max_completion_tokens": 10000,
        }

        if _is_gpt5_responses_model(model_name):
            # GPT-5 系列使用 Responses API 参数：input + max_output_tokens。
            chat_kwargs["use_responses_api"] = True
        else:
            # 其他 OpenAI 兼容模型沿用低温度生成，减少脚本输出波动。
            chat_kwargs["temperature"] = 0.1

        return ChatOpenAI(**chat_kwargs)

    def get_llm(self, model_name=None):
        # 保留旧入口，允许调用方显式传入模型名。
        return self.getDeepseek(model_name)
