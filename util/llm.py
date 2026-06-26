import base64
import os
from urllib.parse import urljoin

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from openai import OpenAI

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

    def get_openai_image_model_image(self, model_name=None, prompt=None, size=None, quality=None, output_format=None, n=None):
        # 封面视觉方案和 SVG 这类图片资产走独立配置，便于控制更贵模型的调用范围。
        base_url = os.getenv("OPENAI_IMAGE_BASE_URL") or os.getenv("OPENAI_BASE_URL") or "https://fast.youkeduo.site"
        api_key = os.getenv("OPENAI_IMAGE_API_KEY") or os.getenv("OPENAI_API_KEY")
        model_name = model_name or os.getenv("OPENAI_IMAGE_MODEL_NAME") or os.getenv("OPENAI_IMAGE_MODEL") or "gpt-5.5"
        openai_image_wire_api = os.getenv("OPENAI_IMAGE_WIRE_API") or "/v1/images/generations"
        if not api_key:
            raise ValueError("OPENAI_IMAGE_API_KEY 或 OPENAI_API_KEY 未在环境变量或 .env 文件中设置")
        # OpenAI SDK 会自己追加 /images/generations，这里只保留到 /v1，避免重复拼路径。
        image_base_url = urljoin(base_url.rstrip("/") + "/", openai_image_wire_api.strip("/").split("/images/generations")[0])
        client = OpenAI(
            api_key=api_key,
            # 国内用户可替换为第三方转发地址
            base_url=image_base_url
        )
        if size is None:
            size = "1024x1024"
        if quality is None:
            quality = "high"
        if output_format is None:
            output_format = "png"
        if n is None:
            n = 1

        result = client.images.generate(
            model=model_name,           # GPT Image 2的API端点名称
            prompt=prompt,
            size=size,              # 支持：1024x1024 / 1536x1024 / 1024x1536 / 2048x2048
            quality=quality,                # low / medium / high
            output_format=output_format,           # png / jpeg / webp
            n=n
        )
        image_data = base64.b64decode(result.data[0].b64_json)

        return image_data


    def get_llm(self, model_name=None):
        # 保留旧入口，允许调用方显式传入模型名。
        return self.getDeepseek(model_name)
