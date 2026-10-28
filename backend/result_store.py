"""辨識結果存檔。每個資料夾包含原圖、標註圖與 JSON。"""
from __future__ import annotations

import json
import re
import shutil
from collections import Counter
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

from .bbox_adjust import BBOX_SHIFT_VERSION, denudge_top_bboxes

# 資料夾名稱是 YYYYMMDD_HHMMSS。同一秒重複時，名稱加上 _N。
SESSION_DIR_RE = re.compile(r"^(\d{8})_(\d{6})(?:_(\d+))?$")


def parse_session_name(name: str) -> tuple[str, str] | None:
    """名稱符合規則時回傳 (YYYYMMDD, HHMMSS)。否則回傳 None。"""
    m = SESSION_DIR_RE.match(name)
    if not m:
        return None
    return m.group(1), m.group(2)


def _read_result_json(path: Path) -> dict | None:
    """讀取 result.json。檔案不存在、無法讀取，或根節點不是物件時回傳 None。"""
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def list_records_for_date(upload_root: Path, date_ymd: str) -> list[dict]:
    """列出指定日期的辨識紀錄。日期可為 YYYY-MM-DD 或 YYYYMMDD。依資料夾名稱的前 8 碼篩選。"""
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

def _color_dist(a: tuple[int, int, int], b: tuple[int, int, int]) -> int:
    """RGB 距離。計算方式是三個通道差值的絕對值總和。"""
    return abs(a[0] - b[0]) + abs(a[1] - b[1]) + abs(a[2] - b[2])


