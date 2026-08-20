# server-py/app/config.py
# 统一配置入口：所有环境变量从这里读取，业务代码不直接用 os.environ
import os
import sys
from dotenv import load_dotenv

load_dotenv()


class _AppConfig:
    port = int(os.getenv("PORT", "3000"))
    env = os.getenv("NODE_ENV", "development")
    allowed_origins = (os.getenv("ALLOWED_ORIGINS") or "http://localhost:5173").split(",")


class _AiConfig:
    deepseek_key = os.getenv("DEEPSEEK_API_KEY")
    openai_key = os.getenv("OPENAI_API_KEY")
    zhipu_key = os.getenv("ZHIPU_API_KEY")
    primary_model = os.getenv("PRIMARY_MODEL", "deepseek-chat")
    embed_model = os.getenv("EMBED_MODEL", "BAAI/bge-m3")
    base_url = "https://api.deepseek.com/v1"
    embed_base_url = os.getenv("EMBED_BASE_URL", "https://api.siliconflow.cn/v1")


class _ChromaConfig:
    url = os.getenv("CHROMA_URL", "http://localhost:8000")


class _CacheConfig:
    ttl = int(os.getenv("CACHE_TTL", "1800000"))  # 30 分钟（毫秒）


class _DbConfig:
    host = os.getenv("DB_HOST", "localhost")
    port = int(os.getenv("DB_PORT", "3306"))
    user = os.getenv("DB_USER", "workmind")
    password = os.getenv("DB_PASSWORD", "workmind123")
    database = os.getenv("DB_DATABASE", "workmind")
    # 容器内可通过 DATABASE_URL 直接覆盖完整连接串
    url = os.getenv("DATABASE_URL") or (
        f"mysql+pymysql://{user}:{password}@{host}:{port}/{database}?charset=utf8mb4"
    )


class _JwtConfig:
    secret = os.getenv("JWT_SECRET", "workmind-dev-secret-change-me")
    algorithm = "HS256"
    # access token：短命 JWT（8h），每次请求携带，泄露窗口小
    access_expires_hours = int(os.getenv("JWT_ACCESS_EXPIRES_HOURS", "8"))
    # refresh token：长命随机串（7d），仅用于换发 access，落库可吊销
    refresh_expires_days = int(os.getenv("JWT_REFRESH_EXPIRES_DAYS", "7"))


class Config:
    app = _AppConfig()
    ai = _AiConfig()
    chroma = _ChromaConfig()
    cache = _CacheConfig()
    db = _DbConfig()
    jwt = _JwtConfig()


config = Config()


def validate_config():
    if not config.ai.deepseek_key:
        print("❌ 缺少 DEEPSEEK_API_KEY，请在 .env 文件中配置", file=sys.stderr)
        sys.exit(1)
    print("✓ 配置校验通过")
