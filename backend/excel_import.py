"""讀取 Excel 並寫入資料庫。預設只新增。不覆寫既有菜色。"""
import json
from pathlib import Path

import openpyxl
from sqlalchemy.orm import Session

from .models import Dish


def _clean_composition(value) -> str | None:
    """組成文字遇到「以下數值」時，捨棄該行與其後的內容。"""
    if value is None:
        return None
    lines = []
    for line in str(value).splitlines():
        if line.strip().startswith("以下數值"):
            break
        lines.append(line)
    result = "\n".join(lines).strip()
    return result or None


def _text(value):
    if value is None:
        return None
    return str(value)


def _number(value):
    if value is None:
        return None
    return float(value)


def load_dishes_from_excel(xlsx_path: Path) -> list[dict]:
    """讀取 Excel 資料列。不寫入資料庫。"""
    # data_only=True：讀取公式計算後的值。
    wb = openpyxl.load_workbook(xlsx_path, data_only=True)
    ws = wb.active
    items: list[dict] = []

    # 第 1 列是表頭。從第 2 列開始讀取。
    for r in ws.iter_rows(min_row=2, values_only=True):
        code, name = r[1], r[2]
        if code is None or name is None:
            continue
        name_str = str(name).strip()
        if not name_str or name_str.startswith("以下數值"):
            continue

        photos = [str(x) for x in [r[6], r[7], r[8]] if x]

        # 索引對應台鋼餐飲 Excel 的欄序。
        items.append(
            {
                "meal_type": r[0],
                "dish_code": int(code),
                "name": name_str,
                "composition": _clean_composition(r[3]),
                "cooking_method": r[4],
                "oil_flavor_note": r[5],
                "photo_files": json.dumps(photos, ensure_ascii=False),
                "tfda_name": r[9],
                "calories_100g": _text(r[10]),
                "protein_100g": _text(r[11]),
                "fat_100g": _text(r[12]),
                "carb_100g": _text(r[13]),
                "sodium_mg": _text(r[14]),
                "potassium_mg": _text(r[15]),
                "calcium_mg": _text(r[16]),
                "magnesium_mg": _text(r[17]),
                "fluffiness": r[18],
                "weight_staple_g": _number(r[19]),
                "weight_main1_g": _number(r[20]),
                "weight_main2_g": _number(r[21]),
                "weight_side1_g": _number(r[22]),
                "weight_side2_g": _number(r[23]),
                "weight_side3_g": _number(r[24]),
                "weight_side4_g": _number(r[25]),
                "plate_photo": str(r[26]) if r[26] else None,
                "notes": r[27],
            }
        )
    return items


def import_excel_to_db(
    db: Session, xlsx_path: Path, replace: bool = False
) -> dict[str, int]:
    """寫入 Excel 的菜色。replace 為 false 時，已存在的菜色編號或菜名略過。"""
    items = load_dishes_from_excel(xlsx_path)

    # replace=True 時先刪除全部菜色。此模式只給 scripts/seed_excel.py 使用。
    # 管理頁匯入使用 replace=False。
    if replace:
        db.query(Dish).delete()
        db.commit()

    existing = db.query(Dish).all()
    existing_codes = {d.dish_code for d in existing if d.dish_code is not None}
    existing_names = {d.name for d in existing if d.name}

    imported = 0
    skipped = 0
    for item in items:
        code, name = item.get("dish_code"), item.get("name")
        if (code is not None and code in existing_codes) or (name in existing_names):
            skipped += 1
            continue

        db.add(Dish(**item))
        # 同一份 Excel 的後續列也要略過相同編號或菜名。
        if code is not None:
            existing_codes.add(code)
        if name:
            existing_names.add(name)
        imported += 1

    db.commit()
    return {
        "imported": imported,
        "skipped": skipped,
        "total_in_file": len(items),
    }
