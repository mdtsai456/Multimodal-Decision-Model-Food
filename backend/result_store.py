"""辨識結果存檔：日期時間資料夾內放原圖、標註圖、JSON。"""
from __future__ import annotations  # 讓型別註解可用「之後才定義」的名稱

# --- 標準函式庫 ---
import json                      # 讀寫 result.json
import re                        # 解析資料夾名稱正則
import shutil                    # 複製原圖到 result1/result2
from collections import Counter  # 統計最常見的餐別
from datetime import datetime    # 產生「日期_時間」資料夾名
from pathlib import Path         # 路徑處理

# --- 影像處理（畫外框、標籤）---
from PIL import Image, ImageDraw, ImageFont, ImageOps

# BBOX_SHIFT_VERSION：存檔版本號；denudge_top_bboxes：還原舊版上排偏移
from .bbox_adjust import BBOX_SHIFT_VERSION, denudge_top_bboxes

# 資料夾命名：YYYYMMDD_HHMMSS 或同秒撞名時 YYYYMMDD_HHMMSS_N
SESSION_DIR_RE = re.compile(r"^(\d{8})_(\d{6})(?:_(\d+))?$")


def parse_session_name(name: str) -> tuple[str, str] | None:
    """回傳 (YYYYMMDD, HHMMSS)；不合命名規則則 None。"""
    m = SESSION_DIR_RE.match(name)  # 用正則對資料夾名
    if not m:
        return None  # 名稱不符（可能是暫存檔等）→ 忽略
    return m.group(1), m.group(2)  # 日期八碼、時間六碼


def _read_result_json(path: Path) -> dict | None:
    """讀取 result.json；失敗或不存在回 None。"""
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))  # 文字 → Python dict
    except (OSError, json.JSONDecodeError):
        return None  # 讀檔失敗或 JSON 壞掉
    return data if isinstance(data, dict) else None  # 根節點必須是物件


def list_records_for_date(upload_root: Path, date_ymd: str) -> list[dict]:
    """
    列出指定日期（YYYY-MM-DD 或 YYYYMMDD）的辨識紀錄。
    依資料夾名稱前綴 YYYYMMDD 篩選。
    """
    compact = date_ymd.replace("-", "")  # "2026-07-16" → "20260716"
    if not re.fullmatch(r"\d{8}", compact):
        return []  # 日期格式不對 → 空列表

    records: list[dict] = []
    if not upload_root.is_dir():
        return records  # uploads 資料夾不存在

    # 新到舊排序（資料夾名含時間，字串排序即可）
    for path in sorted(upload_root.iterdir(), reverse=True):
        if not path.is_dir():
            continue  # 略過檔案，只看 session 資料夾
        parsed = parse_session_name(path.name)
        if not parsed or parsed[0] != compact:
            continue  # 名稱不合或不是查詢的那天

        date_part, time_part = parsed
        payload = _read_result_json(path / "result.json") or {}  # 讀該次結果
        dishes = payload.get("dishes") if isinstance(payload.get("dishes"), list) else []
        # 同時有 result1、result2 → 當時有雙結果
        has_dual = (path / "result1").is_dir() and (path / "result2").is_dir()
        # 摘要用：菜名列表
        names = [
            str(d.get("dish_name") or "")
            for d in dishes
            if isinstance(d, dict) and d.get("dish_name")
        ]
        hh, mm, ss = time_part[0:2], time_part[2:4], time_part[4:6]  # HHMMSS → 時:分:秒
        records.append(
            {
                "id": path.name,  # 資料夾名＝紀錄 id
                "date": f"{date_part[0:4]}-{date_part[4:6]}-{date_part[6:8]}",
                "time": f"{hh}:{mm}:{ss}",
                "meal_type": str(payload.get("meal_type") or ""),
                "dish_count": len(dishes),
                "dish_names": names,
                "has_dual": has_dual,
                "boxed_url": f"/uploads/{path.name}/result_boxed.jpg",  # 前端可直接開圖
            }
        )
    return records

"""=======救舊資料、從標註圖反推外框位置時用的小工具=========="""
def _color_dist(a: tuple[int, int, int], b: tuple[int, int, int]) -> int:
    """兩個 RGB 顏色的距離（各通道差絕對值相加）；愈小愈像。"""
    return abs(a[0] - b[0]) + abs(a[1] - b[1]) + abs(a[2] - b[2])


