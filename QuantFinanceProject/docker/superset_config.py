# ./docker/superset_config.py

# The SQLAlchemy connection string to your PostgreSQL database
SQLALCHEMY_DATABASE_URI = "postgresql://quantuser:myStrongPass@tsdb:5432/quantdata"

# Your secret key - REPLACE THIS VALUE
SECRET_KEY = "hLr0OGWLazeAJQqIZ4TP+X+7hAZPz2DsviQ9gjVzzUGc+1+zyu3xvWUp"

# Configure the Redis cache
CACHE_CONFIG = {
    'CACHE_TYPE': 'RedisCache',
    'CACHE_DEFAULT_TIMEOUT': 300,
    'CACHE_KEY_PREFIX': 'superset_',
    'CACHE_REDIS_HOST': 'redis',
    'CACHE_REDIS_PORT': 6379,
    'CACHE_REDIS_DB': 1,
    'CACHE_REDIS_URL': 'redis://redis:6379/1'
}