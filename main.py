import uvicorn
import os
from pathlib import Path
from dotenv import load_dotenv
from api.app import create_app
from db.schema import init_db
import sqlite3

load_dotenv()

DB_PATH   = Path(os.environ.get("TOKEN_FLOW_DB", "/home/ec2-user/.openclaw/data/token_flow.db"))
WORKSPACE = Path(os.environ.get("WORKSPACE",    "/home/ec2-user/.openclaw/workspace"))
MEMORY_DIR = Path(os.environ.get("MEMORY_DIR",  "/home/ec2-user/.openclaw/workspace/memory"))
PORT      = int(os.environ.get("PORT", 8001))

if __name__ == "__main__":
    # Ensure directories exist (same pattern as smart-memory)
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    # Init DB schema
    conn = sqlite3.connect(DB_PATH)
    init_db(conn)
    conn.close()

    print(f"🔧 token-flow service starting on http://localhost:{PORT}")
    print(f"   Workspace : {WORKSPACE}")
    print(f"   Memory dir: {MEMORY_DIR}")
    print(f"   DB        : {DB_PATH}")

    app = create_app(db_path=str(DB_PATH))
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")
