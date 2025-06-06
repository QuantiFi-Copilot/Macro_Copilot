from sqlalchemy import create_engine, Column, String, TIMESTAMP, Date, Float  # ⬅️ Add this at the top if not already
from sqlalchemy.orm import declarative_base, sessionmaker
import datetime
import os
from dotenv import load_dotenv
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..","..", ".."))
load_dotenv(dotenv_path=os.path.join(ROOT_DIR, ".env"))

Base = declarative_base()

class MacroSeries(Base):
    __tablename__ = 'macro_series'
    timestamp = Column(TIMESTAMP, primary_key=True, default=datetime.datetime.utcnow)
    ticker = Column(String)
    value = Column(Float)
    source = Column(String)
    units = Column(String)
    frequency = Column(String)
    recorded_at = Column(Date)

# Use environment variable for DB connection
DB_URL = os.getenv("DATABASE_URL", "").strip()
engine = create_engine(DB_URL)
Session = sessionmaker(bind=engine)

def create_tables():
    Base.metadata.create_all(engine)