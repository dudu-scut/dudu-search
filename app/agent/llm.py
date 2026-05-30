"""
大模型初始化模块

负责从 .env 中读取模型配置，并创建项目统一复用的模型对象
后续主智能体和子智能体都从这里导入 model，避免在多个文件里重复加载环境变量
"""

from langchain.chat_models import init_chat_model

from app.config import settings

# 使用 OpenAI 兼容协议接入 DeepSeek 模型；具体模型名由 settings.LLM_MODEL 控制
model = init_chat_model(
    model=settings.LLM_MODEL,
    model_provider="openai",
    api_key=settings.LLM_API_KEY,
    base_url=settings.LLM_BASE_URL,
    extra_body={"thinking": {"type": "disabled"}},
)
