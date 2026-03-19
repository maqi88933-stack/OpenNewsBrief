import os
from dotenv import load_dotenv
from langchain_community.llms.tongyi import Tongyi
from langchain_openai import ChatOpenAI
from langchain_core.prompts import PromptTemplate

# 加载环境变量
load_dotenv()

# 定义tongyi模型工厂
class LLmFactory:
    
    def getDeepseek(self):
        # 从环境变量获取配置，如果不存在则使用之前的值作为回退（或者不设默认值以确保安全性）
        BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
        API_KEY = os.getenv("DEEPSEEK_API_KEY")
        model_name = os.getenv("DEEPSEEK_MODEL_NAME", "deepseek-chat")

        if not API_KEY:
            raise ValueError("DEEPSEEK_API_KEY 未在环境变量或 .env 文件中设置")

        llm = ChatOpenAI(
            model=model_name, 
            openai_api_key=API_KEY, 
            openai_api_base=BASE_URL,
            max_tokens=10000,
            temperature=0.1
        )
        return llm
        
   

        
    def get_llm(self, model_name):
       
        return self.getDeepseek()
        



