import os
from dataclasses import dataclass


@dataclass(frozen=True)
class CacheSettings:
    enabled: bool
    redis_url: str
    prefix: str
    reference_ttl: int
    transaction_ttl: int
    socket_timeout: float

    @classmethod
    def from_environment(cls):
        redis_url = os.getenv("REDIS_URL", "")
        enabled = os.getenv("CACHE_ENABLED", str(bool(redis_url))).lower() in (
            "1", "true", "yes", "on"
        )
        settings = cls(
            enabled=enabled,
            redis_url=redis_url,
            prefix="{}:{}:v1".format(
                os.getenv("CACHE_NAMESPACE", "stock-portfolio"),
                os.getenv("APP_ENV", "development"),
            ),
            reference_ttl=int(os.getenv("CACHE_REFERENCE_TTL_SECONDS", "604800")),
            transaction_ttl=int(os.getenv("CACHE_TRANSACTION_TTL_SECONDS", "86400")),
            socket_timeout=float(os.getenv("CACHE_REDIS_TIMEOUT_SECONDS", "0.25")),
        )
        if min(settings.reference_ttl, settings.transaction_ttl, settings.socket_timeout) <= 0:
            raise ValueError("Cache TTLs and Redis timeout must be positive")
        return settings
