"""將 Excel 寫入資料庫。此程式刪除既有菜色。只在初始化時執行。"""
from pathlib import Path
import sys

# 將專案根目錄加入匯入路徑，才能 import backend。
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.database import SessionLocal, Base, engine
from backend.excel_import import import_excel_to_db

# 命令列未提供路徑時使用此檔。
DEFAULT_XLSX = Path(r"C:\Users\姜語綺\Downloads\台鋼餐飲資料20260708.xlsx")


def main():
    xlsx = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_XLSX
    if not xlsx.exists():
        raise SystemExit(f"找不到檔案：{xlsx}")

    Base.metadata.create_all(bind=engine)

    db = SessionLocal()
    try:
        # replace=True：先刪除既有菜色，再寫入 Excel。
        result = import_excel_to_db(db, xlsx, replace=True)
        print(f"已匯入 {result['imported']} 筆菜色")
    finally:
        db.close()


if __name__ == "__main__":
    main()
