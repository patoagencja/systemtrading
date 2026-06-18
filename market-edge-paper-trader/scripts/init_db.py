"""Initialize the SQLite database and create all tables."""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.config import DB_PATH, DATA_DIR
from app.database import init_db

if __name__ == "__main__":
    DATA_DIR.mkdir(exist_ok=True)
    print(f"Database path: {DB_PATH}")
    init_db()
    print("Done.")
