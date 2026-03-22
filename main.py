import uvicorn
import os
from pathlib import Path
from dotenv import load_dotenv
from api.app import create_app
from db.schema import init_db

load_dotenv()

DATABASE_URL = os.environ.get("DATABASE_URL", "")
WORKSPACE    = Path(os.environ.get("WORKSPACE",   "/home/ec2-user/.openclaw/workspace"))
MEMORY_DIR   = Path(os.environ.get("MEMORY_DIR",  "/home/ec2-user/.openclaw/workspace/memory"))
PORT         = int(os.environ.get("PORT", 8001))

# Fall back to SQLite for local dev when DATABASE_URL is not set
if not DATABASE_URL:
    _sqlite_path = Path(os.environ.get("TOKEN_FLOW_DB", "/home/ec2-user/.openclaw/data/token_flow.db"))
    _sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    DATABASE_URL = f"sqlite:///{_sqlite_path}"

# Ensure memory dir exists
MEMORY_DIR.mkdir(parents=True, exist_ok=True)


def _init_conn(url: str):
    if url.startswith("sqlite"):
        import sqlite3
        path = url.replace("sqlite:///", "")
        return sqlite3.connect(path)
    else:
        from db.pg_compat import connect as pg_connect
        return pg_connect(url)


if __name__ == "__main__":
    # Init DB schema on startup
    conn = _init_conn(DATABASE_URL)
    init_db(conn)
    conn.close()

    db_label = "SQLite (local)" if DATABASE_URL.startswith("sqlite") else "PostgreSQL"
    print(f"🔧 token-flow service starting on http://localhost:{PORT}")
    print(f"   Workspace : {WORKSPACE}")
    print(f"   Memory dir: {MEMORY_DIR}")
    print(f"   DB        : {db_label}")

    app = create_app(database_url=DATABASE_URL)
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")
