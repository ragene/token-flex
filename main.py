import uvicorn
import os
import sqlite3
from dotenv import load_dotenv
from api.app import create_app
from db.schema import init_db

load_dotenv()
DB_PATH = os.environ.get("TOKEN_FLOW_DB", "/home/ec2-user/.openclaw/data/token_flow.db")

if __name__ == "__main__":
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    init_db(conn)
    conn.close()
    app = create_app(db_path=DB_PATH)
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8001)), reload=False)
