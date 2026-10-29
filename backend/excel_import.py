from __future__ import annotations

"""讀取 Excel 並寫入資料庫（預設只追加、不覆蓋舊資料）。"""
import json
from pathlib import Path
from typing import Optional

import openpyxl
from sqlalchemy.orm import Session

from .models import Dish


# 清理「組成」欄位：遇到「以下數值…」這類說明就截斷，只留真正食材文字
def _clean_composition(value) -> Optional[str]:
    if value is None:
        return None
    lines = []
    for line in str(value).splitlines():
        if line.strip().startswith("以下數值"):
            break
        lines.append(line)
    result = "\n".join(lines).strip()
    return result or None


# 讀取 Excel 每一列，轉成可寫入 Dish 的字典清單（還沒寫進資料庫）
def load_dishes_from_excel(xlsx_path: Path) -> list[dict]:
    # data_only=True：讀公式計算後的值，不是公式本身
    wb = openpyxl.load_workbook(xlsx_path, data_only=True)
    ws = wb.active  # 使用第一個工作表
    items: list[dict] = []

    # [1:] 跳過表頭列，從第二列開始讀資料
    for r in list(ws.iter_rows(values_only=True))[1:]:
        code, name = r[1], r[2]  # A.菜色編號、B.實際菜名
        # 沒有編號或菜名 → 略過空列／說明列
        if code is None or name is None:
            continue
        name_str = str(name).strip()
        if not name_str or name_str.startswith("以下數值"):
            continue

        # C 欄及旁欄可能有多張對應照片檔名
        photos = [str(x) for x in [r[6], r[7], r[8]] if x]

        # 欄位索引對應台鋼餐飲 Excel 格式
        items.append(
            {
                "meal_type": r[0],                          # 餐別（早／午／晚）
                "dish_code": int(code),                     # 菜色編號
                "name": name_str,                           # 實際菜名
                "composition": _clean_composition(r[3]),    # 組成
                "cooking_method": r[4],                     # AI 烹調方式
                "oil_flavor_note": r[5],                    # AI 吸油調味估算
                "photo_files": json.dumps(photos, ensure_ascii=False),  # 照片檔名（存成 JSON 字串）
                "tfda_name": r[9],                          # TFDA 替代品名稱
                "calories_100g": str(r[10]) if r[10] is not None else None,   # 熱量
                "protein_100g": str(r[11]) if r[11] is not None else None,    # 粗蛋白
                "fat_100g": str(r[12]) if r[12] is not None else None,        # 粗脂肪
                "carb_100g": str(r[13]) if r[13] is not None else None,       # 總碳水
                "sodium_mg": str(r[14]) if r[14] is not None else None,       # 鈉
                "potassium_mg": str(r[15]) if r[15] is not None else None,    # 鉀
                "calcium_mg": str(r[16]) if r[16] is not None else None,      # 鈣
                "magnesium_mg": str(r[17]) if r[17] is not None else None,    # 鎂
                "fluffiness": r[18],                        # AI 食物蓬鬆度分類
                "weight_staple_g": float(r[19]) if r[19] is not None else None,  # 主食重
                "weight_main1_g": float(r[20]) if r[20] is not None else None,   # 主菜1重
                "weight_main2_g": float(r[21]) if r[21] is not None else None,   # 主菜2重
                "weight_side1_g": float(r[22]) if r[22] is not None else None,   # 副菜1重
                "weight_side2_g": float(r[23]) if r[23] is not None else None,   # 副菜2重
                "weight_side3_g": float(r[24]) if r[24] is not None else None,   # 副菜3重
                "weight_side4_g": float(r[25]) if r[25] is not None else None,   # 副菜4重
                "plate_photo": str(r[26]) if r[26] else None,  # 完整一盤菜照片
                "notes": r[27],                             # 備註
            }
        )
    return items


# 把 Excel 內容寫入資料庫；預設只新增，不覆蓋已存在的菜
def import_excel_to_db(
    db: Session, xlsx_path: Path, replace: bool = False
) -> dict[str, int]:
    items = load_dishes_from_excel(xlsx_path)

    # replace=True：先清空整張表再匯入（只給 scripts/seed_excel.py 初始化用）
    # 管理頁一般匯入請用 replace=False，避免舊資料被刪掉
    if replace:
        db.query(Dish).delete()
        db.commit()

    # 先抓出現有菜色，用來判斷「重複」
    existing = db.query(Dish).all()
    existing_codes = {d.dish_code for d in existing if d.dish_code is not None}
    existing_names = {d.name for d in existing if d.name}

    imported = 0  # 成功新增筆數
    skipped = 0   # 因重複而略過筆數
    for item in items:
        code, name = item.get("dish_code"), item.get("name")
        # 菜色編號或菜名任一已存在 → 略過，保留資料庫舊資料
        if (code is not None and code in existing_codes) or (name in existing_names):
            skipped += 1
            continue

        db.add(Dish(**item))
        # 同一份 Excel 內也可能重複，寫入後立刻記進集合避免重加
        if code is not None:
            existing_codes.add(code)
        if name:
            existing_names.add(name)
        imported += 1

    db.commit()  # 一次提交所有新增
    return {
        "imported": imported,          # 新增幾筆
        "skipped": skipped,            # 略過幾筆
        "total_in_file": len(items),   # Excel 裡有效列總數
    }
