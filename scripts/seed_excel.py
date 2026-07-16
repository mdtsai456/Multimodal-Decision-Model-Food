"""初次把 Excel 整批寫入資料庫（會清空舊資料，只給初始化用）。"""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.database import SessionLocal, Base, engine
from backend.excel_import import import_excel_to_db

DEFAULT_XLSX = Path(r"C:\Users\姜語綺\Downloads\台鋼餐飲資料20260708.xlsx")


def main():
    xlsx = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_XLSX
    if not xlsx.exists():
        raise SystemExit(f"找不到檔案：{xlsx}")
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        result = import_excel_to_db(db, xlsx, replace=True)
        print(f"已匯入 {result['imported']} 筆菜色")
    finally:
        db.close()


if __name__ == "__main__":
    main()
