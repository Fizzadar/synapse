# Copyright 2020 The Matrix.org Foundation C.I.C.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from typing import Any

from synapse.config._base import Config
from synapse.types import JsonDict
from synapse.util.check_dependencies import check_requirements


class RedisConfig(Config):
    section = "redis"

    def read_config(self, config: JsonDict, **kwargs: Any) -> None:
        redis_config = config.get("redis") or {}
        self.redis_enabled = redis_config.get("enabled", False)

        if not self.redis_enabled:
            return

        check_requirements("redis")

        self.redis_host = redis_config.get("host", "localhost")
        self.redis_port = redis_config.get("port", 6379)
        self.redis_password = redis_config.get("password")

        cache_shard_config = redis_config.get("cache_shards")
        if cache_shard_config:
            self.cache_shard_hosts = cache_shard_config.get("hosts", [])
            self.cache_shard_expire = cache_shard_config.get("expire_caches", False)
            self.cache_shard_ttl = cache_shard_config.get("cache_entry_ttl", False)

    def generate_config_section(self, **kwargs: Any) -> str:
        return """\
        # Configuration for Redis when using workers. This *must* be enabled when
        # using workers (unless using old style direct TCP configuration).
        #
        redis:
          # Uncomment the below to enable Redis support.
          #
          #enabled: true

          # Optional host and port to use to connect to redis. Defaults to
          # localhost and 6379
          #
          #host: localhost
          #port: 6379

          # Optional password if configured on the Redis instance
          #
          #password: <secret_password>

          # Optional one or more Redis hosts to use for long term sharedd caches.
          # Should be configured to automatically expire records when out of
          # memory, and not be the same instance as used for replication.
          #
          #cache_shards:
          #  enabled: false
          #  expire_caches: false
          #  cache_entry_ttl: 30m
          #  hosts:
          #    - host: localhost
          #      port: 6379
        """
