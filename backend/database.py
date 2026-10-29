"""資料庫連線與路徑。"""
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

# 設定與資料存放在專案根目錄。
BASE_DIR = Path(__file__).resolve().parents[1]
# 前端檔案在 health_web。
BASE_FOR_FRONT_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")

DATA_DIR = BASE_DIR / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
DB_PATH = DATA_DIR / "menu.db"

DATA_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

print("BASE_DIR =", BASE_DIR)
print("DATA_DIR =", DATA_DIR)
print("UPLOAD_DIR =", UPLOAD_DIR)
print("BASE_FOR_FRONT_DIR =", BASE_FOR_FRONT_DIR)

engine = create_engine(
    f"sqlite:///{DB_PATH}",
    # FastAPI 可在不同執行緒處理請求。SQLite 必須允許同一連線跨執行緒使用。
    connect_args={"check_same_thread": False},
)

# autocommit=False：呼叫 commit 後才寫入。
# autoflush=False：查詢前不寫入未提交的變更。
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    """資料表類別繼承此類別。"""

    pass


def get_db():
    """每個請求開啟一個 Session。請求結束時關閉 Session。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
