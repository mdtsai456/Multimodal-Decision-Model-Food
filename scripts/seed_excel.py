"""初次把 Excel 整批寫入資料庫（會清空舊資料，只給初始化用）。"""
from pathlib import Path  # 處理檔案路徑
import sys                # 讀命令列參數、調整模組搜尋路徑

# 專案根目錄（scripts 的上一層），讓後面可以 import backend.*
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.database import SessionLocal, Base, engine  # DB 連線與建表
from backend.excel_import import import_excel_to_db      # Excel → SQLite

# 未傳參數時的預設 Excel 路徑（可依本機檔案位置修改）
DEFAULT_XLSX = Path(r"C:\Users\姜語綺\Downloads\台鋼餐飲資料20260708.xlsx")


def main():
    # 有傳路徑就用參數，否則用預設檔
    xlsx = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_XLSX
    if not xlsx.exists():
        raise SystemExit(f"找不到檔案：{xlsx}")

    # 確保 dishes 資料表存在
    Base.metadata.create_all(bind=engine)

    db = SessionLocal()
    try:
        # replace=True：先清空再匯入（與管理頁「只追加」不同）
        result = import_excel_to_db(db, xlsx, replace=True)
        print(f"已匯入 {result['imported']} 筆菜色")
    finally:
        db.close()  # 無論成功失敗都關連線


if __name__ == "__main__":
    main()
