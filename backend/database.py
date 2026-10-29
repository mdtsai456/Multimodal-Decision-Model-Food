"""資料庫連線與路徑設定。"""
"""
   db = SessionLocal()   # 開門
   # 查資料、改資料…
   db.close()            # 關門
"""
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

# 專案根目錄（backend 的上一層， /）
BASE_DIR = Path(__file__).resolve().parents[1]
BASE_FOR_FRONT_DIR = Path(__file__).resolve().parent.parent  # 前端 HTML/CSS/JS 目錄

# 載入根目錄的 .env（API Key、管理員密碼等）
load_dotenv(BASE_DIR / ".env")

# 資料存放位置
DATA_DIR = BASE_DIR / "data"          # 資料夾：資料庫、上傳檔
UPLOAD_DIR = DATA_DIR / "uploads"     # 使用者上傳的餐盤圖片
DB_PATH = DATA_DIR / "menu.db"        # SQLite 資料庫檔案路徑

# 若資料夾不存在就建立（exist_ok=True：已存在也不報錯）
DATA_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

print("BASE_DIR =", BASE_DIR)
print("DATA_DIR =", DATA_DIR)
print("UPLOAD_DIR =", UPLOAD_DIR)
print("BASE_FOR_FRONT_DIR =", BASE_FOR_FRONT_DIR)

# 建立資料庫引擎，連到 SQLite 檔案
engine = create_engine(
    f"sqlite:///{DB_PATH}",
    # SQLite 預設同一連線只能在同一個執行緒用；
    # FastAPI 可能在不同執行緒處理請求，所以要關掉這項限制
    connect_args={"check_same_thread": False},
)

# Session 工廠：之後每次要讀寫資料庫，就用它開一個 Session
# autocommit=False：要自己 commit 才真的寫入
# autoflush=False：查詢前不會自動把暫存變更灌進 DB
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


# 所有資料表 model（例如 Dish）都要繼承這個 Base
class Base(DeclarativeBase):
    pass


# 給 FastAPI 用的資料庫依賴：請求進來時開連線，結束後關掉
def get_db():
    db = SessionLocal()
    try:
        yield db  # 把 db 交給 API 函式使用
    finally:
        db.close()  # 無論成功或失敗都關閉連線，避免漏關
