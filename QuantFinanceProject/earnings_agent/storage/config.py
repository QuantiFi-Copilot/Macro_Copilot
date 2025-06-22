# earnings_agent/storage/config.py
import os

# This centralizes the schema name so other modules can import it
# without creating circular dependencies.
DB_SCHEMA = os.getenv("EARNINGS_DB_SCHEMA", "earnings_data")