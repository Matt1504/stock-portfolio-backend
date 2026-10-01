import hashlib
import logging
import threading
import uuid

from bson import json_util

from cache.settings import CacheSettings

try:
    from redis import Redis
    from redis.exceptions import RedisError
except ImportError:
    Redis = None

    class RedisError(Exception):
        pass


logger = logging.getLogger(__name__)


class ReadCache:
    """Cache-aside reads with generation keys to invalidate all query variants.

    Old entries expire naturally. A read overlapping an invalidation can only
    populate its old generation, never the newly active generation.
    """

    def __init__(self, settings, client=None):
        self.settings = settings
        self.client = client
        self._pending_invalidations = set()
        self._lock = threading.RLock()
        if self.client is None and settings.enabled:
            if Redis is None or not settings.redis_url:
                logger.warning("Redis caching disabled: install redis and configure REDIS_URL")
            else:
                try:
                    self.client = Redis.from_url(
                        settings.redis_url,
                        decode_responses=True,
                        socket_connect_timeout=settings.socket_timeout,
                        socket_timeout=settings.socket_timeout,
                        retry_on_timeout=False,
                    )
                except (RedisError, ValueError):
                    logger.warning("Redis caching disabled: invalid connection configuration")

    @property
    def enabled(self):
        return self.settings.enabled and self.client is not None

    def _generation_key(self, namespace):
        return "{}:{}:generation".format(self.settings.prefix, namespace)

    def _flush_invalidations(self):
        # Called under the lock. Keep pending invalidations until Redis confirms
        # them; this process must not reuse pre-write data after an outage.
        if self._pending_invalidations:
            with self.client.pipeline(transaction=True) as pipeline:
                for namespace in sorted(self._pending_invalidations):
                    pipeline.set(self._generation_key(namespace), uuid.uuid4().hex)
                pipeline.execute()
            self._pending_invalidations.clear()

    def invalidate(self, *namespaces):
        if not self.enabled:
            return
        with self._lock:
            self._pending_invalidations.update(namespaces)
            try:
                self._flush_invalidations()
            except RedisError:
                logger.warning("Redis invalidation failed; pending namespaces will bypass cache until recovery")

    def _key(self, namespace, parameters):
        with self._lock:
            self._flush_invalidations()
            generation_key = self._generation_key(namespace)
            # Random tokens prevent old entries becoming active again if a
            # generation key is evicted independently of its cached entries.
            generation = self.client.get(generation_key)
            if generation is None:
                self.client.set(generation_key, uuid.uuid4().hex, nx=True)
                generation = self.client.get(generation_key)
            if generation is None:
                raise RedisError("Redis generation key was unavailable")
        arguments = json_util.dumps(parameters, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(arguments.encode("utf-8")).hexdigest()
        return "{}:{}:{}:{}".format(self.settings.prefix, namespace, generation, digest)

    def get_or_load(self, namespace, parameters, ttl, loader, encode, decode):
        if not self.enabled:
            return loader()
        try:
            key = self._key(namespace, parameters)
            payload = self.client.get(key)
        except RedisError:
            logger.warning("Redis read failed; using MongoDB")
            return loader()

        if payload is not None:
            try:
                value = decode(payload)
                logger.debug("Cache hit: %s", namespace)
                return value
            except (ValueError, TypeError, KeyError, AttributeError):
                logger.warning("Invalid cached payload for %s; reloading MongoDB", namespace)

        logger.debug("Cache miss: %s", namespace)
        value = loader()
        try:
            payload = encode(value)
        except (ValueError, TypeError, KeyError, AttributeError):
            logger.warning("Could not serialize %s; returning MongoDB data without caching", namespace)
            return value
        try:
            self.client.set(key, payload, ex=ttl)
        except RedisError:
            logger.warning("Redis write failed; returning MongoDB data")
        return value


cache = ReadCache(CacheSettings.from_environment())
