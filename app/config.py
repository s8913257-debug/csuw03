import os
import secrets
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

DB_PATH = DATA_DIR / "lab_toolbox.db"
SECRET_KEY_PATH = BASE_DIR / ".secret_key"

APP_TITLE = "濫用藥物實驗室工具箱"

# 六大工具分類（依 SPEC.md 規範）
CATEGORIES = [
    "收樣",
    "初篩檢驗",
    "確認檢驗",
    "品管工具",
    "帳款",
    "藥品管理",
]


def get_secret_key() -> str:
    env_key = os.environ.get("LAB_TOOLBOX_SECRET")
    if env_key:
        return env_key
    if SECRET_KEY_PATH.exists():
        return SECRET_KEY_PATH.read_text(encoding="utf-8").strip()
    key = secrets.token_hex(32)
    SECRET_KEY_PATH.write_text(key, encoding="utf-8")
    return key
