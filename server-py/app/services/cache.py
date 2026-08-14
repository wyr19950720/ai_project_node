# server-py/app/services/cache.py
# 精确缓存：相同的 system + message 直接返回缓存，不调 API
import hashlib
import time

from app.config import config


class ExactCache:
    def __init__(self):
        self.store: dict[str, dict] = {}  # key → { content, ts, tokens }
        self.stats = {"hits": 0, "misses": 0, "savedTokens": 0}

    def _key(self, system_prompt: str | None, message: str) -> str:
        raw = f"{system_prompt or ''}||{message}"
        return hashlib.md5(raw.encode("utf-8")).hexdigest()

    def get(self, system_prompt: str | None, message: str):
        k = self._key(system_prompt, message)
        entry = self.store.get(k)

        if not entry:
            self.stats["misses"] += 1
            return None

        # 毫秒级 TTL（与 JS 版保持一致）
        if (time.time() * 1000) - entry["ts"] > config.cache.ttl:
            del self.store[k]
            self.stats["misses"] += 1
            return None

        self.stats["hits"] += 1
        self.stats["savedTokens"] += entry.get("tokens", 0)
        return entry

    def set(self, system_prompt: str | None, message: str, content: str, tokens: int = 0):
        k = self._key(system_prompt, message)
        self.store[k] = {"content": content, "tokens": tokens, "ts": time.time() * 1000}

        # 缓存超过 500 条时，清除最老的一批（简单 LRU）
        if len(self.store) > 500:
            oldest = sorted(self.store.items(), key=lambda kv: kv[1]["ts"])[:50]
            for key, _ in oldest:
                del self.store[key]

    @property
    def hit_rate(self) -> str:
        total = self.stats["hits"] + self.stats["misses"]
        return "0%" if total == 0 else f"{self.stats['hits'] / total * 100:.1f}%"

    def get_stats(self) -> dict:
        return {
            "size": len(self.store),
            "hits": self.stats["hits"],
            "misses": self.stats["misses"],
            "hitRate": self.hit_rate,
            "savedTokens": self.stats["savedTokens"],
        }


# 全局单例
cache = ExactCache()
