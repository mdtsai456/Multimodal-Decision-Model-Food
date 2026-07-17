"""辨識結果存檔：日期時間資料夾內放原圖、標註圖、JSON。"""
from __future__ import annotations

import json
import re
import shutil
from collections import Counter
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

# 資料夾命名：YYYYMMDD_HHMMSS 或 YYYYMMDD_HHMMSS_N
SESSION_DIR_RE = re.compile(r"^(\d{8})_(\d{6})(?:_(\d+))?$")


def parse_session_name(name: str) -> tuple[str, str] | None:
    """回傳 (YYYYMMDD, HHMMSS)；不合命名規則則 None。"""
    m = SESSION_DIR_RE.match(name)
    if not m:
        return None
    return m.group(1), m.group(2)


def _read_result_json(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def list_records_for_date(upload_root: Path, date_ymd: str) -> list[dict]:
    """
    列出指定日期（YYYY-MM-DD 或 YYYYMMDD）的辨識紀錄。
    依資料夾名稱前綴 YYYYMMDD 篩選。
    """
    compact = date_ymd.replace("-", "")
    if not re.fullmatch(r"\d{8}", compact):
        return []

    records: list[dict] = []
    if not upload_root.is_dir():
        return records

    for path in sorted(upload_root.iterdir(), reverse=True):
        if not path.is_dir():
            continue
        parsed = parse_session_name(path.name)
        if not parsed or parsed[0] != compact:
            continue

        date_part, time_part = parsed
        payload = _read_result_json(path / "result.json") or {}
        dishes = payload.get("dishes") if isinstance(payload.get("dishes"), list) else []
        has_dual = (path / "result1").is_dir() and (path / "result2").is_dir()
        names = [
            str(d.get("dish_name") or "")
            for d in dishes
            if isinstance(d, dict) and d.get("dish_name")
        ]
        hh, mm, ss = time_part[0:2], time_part[2:4], time_part[4:6]
        records.append(
            {
                "id": path.name,
                "date": f"{date_part[0:4]}-{date_part[4:6]}-{date_part[6:8]}",
                "time": f"{hh}:{mm}:{ss}",
                "meal_type": str(payload.get("meal_type") or ""),
                "dish_count": len(dishes),
                "dish_names": names,
                "has_dual": has_dual,
                "boxed_url": f"/uploads/{path.name}/result_boxed.jpg",
            }
        )
    return records


def load_record_detail(upload_root: Path, session_id: str) -> dict | None:
    """讀取單筆辨識紀錄詳情；找不到或路徑不合法回傳 None。"""
    if not SESSION_DIR_RE.match(session_id):
        return None
    session_dir = (upload_root / session_id).resolve()
    try:
        session_dir.relative_to(upload_root.resolve())
    except ValueError:
        return None
    if not session_dir.is_dir():
        return None

    parsed = parse_session_name(session_id)
    if not parsed:
        return None
    date_part, time_part = parsed
    hh, mm, ss = time_part[0:2], time_part[2:4], time_part[4:6]

    has_dual = (session_dir / "result1").is_dir() and (session_dir / "result2").is_dir()
    primary = _read_result_json(session_dir / "result.json") or {}
    primary_dishes = (
        primary.get("dishes") if isinstance(primary.get("dishes"), list) else []
    )

    dishes_out: list[dict] = []
    if has_dual:
        r1 = _read_result_json(session_dir / "result1" / "result.json") or {}
        r2 = _read_result_json(session_dir / "result2" / "result.json") or {}
        d1 = r1.get("dishes") if isinstance(r1.get("dishes"), list) else []
        d2 = r2.get("dishes") if isinstance(r2.get("dishes"), list) else []
        n = max(len(d1), len(d2), len(primary_dishes))
        for i in range(n):
            left = d1[i] if i < len(d1) and isinstance(d1[i], dict) else (
                primary_dishes[i] if i < len(primary_dishes) and isinstance(primary_dishes[i], dict) else {}
            )
            right = d2[i] if i < len(d2) and isinstance(d2[i], dict) else None
            item = {
                "dish_id": str(left.get("dish_id") or ""),
                "dish_name": str(left.get("dish_name") or ""),
                "confidence": float(left.get("confidence") or 0),
                "alternative": None,
            }
            if right and right.get("dish_name"):
                # 僅當次候選菜名與主候選不同才當雙結果顯示
                if str(right.get("dish_name")) != item["dish_name"]:
                    item["alternative"] = {
                        "dish_id": str(right.get("dish_id") or ""),
                        "dish_name": str(right.get("dish_name") or ""),
                        "confidence": float(right.get("confidence") or 0),
                    }
            dishes_out.append(item)
    else:
        for d in primary_dishes:
            if not isinstance(d, dict):
                continue
            dishes_out.append(
                {
                    "dish_id": str(d.get("dish_id") or ""),
                    "dish_name": str(d.get("dish_name") or ""),
                    "confidence": float(d.get("confidence") or 0),
                    "alternative": None,
                }
            )

    return {
        "id": session_id,
        "date": f"{date_part[0:4]}-{date_part[4:6]}-{date_part[6:8]}",
        "time": f"{hh}:{mm}:{ss}",
        "meal_type": str(primary.get("meal_type") or ""),
        "has_dual": has_dual,
        "boxed_url": f"/uploads/{session_id}/result_boxed.jpg",
        "boxed1_url": f"/uploads/{session_id}/result1/result_boxed.jpg" if has_dual else None,
        "boxed2_url": f"/uploads/{session_id}/result2/result_boxed.jpg" if has_dual else None,
        "dishes": dishes_out,
    }


def colors_for_count(n: int) -> list[tuple[int, int, int]]:
    """依菜色數量在色環均分色相，同一次辨識每道菜顏色都不同。"""
    total = max(n, 1)
    colors = []
    for i in range(total):
        hue = (i * 360 / total) % 360
        colors.append(_hsl_to_rgb(hue, 0.58, 0.40))
    return colors


def _hsl_to_rgb(h: float, s: float, l: float) -> tuple[int, int, int]:
    # h: 0~360, s/l: 0~1 → RGB 0~255
    c = (1 - abs(2 * l - 1)) * s
    x = c * (1 - abs((h / 60) % 2 - 1))
    m = l - c / 2
    if h < 60:
        r, g, b = c, x, 0
    elif h < 120:
        r, g, b = x, c, 0
    elif h < 180:
        r, g, b = 0, c, x
    elif h < 240:
        r, g, b = 0, x, c
    elif h < 300:
        r, g, b = x, 0, c
    else:
        r, g, b = c, 0, x
    return (
        int(round((r + m) * 255)),
        int(round((g + m) * 255)),
        int(round((b + m) * 255)),
    )


def make_session_dir(upload_root: Path) -> Path:
    # 資料夾名稱：辨識當下的日期+時間，例如 20260716_125930
    base = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = upload_root / base
    n = 1
    while path.exists():
        path = upload_root / f"{base}_{n}"
        n += 1
    path.mkdir(parents=True, exist_ok=False)
    return path


def _load_font(image_width: int) -> ImageFont.ImageFont:
    size = max(14, image_width // 40)
    # Windows 常見中文字型
    for name in ("msjh.ttc", "msyh.ttc", "mingliu.ttc", "arialuni.ttf"):
        candidate = Path(r"C:\Windows\Fonts") / name
        if candidate.exists():
            try:
                return ImageFont.truetype(str(candidate), size=size)
            except OSError:
                continue
    return ImageFont.load_default()


def draw_boxed_image(
    source_path: Path,
    predictions: list[dict],
    out_path: Path,
) -> None:
    # 在原圖上畫出每道菜的大概外框與菜名（與辨識時相同：先依 EXIF 轉正）
    img = ImageOps.exif_transpose(Image.open(source_path)).convert("RGB")
    draw = ImageDraw.Draw(img)
    w, h = img.size
    font = _load_font(w)
    line_w = max(2, w // 220)
    colors = colors_for_count(len(predictions))

    for index, item in enumerate(predictions):
        box = item.get("bbox")
        if not isinstance(box, (list, tuple)) or len(box) != 4:
            continue
        x, y, bw, bh = [float(v) for v in box]
        left, top = x * w, y * h
        right, bottom = left + bw * w, top + bh * h
        color = colors[index]

        draw.rectangle([left, top, right, bottom], outline=color, width=line_w)

        label = str(item.get("name") or "")
        if not label:
            continue
        # 標籤底色
        text_bbox = draw.textbbox((0, 0), label, font=font)
        tw = text_bbox[2] - text_bbox[0]
        th = text_bbox[3] - text_bbox[1]
        pad = 6
        ly = max(0, top - th - pad * 2 - 2)
        draw.rectangle(
            [left, ly, left + tw + pad * 2, ly + th + pad * 2],
            fill=color,
        )
        draw.text((left + pad, ly + pad), label, fill=(255, 255, 255), font=font)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    # 一律存 jpg，方便瀏覽
    img.save(out_path, format="JPEG", quality=90)


def build_result_json(predictions: list[dict], dish_by_id: dict) -> dict:
    # 組出要存檔的 JSON（meal_type + dishes）
    meal_types = []
    dishes_out = []
    for item in predictions:
        dish = dish_by_id.get(item.get("dish_id"))
        if dish and dish.meal_type:
            meal_types.append(dish.meal_type)

        # JSON 的 dish_id 用菜色編號（沒有則用資料庫 id），字串格式
        code = item.get("dish_code")
        raw_id = code if code is not None else item.get("dish_id")
        dishes_out.append(
            {
                "dish_id": "" if raw_id is None else str(raw_id),
                "dish_name": item.get("name") or "",
                "confidence": round(float(item.get("confidence") or 0), 2),
            }
        )

    meal_type = ""
    if meal_types:
        meal_type = Counter(meal_types).most_common(1)[0][0]

    return {"meal_type": meal_type, "dishes": dishes_out}


def _strip_alternatives(predictions: list[dict]) -> list[dict]:
    """主解釋：只保留每筆的主候選（不含 alternative 欄位）。"""
    out = []
    for item in predictions:
        out.append(
            {
                "name": item.get("name"),
                "dish_id": item.get("dish_id"),
                "dish_code": item.get("dish_code"),
                "confidence": item.get("confidence"),
                "reason": item.get("reason"),
                "bbox": item.get("bbox"),
            }
        )
    return out


def _apply_alternatives(predictions: list[dict]) -> list[dict]:
    """次解釋：有 alternative 的位置改用次佳候選。"""
    out = []
    for item in predictions:
        alt = item.get("alternative")
        if alt:
            out.append(
                {
                    "name": alt.get("name"),
                    "dish_id": alt.get("dish_id"),
                    "dish_code": alt.get("dish_code"),
                    "confidence": alt.get("confidence"),
                    "reason": alt.get("reason"),
                    "bbox": item.get("bbox"),
                }
            )
        else:
            out.append(
                {
                    "name": item.get("name"),
                    "dish_id": item.get("dish_id"),
                    "dish_code": item.get("dish_code"),
                    "confidence": item.get("confidence"),
                    "reason": item.get("reason"),
                    "bbox": item.get("bbox"),
                }
            )
    return out


def _write_bundle_files(
    dest_dir: Path,
    original_path: Path,
    original_name: str,
    predictions: list[dict],
    dish_by_id: dict,
) -> None:
    dest_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(original_path, dest_dir / original_name)
    draw_boxed_image(original_path, predictions, dest_dir / "result_boxed.jpg")
    payload = build_result_json(predictions, dish_by_id)
    (dest_dir / "result.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def save_recognition_bundle(
    upload_root: Path,
    source_path: Path,
    predictions: list[dict],
    dish_by_id: dict,
) -> Path:
    """
    在 uploads 下建立「日期_時間」資料夾，寫入：
    1. original.*  上傳原圖
    2. result_boxed.jpg  有外框的結果圖
    3. result.json  辨識結果

    若有信心相近的雙候選，另建 result1/、result2/，
    各存一份原圖、標註圖、JSON（result1=主候選，result2=次佳候選）。
    """
    session_dir = make_session_dir(upload_root)

    suffix = source_path.suffix.lower() or ".jpg"
    original_name = f"original{suffix}"
    original_path = session_dir / original_name
    # 原圖移入資料夾（不再留在 uploads 根目錄）
    source_path.replace(original_path)

    primary_preds = _strip_alternatives(predictions)
    has_close = any(item.get("alternative") for item in predictions)

    if has_close:
        alt_preds = _apply_alternatives(predictions)
        _write_bundle_files(
            session_dir / "result1",
            original_path,
            original_name,
            primary_preds,
            dish_by_id,
        )
        _write_bundle_files(
            session_dir / "result2",
            original_path,
            original_name,
            alt_preds,
            dish_by_id,
        )
        # 根目錄仍寫主解釋，方便直接瀏覽
        boxed_path = session_dir / "result_boxed.jpg"
        draw_boxed_image(original_path, primary_preds, boxed_path)
        payload = build_result_json(primary_preds, dish_by_id)
        (session_dir / "result.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    else:
        boxed_path = session_dir / "result_boxed.jpg"
        draw_boxed_image(original_path, primary_preds, boxed_path)
        payload = build_result_json(primary_preds, dish_by_id)
        (session_dir / "result.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    return session_dir
