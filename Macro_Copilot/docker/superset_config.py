# ./docker/superset_config.py

SQLALCHEMY_DATABASE_URI = "postgresql://quantuser:myStrongPass@tsdb:5432/macrodata"

SECRET_KEY = "g7y7ZHV0MBaPCt7myyWXcnDp_u-dkiZeF0ijgpTptpYuwjNv91ZzPTtLI5ZXqZAQ4Cmhpa_pjc8GYt5lKZotmw"

CACHE_CONFIG = {
    "CACHE_TYPE": "RedisCache",
    "CACHE_DEFAULT_TIMEOUT": 300,
    "CACHE_KEY_PREFIX": "superset_",
    "CACHE_REDIS_HOST": "redis",
    "CACHE_REDIS_PORT": 6379,
    "CACHE_REDIS_DB": 1,
    "CACHE_REDIS_URL": "redis://redis:6379/1",
}