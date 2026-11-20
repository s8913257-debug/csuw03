import hashlib
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from typing import Iterator

from app.config import DB_PATH


def hash_password(password: str, salt: str | None = None) -> str:
    """使用 PBKDF2-HMAC-SHA256 雜湊密碼，回傳 'salt$hash' 格式字串。"""
    if salt is None:
        salt = os.urandom(16).hex()
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 120_000)
    return f"{salt}${dk.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    """驗證明文密碼是否與儲存的雜湊相符。"""
    try:
        salt, _ = stored_hash.split("$", 1)
    except ValueError:
        return False
    return hash_password(password, salt) == stored_hash


@contextmanager
def get_db() -> Iterator[sqlite3.Connection]:
    """取得 SQLite 資料庫連線，啟用 Row factory 並確保結束時關閉。"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """初始化資料表，若無任何帳號則建立預設管理員帳號 (admin / admin1234)。"""
    with get_db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                display_name TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'user' CHECK (role IN ('admin', 'user')),
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS history_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tool_id TEXT NOT NULL,
                tool_name TEXT NOT NULL,
                category TEXT NOT NULL,
                batch_or_filename TEXT NOT NULL,
                note TEXT NOT NULL DEFAULT '',
                summary TEXT NOT NULL DEFAULT '',
                columns_json TEXT NOT NULL,
                rows_json TEXT NOT NULL,
                row_count INTEGER NOT NULL DEFAULT 0,
                user_id INTEGER NOT NULL,
                username TEXT NOT NULL,
                user_display_name TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id)
            );

            CREATE INDEX IF NOT EXISTS idx_history_created_at ON history_records(created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_history_category ON history_records(category);
            CREATE INDEX IF NOT EXISTS idx_history_tool_id ON history_records(tool_id);
            """
        )

        existing = conn.execute("SELECT COUNT(*) AS cnt FROM users").fetchone()["cnt"]
        if existing == 0:
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            conn.execute(
                """
                INSERT INTO users (username, display_name, password_hash, role, is_active, created_at)
                VALUES (?, ?, ?, 'admin', 1, ?)
                """,
                ("admin", "系統管理員", hash_password("admin1234"), now),
            )
