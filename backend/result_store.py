"""辨識結果存檔：日期時間資料夾內放原圖、標註圖、JSON。"""
from __future__ import annotations

import json
import shutil
from collections import Counter
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps


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
