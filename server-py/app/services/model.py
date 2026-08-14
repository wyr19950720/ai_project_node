# server-py/app/services/model.py
# 模型工厂：统一创建模型实例，业务代码不直接 new ChatOpenAI
from langchain_openai import ChatOpenAI, OpenAIEmbeddings

from app.config import config


def create_chat_model(temperature: float = 0.7, streaming: bool = False) -> ChatOpenAI:
    """
    创建对话模型
    temperature: 随机性，0=确定，1=创意
    streaming:   是否流式输出
    """
    instance = ChatOpenAI(
        model=config.ai.primary_model,
        api_key=config.ai.deepseek_key,
        base_url=config.ai.base_url,
        temperature=temperature,
        streaming=streaming,
        stream_usage=streaming,
        timeout=30.0,
    )
    return instance


class ZhipuAIEmbeddings:
    """轻量 ZhipuAI Embedding 客户端（OpenAI 兼容协议）"""

    def __init__(self, api_key: str, model: str = "embedding-3"):
        self._client = OpenAIEmbeddings(
            model=model,
            api_key=api_key,
            base_url="https://open.bigmodel.cn/api/paas/v4",
            check_embedding_ctx_length=False,
        )

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._client.embed_documents(texts)

    def embed_query(self, text: str) -> list[float]:
        return self._client.embed_query(text)

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        return await self._client.aembed_documents(texts)

    async def aembed_query(self, text: str) -> list[float]:
        return await self._client.aembed_query(text)


def create_embeddings():
    """
    创建 Embedding 模型（向量化文本，RAG 必用）
    注意：DeepSeek 暂无 embedding 模型，这里用 OpenAI 兼容接口
    """
    # 优先使用智谱 AI（key 格式 xxxx.xxxx）
    if config.ai.zhipu_key:
        return ZhipuAIEmbeddings(api_key=config.ai.zhipu_key, model="embedding-3")
    # 其次使用 SiliconFlow / OpenAI 兼容接口
    if config.ai.openai_key:
        return OpenAIEmbeddings(
            model=config.ai.embed_model,
            api_key=config.ai.openai_key,
            base_url=config.ai.embed_base_url,
            check_embedding_ctx_length=False,
        )
    print("⚠️  未配置 ZHIPU_API_KEY 或 OPENAI_API_KEY，RAG 功能将不可用")
    return None


# 单例：应用启动时创建一次，全局复用
chat_model = create_chat_model(temperature=0.7, streaming=True)
embeddings = create_embeddings()