def recover_bboxes_from_boxed(
    original_path: Path,
    boxed_path: Path,
    dish_count: int,
) -> list[list[float] | None]:
    """舊紀錄沒有 bbox 時，比對原圖與標註圖的色塊，還原相對座標。回傳長度等於 dish_count。失敗的位置是 None。"""
    if dish_count <= 0 or not original_path.is_file() or not boxed_path.is_file():
        return []

    orig = ImageOps.exif_transpose(Image.open(original_path)).convert("RGB")
    boxed = Image.open(boxed_path).convert("RGB")
    if boxed.size != orig.size:
        boxed = boxed.resize(orig.size)
    w, h = orig.size
    op = orig.load()
    bp = boxed.load()
    colors = colors_for_count(dish_count)
    boxes: list[list[float] | None] = []

    for color in colors:
        xs: list[int] = []
        ys: list[int] = []
        for y in range(0, h, 2):
            for x in range(0, w, 2):
                bc = bp[x, y]
                oc = op[x, y]
                if _color_dist(bc, color) <= 40 and _color_dist(bc, oc) > 30:
                    xs.append(x)
                    ys.append(y)
        if len(xs) < 8:
            boxes.append(None)
            continue
        x0, x1 = min(xs), max(xs)
        y0, y1 = min(ys), max(ys)
        # 標籤在框上方。裁掉上方一條，避免把標籤算進框。
        trim = max(8, (y1 - y0) // 12)
        y0 = min(y0 + trim, y1 - 4)
        boxes.append(
            [
                round(x0 / w, 4),
                round(y0 / h, 4),
                round((x1 - x0) / w, 4),
                round((y1 - y0) / h, 4),
            ]
        )
    return boxes

def _ensure_dish_bboxes(session_dir: Path, dishes: list[dict]) -> list[dict]:
    """菜色缺少 bbox 時，從標註圖還原，並寫回 result.json。"""
    if not dishes:
        return dishes
    if all(isinstance(d.get("bbox"), list) and len(d["bbox"]) == 4 for d in dishes):
        return dishes

    boxed = session_dir / "result_boxed.jpg"
    original = next(iter(sorted(session_dir.glob("original.*"))), None)
    if (session_dir / "result1" / "result_boxed.jpg").is_file():
        boxed = session_dir / "result1" / "result_boxed.jpg"
        original = next(
            iter(sorted((session_dir / "result1").glob("original.*"))),
            original,
        )
    if original is None or not boxed.is_file():
        return dishes

    try:
        recovered = recover_bboxes_from_boxed(original, boxed, len(dishes))
    except OSError:
        return dishes

    changed = False
    for dish, bbox in zip(dishes, recovered):
        if bbox and not (
            isinstance(dish.get("bbox"), list) and len(dish.get("bbox")) == 4
        ):
            dish["bbox"] = bbox
            changed = True

    if changed:
        result_path = session_dir / "result.json"
        payload = _read_result_json(result_path) or {}
        payload_dishes = (
            payload.get("dishes") if isinstance(payload.get("dishes"), list) else []
        )
        for i, d in enumerate(payload_dishes):
            if i < len(dishes) and dishes[i].get("bbox"):
                if isinstance(d, dict):
                    d["bbox"] = dishes[i]["bbox"]
        payload["dishes"] = payload_dishes or [
            {
                "dish_id": d.get("dish_id", ""),
                "dish_name": d.get("dish_name", ""),
                "confidence": d.get("confidence", 0),
                "bbox": d.get("bbox"),
            }
            for d in dishes
        ]
        try:
            result_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError:
            pass

    return dishes

def _normalize_stored_dish(d: dict) -> dict:
    """將 result.json 的一道菜轉成 API 詳情。"""
    bbox = d.get("bbox")
    if not isinstance(bbox, list) or len(bbox) != 4:
        bbox = None
    else:
        try:
            bbox = [float(v) for v in bbox]
        except (TypeError, ValueError):
            bbox = None

    alt_raw = d.get("alternative")
    alternative = None
    if isinstance(alt_raw, dict) and alt_raw.get("dish_name"):
        alternative = {
            "dish_id": str(alt_raw.get("dish_id") or ""),
            "dish_name": str(alt_raw.get("dish_name") or ""),
            "confidence": float(alt_raw.get("confidence") or 0),
            "reason": alt_raw.get("reason") or None,
        }

    return {
        "dish_id": str(d.get("dish_id") or ""),
        "dish_name": str(d.get("dish_name") or ""),
        "confidence": float(d.get("confidence") or 0),
        "reason": d.get("reason") or None,
        "bbox": bbox,
        "alternative": alternative,
    }


def load_record_detail(upload_root: Path, session_id: str) -> dict | None:
    """讀取一筆辨識紀錄。找不到或路徑不合法時回傳 None。"""
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
    r1 = _read_result_json(session_dir / "result1" / "result.json") or {}
    r2 = _read_result_json(session_dir / "result2" / "result.json") or {}
    d1 = r1.get("dishes") if isinstance(r1.get("dishes"), list) else []
    d2 = r2.get("dishes") if isinstance(r2.get("dishes"), list) else []

    if primary_dishes:
        for i, d in enumerate(primary_dishes):
            if not isinstance(d, dict):
                continue
            item = _normalize_stored_dish(d)
            if item["bbox"] is None and i < len(d1) and isinstance(d1[i], dict):
                item["bbox"] = _normalize_stored_dish(d1[i])["bbox"]
            if item["alternative"] is None and i < len(d2) and isinstance(d2[i], dict):
                right = d2[i]
                if right.get("dish_name") and str(right.get("dish_name")) != item["dish_name"]:
                    item["alternative"] = {
                        "dish_id": str(right.get("dish_id") or ""),
                        "dish_name": str(right.get("dish_name") or ""),
                        "confidence": float(right.get("confidence") or 0),
                        "reason": right.get("reason") or None,
                    }
            dishes_out.append(item)
    elif has_dual:
        # 根目錄沒有 dishes 時，由 result1 與 result2 組成。
        n = max(len(d1), len(d2))
        for i in range(n):
            left_src = d1[i] if i < len(d1) and isinstance(d1[i], dict) else {}
            item = _normalize_stored_dish(left_src)
            right = d2[i] if i < len(d2) and isinstance(d2[i], dict) else None
            if right and right.get("dish_name"):
                if str(right.get("dish_name")) != item["dish_name"]:
                    item["alternative"] = {
                        "dish_id": str(right.get("dish_id") or ""),
                        "dish_name": str(right.get("dish_name") or ""),
                        "confidence": float(right.get("confidence") or 0),
                        "reason": right.get("reason") or None,
                    }
            dishes_out.append(item)

    dishes_out = _ensure_dish_bboxes(session_dir, dishes_out)

    # 版本小於 BBOX_SHIFT_VERSION 時，還原上排位移並重畫標註圖。每個資料夾只做一次。
    stored_ver = int(primary.get("bbox_shift_version") or 1)
    if stored_ver < BBOX_SHIFT_VERSION:
        meal_type = str(primary.get("meal_type") or "")
        if denudge_top_bboxes(dishes_out):
            _persist_denudged_bboxes(
                session_dir,
                dishes_out,
                has_dual=has_dual,
                meal_type=meal_type,
            )
        else:
            primary["bbox_shift_version"] = BBOX_SHIFT_VERSION
            try:
                (session_dir / "result.json").write_text(
                    json.dumps(primary, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            except OSError:
                pass

    original = next(iter(sorted(session_dir.glob("original.*"))), None)
    original_url = (
        f"/uploads/{session_id}/{original.name}" if original and original.is_file() else None
    )

    return {
        "id": session_id,
        "date": f"{date_part[0:4]}-{date_part[4:6]}-{date_part[6:8]}",
        "time": f"{hh}:{mm}:{ss}",
        "meal_type": str(primary.get("meal_type") or ""),
        "has_dual": has_dual,
        "original_url": original_url,
        "boxed_url": f"/uploads/{session_id}/result_boxed.jpg",
        "boxed1_url": f"/uploads/{session_id}/result1/result_boxed.jpg" if has_dual else None,
        "boxed2_url": f"/uploads/{session_id}/result2/result_boxed.jpg" if has_dual else None,
        "dishes": dishes_out,
    }


def colors_for_count(n: int) -> list[tuple[int, int, int]]:
    """依菜色數量均分色相。同一次辨識的每道菜使用不同顏色。"""
    total = max(n, 1)
    colors = []
    for i in range(total):
        hue = (i * 360 / total) % 360
        colors.append(_hsl_to_rgb(hue, 0.58, 0.40))
    return colors


def _hsl_to_rgb(h: float, s: float, l: float) -> tuple[int, int, int]:
    """將 HSL 轉成 RGB。RGB 的範圍是 0 到 255。"""
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
    """建立本次辨識的資料夾。名稱是 YYYYMMDD_HHMMSS。同一秒重複時加上 _N。"""
    base = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = upload_root / base
    n = 1
    while path.exists():
        path = upload_root / f"{base}_{n}"
        n += 1
    path.mkdir(parents=True, exist_ok=False)
    return path


def _load_font(image_width: int) -> ImageFont.ImageFont:
    """載入可顯示繁體中文的字型。先使用專案內的字型。"""
    size = max(28, image_width // 28)
    bundled = Path(__file__).resolve().parent / "assets" / "fonts" / "NotoSansTC-Regular.otf"
    candidates: list[tuple[Path, int]] = [
        (bundled, 0),
        (Path(r"C:\Windows\Fonts") / "msjh.ttc", 0),
        (Path(r"C:\Windows\Fonts") / "msyh.ttc", 0),
        (Path(r"C:\Windows\Fonts") / "mingliu.ttc", 0),
        (Path(r"C:\Windows\Fonts") / "kaiu.ttf", 0),
        (Path(r"C:\Windows\Fonts") / "arialuni.ttf", 0),
        (Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"), 0),
        (Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"), 0),
        (Path("/usr/share/fonts/opentype/noto/NotoSansCJKtc-Regular.otf"), 0),
        (Path("/usr/share/fonts/truetype/noto/NotoSansTC-Regular.otf"), 0),
    ]
    for candidate, index in candidates:
        if not candidate.exists():
            continue
        try:
            return ImageFont.truetype(str(candidate), size=size, index=index)
        except OSError:
            try:
                return ImageFont.truetype(str(candidate), size=size)
            except OSError:
                continue
    print("[warn] 找不到繁中字型，標註圖菜名可能變小或亂碼；請確認 backend/assets/fonts/NotoSansTC-Regular.otf")
    return ImageFont.load_default()


def draw_boxed_image(
    source_path: Path,
    predictions: list[dict],
    out_path: Path,
) -> None:
    """在原圖畫外框與菜名。存成 JPEG。"""
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

        label = str(item.get("name") or item.get("dish_name") or "")
        if not label:
            continue
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
    img.save(out_path, format="JPEG", quality=90)


def _dish_entry(item: dict) -> dict:
    """一道菜的存檔格式。dish_id 使用菜色編號。沒有菜色編號時使用資料庫 id。"""
    code = item.get("dish_code")
    raw_id = code if code is not None else item.get("dish_id")
    entry: dict = {
        "dish_id": "" if raw_id is None else str(raw_id),
        "dish_name": item.get("name") or "",
        "confidence": round(float(item.get("confidence") or 0), 2),
        "reason": item.get("reason") or None,
        "bbox": item.get("bbox"),
    }
    alt = item.get("alternative")
    if isinstance(alt, dict) and alt.get("name"):
        alt_code = alt.get("dish_code")
        alt_raw = alt_code if alt_code is not None else alt.get("dish_id")
        entry["alternative"] = {
            "dish_id": "" if alt_raw is None else str(alt_raw),
            "dish_name": alt.get("name") or "",
            "confidence": round(float(alt.get("confidence") or 0), 2),
            "reason": alt.get("reason") or None,
        }
    return entry


def build_result_json(predictions: list[dict], dish_by_id: dict) -> dict:
    """建立 result.json 的內容。meal_type 使用出現次數最多的餐別。"""
    meal_types = []
    dishes_out = []
    for item in predictions:
        dish = dish_by_id.get(item.get("dish_id"))
        if dish and dish.meal_type:
            meal_types.append(dish.meal_type)
        dishes_out.append(_dish_entry(item))

    meal_type = ""
    if meal_types:
        meal_type = Counter(meal_types).most_common(1)[0][0]

    return {
        "meal_type": meal_type,
        "dishes": dishes_out,
        "bbox_shift_version": BBOX_SHIFT_VERSION,
    }


def _boxed_fields(src: dict, bbox) -> dict:
    return {
        "name": src.get("name"),
        "dish_id": src.get("dish_id"),
        "dish_code": src.get("dish_code"),
        "confidence": src.get("confidence"),
        "reason": src.get("reason"),
        "bbox": bbox,
    }


def _strip_alternatives(predictions: list[dict]) -> list[dict]:
    """只保留主候選。移除 alternative。"""
    return [_boxed_fields(item, item.get("bbox")) for item in predictions]


def _apply_alternatives(predictions: list[dict]) -> list[dict]:
    """有 alternative 時改用次佳菜名。bbox 仍使用主候選的框。"""
    out = []
    for item in predictions:
        alt = item.get("alternative")
        src = alt if alt else item
        out.append(_boxed_fields(src, item.get("bbox")))
    return out


def _write_result_json(path: Path, meal_type: str, dishes: list[dict]) -> None:
    """將 meal_type 與 dishes 寫入 JSON 檔。"""
    payload = {
        "meal_type": meal_type,
        "dishes": dishes,
        "bbox_shift_version": BBOX_SHIFT_VERSION,
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _persist_denudged_bboxes(
    session_dir: Path,
    dishes_out: list[dict],
    *,
    has_dual: bool,
    meal_type: str,
) -> None:
    """將還原後的上排 bbox 寫回 JSON，並重畫標註圖。"""
    original = next(iter(sorted(session_dir.glob("original.*"))), None)
    if original is None or not original.is_file():
        return
    root_dishes = []
    for d in dishes_out:
        entry = {
            "dish_id": d.get("dish_id") or "",
            "dish_name": d.get("dish_name") or "",
            "confidence": round(float(d.get("confidence") or 0), 2),
            "reason": d.get("reason"),
            "bbox": d.get("bbox"),
        }
        alt = d.get("alternative")
        if isinstance(alt, dict) and alt.get("dish_name"):
            entry["alternative"] = {
                "dish_id": alt.get("dish_id") or "",
                "dish_name": alt.get("dish_name") or "",
                "confidence": round(float(alt.get("confidence") or 0), 2),
                "reason": alt.get("reason"),
            }
        root_dishes.append(entry)

    _write_result_json(session_dir / "result.json", meal_type, root_dishes)
    draw_boxed_image(
        original,
        root_dishes,
        session_dir / "result_boxed.jpg",
    )

    if not has_dual:
        return

    r1_dishes = []
    r2_dishes = []
    for d in root_dishes:
        left = {
            "dish_id": d.get("dish_id") or "",
            "dish_name": d.get("dish_name") or "",
            "confidence": d.get("confidence") or 0,
            "reason": d.get("reason"),
            "bbox": d.get("bbox"),
        }
        r1_dishes.append(left)

        alt = d.get("alternative") if isinstance(d.get("alternative"), dict) else None
        if alt and alt.get("dish_name"):
            right = {
                "dish_id": alt.get("dish_id") or "",
                "dish_name": alt.get("dish_name") or "",
                "confidence": alt.get("confidence") or 0,
                "reason": alt.get("reason"),
                "bbox": d.get("bbox"),
            }
        else:
            right = dict(left)
        r2_dishes.append(right)

    r1_dir = session_dir / "result1"
    r2_dir = session_dir / "result2"
    if r1_dir.is_dir():
        _write_result_json(r1_dir / "result.json", meal_type, r1_dishes)
        draw_boxed_image(original, r1_dishes, r1_dir / "result_boxed.jpg")
    if r2_dir.is_dir():
        _write_result_json(r2_dir / "result.json", meal_type, r2_dishes)
        draw_boxed_image(original, r2_dishes, r2_dir / "result_boxed.jpg")


def _write_bundle_files(
    dest_dir: Path,
    original_path: Path,
    original_name: str,
    predictions: list[dict],
    dish_by_id: dict,
) -> None:
    """在 dest_dir 寫入原圖、標註圖與 result.json。"""
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
    """建立 YYYYMMDD_HHMMSS 資料夾。寫入 original、result_boxed.jpg 與 result.json。
    有 alternative 時另建 result1 與 result2。result1 是主候選。result2 是次佳。
    """
    session_dir = make_session_dir(upload_root)

    suffix = source_path.suffix.lower() or ".jpg"
    original_name = f"original{suffix}"
    original_path = session_dir / original_name
    source_path.replace(original_path)

    primary_preds = _strip_alternatives(predictions)
    draw_boxed_image(original_path, primary_preds, session_dir / "result_boxed.jpg")
    payload = build_result_json(predictions, dish_by_id)
    (session_dir / "result.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    if any(item.get("alternative") for item in predictions):
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
            _apply_alternatives(predictions),
            dish_by_id,
        )

    return session_dir