def recover_bboxes_from_boxed(
    original_path: Path,
    boxed_path: Path,
    dish_count: int,
) -> list[list[float] | None]:
    """
    舊紀錄沒存 bbox 時，比對原圖與標註圖的色塊還原相對座標。
    回傳長度 = dish_count；失敗的位置為 None。
    """
    # 沒菜數或檔案不在 → 無法還原
    if dish_count <= 0 or not original_path.is_file() or not boxed_path.is_file():
        return []

    # 原圖依 EXIF 轉正後轉 RGB；標註圖也轉 RGB
    orig = ImageOps.exif_transpose(Image.open(original_path)).convert("RGB")
    boxed = Image.open(boxed_path).convert("RGB")
    if boxed.size != orig.size:
        boxed = boxed.resize(orig.size)  # 尺寸不一致就縮放到一樣
    w, h = orig.size
    op = orig.load()    # 原圖像素存取
    bp = boxed.load()   # 標註圖像素存取
    colors = colors_for_count(dish_count)  # 當初畫框用的顏色序列
    boxes: list[list[float] | None] = []

    # 每種顏色找「標註圖有、原圖沒有」的像素 → 就是框線／標籤區
    for color in colors:
        xs: list[int] = []
        ys: list[int] = []
        for y in range(0, h, 2):      # 隔行掃描，加快速度
            for x in range(0, w, 2):
                bc = bp[x, y]  # 標註圖像素
                oc = op[x, y]  # 原圖像素
                # 接近該色，且與原圖差夠大 → 判定為畫上去的色
                if _color_dist(bc, color) <= 40 and _color_dist(bc, oc) > 30:
                    xs.append(x)
                    ys.append(y)
        if len(xs) < 8:
            boxes.append(None)  # 點太少，認不出框
            continue
        x0, x1 = min(xs), max(xs)  # 包圍這些點的矩形
        y0, y1 = min(ys), max(ys)
        # 標籤列在框上方，略往下裁，讓 canvas 標籤不要疊太高
        trim = max(8, (y1 - y0) // 12)
        y0 = min(y0 + trim, y1 - 4)
        # 轉成相對座標 [x, y, w, h]（0～1）
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
    """若 dishes 缺 bbox，嘗試從標註圖還原並寫回該筆 result.json。"""
    if not dishes:
        return dishes
    # 每道都已有合法 bbox → 不用還原
    if all(isinstance(d.get("bbox"), list) and len(d["bbox"]) == 4 for d in dishes):
        return dishes

    boxed = session_dir / "result_boxed.jpg"
    # 找 original.jpg / original.png 等
    original = next(iter(sorted(session_dir.glob("original.*"))), None)
    # 雙結果時優先用 result1 裡的圖
    if (session_dir / "result1" / "result_boxed.jpg").is_file():
        boxed = session_dir / "result1" / "result_boxed.jpg"
        original = next(
            iter(sorted((session_dir / "result1").glob("original.*"))),
            original,
        )
    if original is None or not boxed.is_file():
        return dishes  # 缺圖無法還原

    try:
        recovered = recover_bboxes_from_boxed(original, boxed, len(dishes))
    except OSError:
        return dishes

    changed = False
    # 把還原到的 bbox 填進缺框的菜
    for dish, bbox in zip(dishes, recovered):
        if bbox and not (
            isinstance(dish.get("bbox"), list) and len(dish.get("bbox")) == 4
        ):
            dish["bbox"] = bbox
            changed = True

    if changed:
        # 寫回 result.json，下次就不用再掃圖
        result_path = session_dir / "result.json"
        payload = _read_result_json(result_path) or {}
        payload_dishes = (
            payload.get("dishes") if isinstance(payload.get("dishes"), list) else []
        )
        for i, d in enumerate(payload_dishes):
            if i < len(dishes) and dishes[i].get("bbox"):
                if isinstance(d, dict):
                    d["bbox"] = dishes[i]["bbox"]
        # 若原本沒 dishes，用記憶體裡的 dishes 重建
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
            pass  # 寫失敗就算了，記憶體裡已有 bbox

    return dishes
"""============================================================="""

"""把磁碟上存的一道菜，整理成 API／前端好用的固定格式。"""
def _normalize_stored_dish(d: dict) -> dict:
    """把 result.json 的一道菜轉成 API 詳情格式。"""
    bbox = d.get("bbox")
    if not isinstance(bbox, list) or len(bbox) != 4:
        bbox = None  # 格式不對當沒有框
    else:
        try:
            bbox = [float(v) for v in bbox]  # 統一成 float
        except (TypeError, ValueError):
            bbox = None

    alt_raw = d.get("alternative")
    alternative = None
    # 有次佳菜名才組 alternative 物件
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
    """讀取單筆辨識紀錄詳情；找不到或路徑不合法回傳 None。"""
    # 擋掉奇怪的 id（避免路徑穿越）
    if not SESSION_DIR_RE.match(session_id):
        return None
    session_dir = (upload_root / session_id).resolve()
    try:
        # 必須仍在 upload_root 底下，防止 ../ 逃出
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
    # 雙結果子目錄的 JSON（可能用來補 bbox／alternative）
    r1 = _read_result_json(session_dir / "result1" / "result.json") or {}
    r2 = _read_result_json(session_dir / "result2" / "result.json") or {}
    d1 = r1.get("dishes") if isinstance(r1.get("dishes"), list) else []
    d2 = r2.get("dishes") if isinstance(r2.get("dishes"), list) else []

    if primary_dishes:
        # 優先用根目錄 result.json（通常最完整）
        for i, d in enumerate(primary_dishes):
            if not isinstance(d, dict):
                continue
            item = _normalize_stored_dish(d)
            # 根目錄缺 bbox → 試 result1
            if item["bbox"] is None and i < len(d1) and isinstance(d1[i], dict):
                item["bbox"] = _normalize_stored_dish(d1[i])["bbox"]
            # 根目錄缺 alternative → 試 result2 同索引那道菜
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
        # 舊格式：根目錄可能沒完整 dishes，改從 result1/result2 拼
        n = max(len(d1), len(d2))
        for i in range(n):
            left_src = d1[i] if i < len(d1) and isinstance(d1[i], dict) else {}
            item = _normalize_stored_dish(left_src)  # 左＝主候選
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

    # 舊紀錄缺 bbox：從標註圖還原，讓前端可用 canvas 畫出清晰繁中標籤
    dishes_out = _ensure_dish_bboxes(session_dir, dishes_out)

    # 舊版全體 Y-nudge 會讓上排框太低：還原後重畫標註圖（只做一次）
    stored_ver = int(primary.get("bbox_shift_version") or 1)
    if stored_ver < BBOX_SHIFT_VERSION:
        meal_type = str(primary.get("meal_type") or "")
        if denudge_top_bboxes(dishes_out):
            # 有改框 → 寫回 JSON 並重畫標註圖
            _persist_denudged_bboxes(
                session_dir,
                dishes_out,
                has_dual=has_dual,
                meal_type=meal_type,
            )
        else:
            # 無需改框也標成新版，避免每次載入重試
            primary["bbox_shift_version"] = BBOX_SHIFT_VERSION
            try:
                (session_dir / "result.json").write_text(
                    json.dumps(primary, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            except OSError:
                pass

    # 原圖 URL（給前端 canvas 重畫用）
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
    """依菜色數量在色環均分色相，同一次辨識每道菜顏色都不同。"""
    total = max(n, 1)
    colors = []
    for i in range(total):
        hue = (i * 360 / total) % 360  # 色相均分 360 度
        colors.append(_hsl_to_rgb(hue, 0.58, 0.40))  # 固定飽和度／亮度
    return colors


def _hsl_to_rgb(h: float, s: float, l: float) -> tuple[int, int, int]:
    """HSL → RGB（Pillow 畫圖用 0～255）。"""
    # h: 0~360, s/l: 0~1 → RGB 0~255
    c = (1 - abs(2 * l - 1)) * s
    x = c * (1 - abs((h / 60) % 2 - 1))
    m = l - c / 2
    # 依色相落在哪一段，決定 RGB 哪個通道最大
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
    """建立本次辨識專用資料夾，名稱為日期_時間。"""
    # 例如 20260716_125930
    base = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = upload_root / base
    n = 1
    # 同一秒內再辨識一次 → 加 _1、_2…
    while path.exists():
        path = upload_root / f"{base}_{n}"
        n += 1
    path.mkdir(parents=True, exist_ok=False)
    return path


def _load_font(image_width: int) -> ImageFont.ImageFont:
    """載入繁中字型，用來在標註圖上寫菜名。"""
    # 字級略大，縮圖後繁中仍清晰
    size = max(28, image_width // 28)
    # 優先用專案內建繁中字型（部署 Linux 沒有 Windows 字型時仍正常）
    bundled = Path(__file__).resolve().parent / "assets" / "fonts" / "NotoSansTC-Regular.otf"
    candidates: list[tuple[Path, int]] = [
        (bundled, 0),
        (Path(r"C:\Windows\Fonts") / "msjh.ttc", 0),   # 微軟正黑體
        (Path(r"C:\Windows\Fonts") / "msyh.ttc", 0),   # 微軟雅黑
        (Path(r"C:\Windows\Fonts") / "mingliu.ttc", 0),
        (Path(r"C:\Windows\Fonts") / "kaiu.ttf", 0),
        (Path(r"C:\Windows\Fonts") / "arialuni.ttf", 0),
        # Linux／部署環境常見 Noto CJK
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
                return ImageFont.truetype(str(candidate), size=size)  # 不帶 index 再試
            except OSError:
                continue
    print("[warn] 找不到繁中字型，標註圖菜名可能變小或亂碼；請確認 backend/assets/fonts/NotoSansTC-Regular.otf")
    return ImageFont.load_default()  # 最後手段：預設字型（繁中可能爛）


def draw_boxed_image(
    source_path: Path,
    predictions: list[dict],
    out_path: Path,
) -> None:
    """在原圖上畫外框＋菜名，存成 result_boxed.jpg。"""
    # 與辨識時相同：先依 EXIF 轉正，避免框對錯方向
    img = ImageOps.exif_transpose(Image.open(source_path)).convert("RGB")
    draw = ImageDraw.Draw(img)
    w, h = img.size
    font = _load_font(w)
    line_w = max(2, w // 220)  # 線寬隨圖寬縮放
    colors = colors_for_count(len(predictions))

    for index, item in enumerate(predictions):
        box = item.get("bbox")
        if not isinstance(box, (list, tuple)) or len(box) != 4:
            continue  # 沒框就只跳過這道，其他照畫
        x, y, bw, bh = [float(v) for v in box]  # 相對座標 0～1
        left, top = x * w, y * h                 # 轉像素
        right, bottom = left + bw * w, top + bh * h
        color = colors[index]

        # 畫外框矩形
        draw.rectangle([left, top, right, bottom], outline=color, width=line_w)

        label = str(item.get("name") or item.get("dish_name") or "")
        if not label:
            continue
        # 量文字大小 → 在框上方畫色塊底＋白字
        text_bbox = draw.textbbox((0, 0), label, font=font)
        tw = text_bbox[2] - text_bbox[0]
        th = text_bbox[3] - text_bbox[1]
        pad = 6
        ly = max(0, top - th - pad * 2 - 2)  # 標籤放在框頂之上，不超出圖頂
        draw.rectangle(
            [left, ly, left + tw + pad * 2, ly + th + pad * 2],
            fill=color,
        )
        draw.text((left + pad, ly + pad), label, fill=(255, 255, 255), font=font)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    # 一律存 jpg，方便瀏覽
    img.save(out_path, format="JPEG", quality=90)


def _dish_entry(item: dict) -> dict:
    """單一菜色存檔格式（含 bbox，供之後用 canvas 重畫標註）。"""
    code = item.get("dish_code")
    # 存檔的 dish_id 優先用菜色編號（Excel 編號），沒有才用資料庫 id
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
    """組出要寫進 result.json 的內容。"""
    meal_types = []
    dishes_out = []
    for item in predictions:
        dish = dish_by_id.get(item.get("dish_id"))  # 對回 DB 菜色
        if dish and dish.meal_type:
            meal_types.append(dish.meal_type)  # 收集餐別以便投票
        dishes_out.append(_dish_entry(item))

    meal_type = ""
    if meal_types:
        # 取出現最多次的餐別當這次的 meal_type
        meal_type = Counter(meal_types).most_common(1)[0][0]

    return {
        "meal_type": meal_type,
        "dishes": dishes_out,
        "bbox_shift_version": BBOX_SHIFT_VERSION,  # 標記新版 bbox 規則
    }


def _dish_for_draw(name: str, bbox) -> dict:
    """畫標註圖時只需菜名＋框。"""
    return {"name": name, "bbox": bbox}


def _write_result_json(path: Path, meal_type: str, dishes: list[dict]) -> None:
    """把 meal_type + dishes 寫成 JSON 檔。"""
    payload = {
        "meal_type": meal_type,
        "dishes": dishes,
        "bbox_shift_version": BBOX_SHIFT_VERSION,
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),  # 繁中不轉成 \uXXXX
        encoding="utf-8",
    )


def _persist_denudged_bboxes(
    session_dir: Path,
    dishes_out: list[dict],
    *,
    has_dual: bool,
    meal_type: str,
) -> None:
    """把還原後的上排 bbox 寫回 JSON，並重畫 result_boxed 標註圖。"""
    original = next(iter(sorted(session_dir.glob("original.*"))), None)
    if original is None or not original.is_file():
        return  # 沒原圖無法重畫

    # --- 根目錄 result.json + result_boxed.jpg ---
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
        [_dish_for_draw(d["dish_name"], d.get("bbox")) for d in root_dishes],
        session_dir / "result_boxed.jpg",
    )

    if not has_dual:
        return  # 沒雙結果就結束

    # --- 同步更新 result1（主）／result2（次佳）---
    r1_dishes = []
    r2_dishes = []
    r1_draw = []
    r2_draw = []
    for d in root_dishes:
        left = {
            "dish_id": d.get("dish_id") or "",
            "dish_name": d.get("dish_name") or "",
            "confidence": d.get("confidence") or 0,
            "reason": d.get("reason"),
            "bbox": d.get("bbox"),
        }
        r1_dishes.append(left)
        r1_draw.append(_dish_for_draw(left["dish_name"], left.get("bbox")))

        alt = d.get("alternative") if isinstance(d.get("alternative"), dict) else None
        if alt and alt.get("dish_name"):
            # 次佳：菜名不同，bbox 共用
            right = {
                "dish_id": alt.get("dish_id") or "",
                "dish_name": alt.get("dish_name") or "",
                "confidence": alt.get("confidence") or 0,
                "reason": alt.get("reason"),
                "bbox": d.get("bbox"),
            }
        else:
            right = dict(left)  # 沒次佳就跟左邊一樣
        r2_dishes.append(right)
        r2_draw.append(_dish_for_draw(right["dish_name"], right.get("bbox")))

    r1_dir = session_dir / "result1"
    r2_dir = session_dir / "result2"
    if r1_dir.is_dir():
        _write_result_json(r1_dir / "result.json", meal_type, r1_dishes)
        draw_boxed_image(original, r1_draw, r1_dir / "result_boxed.jpg")
    if r2_dir.is_dir():
        _write_result_json(r2_dir / "result.json", meal_type, r2_dishes)
        draw_boxed_image(original, r2_draw, r2_dir / "result_boxed.jpg")


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
    """次解釋：有 alternative 的位置改用次佳候選（bbox 仍用同一格）。"""
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
                    "bbox": item.get("bbox"),  # 框共用
                }
            )
        else:
            # 這格沒有次佳 → 維持主候選
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
    """在 dest_dir 寫入：原圖副本、標註圖、result.json。"""
    dest_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(original_path, dest_dir / original_name)  # 複製原圖
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
    session_dir = make_session_dir(upload_root)  # 新建本次資料夾

    suffix = source_path.suffix.lower() or ".jpg"
    original_name = f"original{suffix}"
    original_path = session_dir / original_name
    # 原圖移入資料夾（不再留在 uploads 根目錄）
    source_path.replace(original_path)

    primary_preds = _strip_alternatives(predictions)  # 只含主候選，用來畫框
    has_close = any(item.get("alternative") for item in predictions)  # 有無雙結果

    if has_close:
        alt_preds = _apply_alternatives(predictions)  # 次佳版本的菜名列表
        # result1：主候選標註
        _write_bundle_files(
            session_dir / "result1",
            original_path,
            original_name,
            primary_preds,
            dish_by_id,
        )
        # result2：次佳候選標註
        _write_bundle_files(
            session_dir / "result2",
            original_path,
            original_name,
            alt_preds,
            dish_by_id,
        )
        # 根目錄寫完整結果（含 alternative + bbox），方便紀錄頁 canvas 重畫
        boxed_path = session_dir / "result_boxed.jpg"
        draw_boxed_image(original_path, primary_preds, boxed_path)
        payload = build_result_json(predictions, dish_by_id)  # 含 alternative
        (session_dir / "result.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    else:
        # 一般情況：根目錄只存一組主結果
        boxed_path = session_dir / "result_boxed.jpg"
        draw_boxed_image(original_path, primary_preds, boxed_path)
        payload = build_result_json(primary_preds, dish_by_id)
        (session_dir / "result.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    return session_dir  # 回傳資料夾 Path，API 可取 .name
