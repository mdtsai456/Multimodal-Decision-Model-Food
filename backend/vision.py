"""餐盤圖片辨識：把菜單餵給 Zeabur AI Hub，回傳菜名與信心分數。"""

# --- 標準函式庫 ---
import base64      # 圖片轉成 base64，才能塞進 API 請求
import io          # 把縮放後的 JPEG 留在記憶體
import json        # 解析模型回傳的 JSON
import mimetypes   # 依副檔名判斷 image/jpeg、image/png 等
import os          # 讀取 .env 裡的環境變數
import re          # 從模型文字中抽出 ```json ... ``` 區塊
from enum import Enum
from pathlib import Path
from typing import assert_never

# --- AI 客戶端（連 Zeabur AI Hub，目前用 OpenAI 格式呼叫 GPT）---
import httpx
from openai import OpenAI
from PIL import Image, ImageOps

from .bbox_adjust import (
    BBOX_BOTTOM_Y_SHIFT,
    BBOX_TOP_Y_SHIFT,
    BBOX_Y_SHIFT,
    nudge_bboxes as _nudge_bboxes,
)
from .models import Dish

# 從環境變數讀設定（見專案根目錄 .env）
API_KEY = os.getenv("ZEABUR_AI_HUB_API_KEY", "")
BASE_URL = os.getenv("ZEABUR_AI_HUB_BASE_URL", "https://hnd1.aihub.zeabur.ai/")
MODEL = os.getenv("AI_MODEL", "gpt-5.6-luna")
# 兩道候選信心差 ≤ 此值時視為「相近」，前端拆成雙條、後端存 result1/result2
CLOSE_CONFIDENCE_DELTA = float(os.getenv("CLOSE_CONFIDENCE_DELTA", "0.30"))
JEV_MODEL = os.getenv("JEV_MODEL", "typesafe/jev-1.14")
JEV_DECISIONS_URL = os.getenv(
    "JEV_DECISIONS_URL", "https://openrouter.ai/api/alpha/decisions"
)
NOUL_PRESENT_MIN = float(os.getenv("NOUL_PRESENT_MIN", "0.50"))
INFERENCE_ENGINE = os.getenv("INFERENCE_ENGINE", "chat")
# 兩框重疊到這個程度，Jev 視為同一格食物
JEV_SAME_CELL_IOU = 0.45
print(
    f"[config] INFERENCE_ENGINE={INFERENCE_ENGINE} AI_MODEL={MODEL} "
    f"JEV_MODEL={JEV_MODEL} NOUL_PRESENT_MIN={NOUL_PRESENT_MIN} "
    f"CLOSE_CONFIDENCE_DELTA={CLOSE_CONFIDENCE_DELTA} "
    f"BBOX_TOP_Y_SHIFT={BBOX_TOP_Y_SHIFT} BBOX_BOTTOM_Y_SHIFT={BBOX_BOTTOM_Y_SHIFT} "
    f"(legacy BBOX_Y_SHIFT={BBOX_Y_SHIFT})"
)


class InferenceEngine(str, Enum):
    """辨識走看圖對話、Jev，或 Jev 之後的區域 Choice。"""

    CHAT = "chat"
    JEV = "jev"
    CHOICE = "choice"


def resolve_engine(override: str | None) -> InferenceEngine:
    """表單有指定就用表單，否則用環境變數；空白視為 chat。"""
    raw = override if override is not None else os.getenv("INFERENCE_ENGINE", "")
    text = (raw or "").strip().lower()
    if not text:
        return InferenceEngine.CHAT
    try:
        return InferenceEngine(text)
    except ValueError as exc:
        raise ValueError("engine 只接受 chat、jev 或 choice") from exc


# 依檔名猜測圖片 MIME 類型；猜不到就當 jpeg
def _guess_media_type(path: Path) -> str:
    mime, _ = mimetypes.guess_type(str(path))
    if mime in {"image/jpeg", "image/png", "image/gif", "image/webp"}:
        return mime
    return "image/jpeg"


# 過長敘述截斷，避免整份 prompt 太大、太貴、太慢
def _clip(text: str | None, max_len: int = 420) -> str:
    if not text:
        return ""
    cleaned = " ".join(str(text).split())  # 壓成單行空白
    return cleaned if len(cleaned) <= max_len else cleaned[: max_len - 1] + "…"


# 把資料庫菜單編成文字清單，當成模型的「候選菜單」
def _dish_catalog(dishes: list[Dish]) -> str:
    blocks = []
    for d in dishes:
        parts = [
            f"【{d.name}】",
            f"id={d.id}",
            f"code={d.dish_code}",
            f"餐別={d.meal_type or ''}",
            f"組成={d.composition or ''}",
        ]
        # 烹調方式／外觀特徵：辨識時最重要的參考文字
        cooking = _clip(d.cooking_method)
        if cooking:
            parts.append(f"AI烹調方式/外觀特徵={cooking}")
        oil = _clip(d.oil_flavor_note, 220)
        if oil:
            parts.append(f"吸油調味特徵={oil}")
        if d.fluffiness:
            parts.append(f"蓬鬆度={d.fluffiness}")
        blocks.append("\n".join(parts))
    return "\n\n".join(blocks)


# 模型回覆不一定是純 JSON，這裡盡量抽出可解析的 JSON
def _extract_json(text: str) -> dict:
    text = text.strip()
    # 1) 直接就是 JSON
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 2) 包在 ```json ... ``` 程式碼區塊裡
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fenced:
        try:
            return json.loads(fenced.group(1).strip())
        except json.JSONDecodeError:
            pass

    # 3) 退而求其次：找第一個 { 到最後一個 }
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        return json.loads(text[start : end + 1])
    raise ValueError("模型回應無法解析為 JSON")


# 組出給模型的完整指示（規則 + 候選菜單）
def _build_prompt(catalog: str) -> str:
    return f"""你是餐盤菜色辨識助手。請依「候選菜單」判斷這張餐盤照片中出現哪些菜色。

規則：
1. 只能從候選菜單中選擇，不可發明不存在的菜名。
2. 請優先比對每道菜的「AI烹調方式/外觀特徵」與「組成」：顏色、質地、醬汁、形狀、切法、是否有配菜等，不要只看菜名。
3. 請「逐格」檢查分格餐盤：每一格都要看，且同一格可能同時有 2 道以上菜色（左右並排、上下堆疊都算）。
4. 顏色相近、同為燉煮/醬燒外觀的菜色（例如醬燒冬瓜與醬燒蘿蔔）常被誤判。若烹調方式相同、僅主食材不同，且畫面上看不出清楚差異，兩者 confidence 應接近（差距通常 < 0.2），不要把最佳拉到 0.95+、次佳壓到 0.6 以下。
5. 若畫面明顯只有一道菜，請精準對到最吻合外觀特徵的那一道，不要硬湊其他菜。
6. 一張餐盤也可能有多道菜，請全部列出，不要過早停止；但也不可為了湊數而亂猜。盤上實際出現的每一格／每一道都要有一筆 predictions，不可因為某道已當過別道的 alternative 就省略。
7. 每道菜給 confidence（0~1 浮點數）；外觀特徵吻合度高才給高分。若最佳與次佳外觀極相似，請避免過度自信。
8. 每道菜都要給 bbox（緊貼該道「食物」的外框）。
9. 【強制次佳候選】每一筆預測都「必須」提供 alternative：針對「同一個 bbox 位置」從候選菜單另選一道最像的次佳菜（不可與 name 相同）。name 放信心最高者，alternative 放第二高者，並各自給 confidence。後端會依信心差決定是否採用；你仍必須每次都給出 alternative，不可省略。alternative 只表示「同位置的次佳猜測」，不能取代其他格子裡真正出現的菜。
10. 若幾乎無法判斷，可回傳空陣列。
11. 只輸出 JSON，不要其他說明文字。

JSON 格式（alternative 為必填；bbox 用左上＋右下角點）：
{{
  "predictions": [
    {{
      "name": "最佳菜名（必須與候選菜單完全一致）",
      "dish_id": 123,
      "dish_code": 6,
      "confidence": 0.92,
      "reason": "簡短理由（對應到哪個外觀特徵）",
      "bbox": [0.12, 0.48, 0.40, 0.82],
      "alternative": {{
        "name": "次佳菜名（必填，須與 name 不同）",
        "dish_id": 124,
        "dish_code": 7,
        "confidence": 0.88,
        "reason": "簡短理由"
      }}
    }}
  ]
}}

bbox 說明（務必正確，常見錯誤會害下排框飄到上排）：
- 格式 [x1, y1, x2, y2]：左上角 (x1,y1) 與右下角 (x2,y2)，皆為相對「整張照片」的 0～1
- 原點在「整張圖」左上；y 愈大愈靠下（不是餐盤內座標）
- 框要包住該道菜的「全部」食物像素：上緣貼近食物頂部、下緣貼近食物底部；寧可略大，不要只框上半截
- 分格餐盤常見為上排／下排兩列：下排菜的中心點通常在整張圖 y≈0.65～0.85，y2 常 ≥ 0.78；若下排框的 y2 < 0.70，多半是畫太上面了，請下移並加高
- 禁止：下排框的上緣畫進上排格子，或下緣停在食物中間
- 上排菜：中心點通常在 y≈0.25～0.45，不要拉到下排
- 同一格有兩道菜時請左右分開框，但仍要對準食物高度
- alternative 與主預測共用同一 bbox，不要為 alternative 另開一筆 predictions
- 菜名必須與候選菜單「完全一致」（繁體字），不可改成簡體或其他寫法

候選菜單（含 AI 烹調方式敘述，請用來比對外觀）：
{catalog}
"""


# 呼叫 Zeabur AI Hub（OpenAI 相容格式 + 看圖）
def _ask_model(prompt: str, media_type: str, image_b64: str) -> str:
    if not API_KEY:
        raise RuntimeError("ZEABUR_AI_HUB_API_KEY is required")

    client = OpenAI(base_url=BASE_URL, api_key=API_KEY)
    res = client.chat.completions.create(
        model=MODEL,
        max_tokens=4096,
        temperature=0,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    # 圖片用 data URI 傳給支援 vision 的模型
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{media_type};base64,{image_b64}",
                        },
                    },
                ],
            }
        ],
    )
    choice = res.choices[0]
    finish = getattr(choice, "finish_reason", None)
    if finish == "length":
        print("[debug] 模型輸出被 max_tokens 截斷，可能漏菜；請再試一次或簡化菜單")
    return (choice.message.content or "").strip()


# 把模型回的一道預測，對回資料庫裡真正的 Dish
def _match_dish(item: dict, dishes: list[Dish]) -> Dish | None:
    by_id = {d.id: d for d in dishes}
    by_name = {d.name: d for d in dishes}
    dish_id = item.get("dish_id")
    name = (item.get("name") or "").strip()

    # 優先用 id（最準）
    if isinstance(dish_id, int) and dish_id in by_id:
        return by_id[dish_id]
    # 再比對完整菜名
    if name in by_name:
        return by_name[name]
    # 最後寬鬆比對（名稱互相包含）
    for d in dishes:
        if name and (name in d.name or d.name in name):
            return d
    return None


# 把模型回的 bbox 正規化成 [x,y,w,h]（0～1）；不合理就回 None
# 新格式優先：[x1,y1,x2,y2]；若不像角點則退回舊格式 [x,y,w,h]
def _normalize_bbox(raw) -> list[float] | None:
    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
        return None
    try:
        a, b, c, d = [float(v) for v in raw]
    except (TypeError, ValueError):
        return None
    # 若模型誤傳成像素很大，粗略當成已是相對座標失敗
    if max(a, b, c, d) > 1.5:
        return None

    # x2>x1 且 y2>y1 → 視為角點格式
    if c > a and d > b:
        x1, y1, x2, y2 = a, b, c, d
    else:
        # 舊格式 [x, y, w, h]
        x1, y1, x2, y2 = a, b, a + c, b + d

    x1 = max(0.0, min(1.0, x1))
    y1 = max(0.0, min(1.0, y1))
    x2 = max(0.0, min(1.0, x2))
    y2 = max(0.0, min(1.0, y2))
    if x2 <= x1 or y2 <= y1:
        return None
    w = x2 - x1
    h = y2 - y1
    if w < 0.02 or h < 0.02:  # 太小的框忽略
        return None
    return [round(x1, 4), round(y1, 4), round(w, 4), round(h, 4)] # 統一輸出：一律變成 [x, y, w, h]（相對座標 0～1）


def _ensure_upright_image(path: Path) -> None:
    """依 EXIF 轉正並覆寫檔案，讓模型看圖與畫框用同一像素方向。"""
    with Image.open(path) as img:
        oriented = ImageOps.exif_transpose(img) # 打開圖片後，依 EXIF 的旋轉／鏡像標記，把像素真正轉正。
        # 轉成 RGB 存回，去掉方向標記造成的錯位
        rgb = oriented.convert("RGB")
        # 轉成一般 RGB
        suffix = path.suffix.lower()
        if suffix in {".png", ".webp", ".gif"}:
            rgb.save(path)
        else:
            rgb.save(path, format="JPEG", quality=92)


def _prediction_core(dish: Dish, item: dict, bbox: list[float] | None) -> dict:
    conf = max(0.0, min(1.0, float(item.get("confidence", 0))))
    return {
        "name": dish.name,
        "dish_id": dish.id,
        "dish_code": dish.dish_code,
        "confidence": conf,
        "reason": item.get("reason"),
        "bbox": bbox,
    }


def _extract_second_best(
    raw_alt,
    dishes: list[Dish],
    primary_name: str,
    primary_id: int,
    bbox: list[float] | None,
) -> dict | None:
    """取出模型給的次佳候選（不依信心差過濾；給 console / second_best 用）。"""
    if not isinstance(raw_alt, dict):
        print(f"[debug] {primary_name}: 模型未給 alternative → 無次佳")
        return None
    alt_dish = _match_dish(raw_alt, dishes)
    if not alt_dish:
        print(f"[debug] {primary_name}: alternative 對不到菜單 → 無次佳")
        return None
    if alt_dish.id == primary_id:
        print(f"[debug] {primary_name}: alternative 與主菜相同 → 無次佳")
        return None
    alt = _prediction_core(alt_dish, raw_alt, bbox)
    return {
        "name": alt["name"],
        "dish_id": alt["dish_id"],
        "dish_code": alt["dish_code"],
        "confidence": alt["confidence"],
        "reason": alt["reason"],
    }


def _maybe_adopt_alternative(
    primary_name: str,
    primary_conf: float,
    second: dict | None,
) -> dict | None:
    """信心相近才採用為 alternative（影響前端雙條與 result1/result2）。"""
    if not second:
        return None
    delta = abs(float(second["confidence"]) - primary_conf)
    if delta > CLOSE_CONFIDENCE_DELTA:
        print(
            f"[debug] {primary_name} ({primary_conf:.2f}) / "
            f"{second['name']} ({second['confidence']:.2f}) "
            f"信心差 {delta:.2f} > {CLOSE_CONFIDENCE_DELTA} → 僅供 debug，不存 alternative"
        )
        return None
    return second


def _bbox_iou(a: list[float] | None, b: list[float] | None) -> float:
    """
    算兩個框重疊多少（IoU = 交集面積 / 聯集面積），回傳 0～1。
    愈接近 1 表示兩個框幾乎同一位置；用來判斷要不要合併成雙結果。
    框格式皆為 [x, y, w, h]（相對座標 0～1）。
    """
    # 缺框或不是 4 個數字 → 當完全不重疊
    if not a or not b or len(a) != 4 or len(b) != 4:
        return 0.0
    # 框 A：左上 (ax1,ay1)、右下 (ax2,ay2)
    ax1, ay1 = a[0], a[1]
    ax2, ay2 = a[0] + a[2], a[1] + a[3]
    # 框 B：同上
    bx1, by1 = b[0], b[1]
    bx2, by2 = b[0] + b[2], b[1] + b[3]
    # 交集矩形的左上／右下（取較內側的邊）
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    # 交集寬高；沒重疊時會是負的 → 用 max 夾成 0
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih # 交集面積
    if inter <= 0:
        return 0.0
    # 聯集 = A面積 + B面積 - 交集（重疊區不要算兩次)
    union = a[2] * a[3] + b[2] * b[3] - inter
    return inter / union if union > 0 else 0.0


def _merge_overlapping_close_pairs(predictions: list[dict]) -> list[dict]:
    """
    後備：若模型把相近兩道菜拆成兩筆且 bbox 重疊、信心相近，
    合併為一筆主預測 + alternative。
    (防止模型沒照做時結果還能合理顯示)
    """
    # 少於兩筆 → 不可能成對，原樣返回
    if len(predictions) < 2:
        return predictions

    used = set()  # 已被合併掉、不要再當主筆處理的索引
    merged: list[dict] = []  # 合併後的結果列表
    for i, a in enumerate(predictions):
        if i in used:
            continue  # 這筆已當過別人的次佳，跳過
        pair_j = None  # 找到的配對索引；找不到維持 None
        # 往後找能不能跟 a 配成「同一格的雙候選」
        for j in range(i + 1, len(predictions)):
            if j in used:
                continue  # j 已被用過
            b = predictions[j]
            # 任一筆已有 alternative → 不重複合併
            if a.get("alternative") or b.get("alternative"):
                continue
            # 信心差太大 → 不像「難分勝負」的兩道
            if abs(a["confidence"] - b["confidence"]) > CLOSE_CONFIDENCE_DELTA:
                continue
            # 框重疊不夠（IoU < 0.45）→ 多半是不同格子，不要併
            if _bbox_iou(a.get("bbox"), b.get("bbox")) < 0.45:
                continue
            pair_j = j  # 找到合適的一對
            break

        if pair_j is None:
            merged.append(a)  # 沒配對 → a 單獨留下
            continue

        b = predictions[pair_j]
        used.add(pair_j)  # 標記 b 已被併入，後面不要再處理
        # 信心高的當主預測，低的當次佳
        primary, secondary = (a, b) if a["confidence"] >= b["confidence"] else (b, a)
        primary = dict(primary)  # 複製一份，避免改到原 list 裡的物件
        second = {
            "name": secondary["name"],
            "dish_id": secondary["dish_id"],
            "dish_code": secondary["dish_code"],
            "confidence": secondary["confidence"],
            "reason": secondary.get("reason"),
        }
        primary["second_best"] = second   # debug 用
        primary["alternative"] = second   # 正式採用雙結果（前端／存檔）
        print(
            f"[debug] bbox 合併相近候選: "
            f"{primary['name']} ({primary['confidence']:.2f}) / "
            f"{secondary['name']} ({secondary['confidence']:.2f})"
        )
        merged.append(primary)

    # 合併完再依信心由高到低排
    merged.sort(key=lambda x: x["confidence"], reverse=True)
    return merged


def _debug_print_close_candidates(predictions: list[dict]) -> None:
    """印出每道菜的最佳／次佳，以及是否採用 alternative。"""
    if not predictions:
        print("[debug] 候選: 無")
        return
    for p in predictions:
        second = p.get("second_best") or p.get("alternative")
        best = (
            f"{p.get('name')} ({float(p.get('confidence') or 0):.2f})"
        )
        if second:
            second_s = (
                f"{second.get('name')} ({float(second.get('confidence') or 0):.2f})"
            )
            flag = "採用雙結果" if p.get("alternative") else "僅 debug"
            print(f"[debug] 最佳/次佳: {best} / {second_s} [{flag}]")
        else:
            print(f"[debug] 最佳/次佳: {best} / 無次佳")


def _fit_jev_size(width: int, height: int) -> tuple[int, int]:
    """把邊長收進 Jev 的 256～1568，總像素收進 78,400～2,458,624。"""
    scale = 1.0
    long_side = max(width, height)
    if long_side > 1568:
        scale = 1568 / long_side
    pixels = width * height
    if pixels * scale * scale > 2_458_624:
        scale = min(scale, (2_458_624 / pixels) ** 0.5)
    fitted_w = width * scale
    fitted_h = height * scale
    grow = 1.0
    short_side = min(fitted_w, fitted_h)
    if short_side > 0 and short_side < 256:
        grow = max(grow, 256 / short_side)
    fitted_pixels = fitted_w * fitted_h
    if fitted_pixels > 0 and fitted_pixels < 78_400:
        grow = max(grow, (78_400 / fitted_pixels) ** 0.5)
    grown_long = max(fitted_w, fitted_h) * grow
    grown_pixels = fitted_pixels * grow * grow
    if grow > 1 and grown_long <= 1568 and grown_pixels <= 2_458_624:
        scale *= grow
    return max(1, round(width * scale)), max(1, round(height * scale))


def _jpeg_data_url_for_jev(path: Path) -> str:
    """轉成 Jev 接受的 JPEG data URL。相對座標仍對得上轉正後的原圖。"""
    with Image.open(path) as img:
        rgb = img.convert("RGB")
        fitted = _fit_jev_size(*rgb.size)
        if fitted != rgb.size:
            rgb = rgb.resize(fitted, Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        rgb.save(buffer, format="JPEG", quality=90)
    encoded = base64.standard_b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


def _jev_questions(dishes: list[Dish]) -> dict:
    """每道菜一題在不在、一題外框。鍵用資料庫 id。"""
    questions: dict = {}
    for dish in dishes:
        appearance = _clip(dish.cooking_method, 200)
        present = f"照片中看得到一道「{dish.name}」。"
        if appearance:
            present += f"外觀：{appearance}"
        questions[f"present_{dish.id}"] = {"type": "noul", "instructions": present}
        questions[f"box_{dish.id}"] = {
            "type": "box",
            "instructions": (
                f"只框住這一道{dish.name}自己的範圍，貼著食物邊緣，不要框進旁邊的另一道菜"
            ),
        }
    return questions


def _bbox_from_jev_edges(edges) -> list[float] | None:
    """Jev 的四邊轉成現有檢查函式吃的角點，再得到 [x, y, w, h]。"""
    if not isinstance(edges, dict):
        return None
    try:
        left = float(edges["left"])
        top = float(edges["top"])
        right = float(edges["right"])
        bottom = float(edges["bottom"])
    except (KeyError, TypeError, ValueError):
        return None
    if right <= left or bottom <= top:
        return None
    return _normalize_bbox([left, top, right, bottom])


def _second_view(pred: dict) -> dict:
    return {
        "name": pred["name"],
        "dish_id": pred["dish_id"],
        "dish_code": pred["dish_code"],
        "confidence": pred["confidence"],
        "reason": pred.get("reason"),
    }


def _with_second(primary: dict, other: dict) -> dict:
    """次佳一律記下；差距夠小才讓前端顯示雙結果。"""
    merged = dict(primary)
    second = _second_view(other)
    merged["second_best"] = second
    delta = abs(float(merged["confidence"]) - float(other["confidence"]))
    if delta <= CLOSE_CONFIDENCE_DELTA:
        merged["alternative"] = second
    return merged


def _merge_jev_cells(primaries: list[dict]) -> list[dict]:
    """IoU 夠高就合成同一格，不看信心差。"""
    remaining = [dict(item) for item in primaries]
    merged: list[dict] = []
    while remaining:
        primary = remaining.pop(0)
        index = 0
        while index < len(remaining):
            other = remaining[index]
            if _bbox_iou(primary.get("bbox"), other.get("bbox")) < JEV_SAME_CELL_IOU:
                index += 1
                continue
            if "second_best" not in primary:
                primary = _with_second(primary, other)
            del remaining[index]
        merged.append(primary)
    return merged


def _fill_rejected_seconds(
    primaries: list[dict], rejected: list[dict]
) -> list[dict]:
    """主結果還沒有次佳時，從沒過門檻、但框在同一格的菜補上。"""
    filled: list[dict] = []
    for primary in primaries:
        if primary.get("second_best") or not rejected:
            filled.append(primary)
            continue
        neighbors = [
            item
            for item in rejected
            if _bbox_iou(primary.get("bbox"), item.get("bbox")) >= JEV_SAME_CELL_IOU
        ]
        if not neighbors:
            filled.append(primary)
            continue
        best = max(neighbors, key=lambda item: float(item["confidence"]))
        filled.append(_with_second(primary, best))
    return filled


def _collect_jev_hits(
    dishes: list[Dish],
    answers: dict,
    present_min: float,
) -> tuple[list[dict], list[dict]]:
    """Noul 過門檻且外框有效的留下，其餘能對上框的另放一袋。"""
    primaries: list[dict] = []
    rejected: list[dict] = []
    for dish in dishes:
        present = answers.get(f"present_{dish.id}")
        box_answer = answers.get(f"box_{dish.id}")
        noul_raw = present.get("noul") if isinstance(present, dict) else None
        try:
            noul = float(noul_raw)
        except (TypeError, ValueError):
            print(f"[debug] {dish.name}: 沒有 Noul → 丟掉")
            continue
        noul = max(0.0, min(1.0, noul))
        edges = box_answer.get("box") if isinstance(box_answer, dict) else None
        bbox = _bbox_from_jev_edges(edges)
        pred = _prediction_core(dish, {"confidence": noul, "reason": None}, bbox)
        if noul >= present_min and bbox is not None:
            print(f"[debug] {dish.name}: Noul {noul:.2f} → 留下")
            primaries.append(pred)
        else:
            why = "低於門檻" if noul < present_min else "外框無效"
            print(f"[debug] {dish.name}: Noul {noul:.2f} → 丟掉（{why}）")
            if bbox is not None:
                rejected.append(pred)
    primaries.sort(key=lambda item: float(item["confidence"]), reverse=True)
    return primaries, rejected


def _predictions_from_jev(
    dishes: list[Dish],
    answers: dict,
    present_min: float,
) -> list[dict]:
    """Noul 決定留不留，Box 只提供外框。"""
    primaries, rejected = _collect_jev_hits(dishes, answers, present_min)
    merged = _merge_jev_cells(primaries)
    return _fill_rejected_seconds(merged, rejected)


def _post_jev_decisions(body: dict) -> dict:
    if not API_KEY:
        raise RuntimeError("ZEABUR_AI_HUB_API_KEY is required")
    response = httpx.post(
        JEV_DECISIONS_URL,
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
        },
        json=body,
        timeout=120.0,
    )
    if response.status_code != 200:
        detail = response.text[:300].replace("\n", " ")
        raise RuntimeError(f"Decisions API HTTP {response.status_code}: {detail}")
    try:
        payload = response.json()
    except json.JSONDecodeError as exc:
        raise RuntimeError("Decisions API 回覆不是 JSON") from exc
    if not isinstance(payload, dict) or "answers" not in payload:
        raise RuntimeError("Decisions API 回覆缺少 answers")
    return payload


def recognize_dishes_chat(image_path: Path, dishes: list[Dish]) -> dict:
    """看圖對話：沿用原本的提示詞、對菜單與相近合併。"""
    if not dishes:
        return {"predictions": [], "model": MODEL, "raw": "菜單為空"}

    # 先轉正方向，再送模型（與之後畫框一致）
    _ensure_upright_image(image_path)

    # 圖片轉 base64 字串
    image_b64 = base64.standard_b64encode(image_path.read_bytes()).decode("ascii")

    # 組 prompt → 問模型 → 拿回文字
    raw_text = _ask_model(
        _build_prompt(_dish_catalog(dishes)),
        _guess_media_type(image_path),
        image_b64,
    )
    parsed = _extract_json(raw_text)

    predictions = []
    seen = set()  # 同一道「主預測」只留一次；次佳不佔名額，避免漏掉盤上其他真菜
    for item in parsed.get("predictions", []):
        dish = _match_dish(item, dishes)
        if not dish or dish.id in seen:
            continue
        seen.add(dish.id)
        bbox = _normalize_bbox(item.get("bbox"))
        pred = _prediction_core(dish, item, bbox)
        second = _extract_second_best(
            item.get("alternative"),
            dishes,
            pred["name"],
            dish.id,
            bbox,
        )
        if second:
            pred["second_best"] = second
            # 注意：不可把 second 加入 seen。
            # 否則若 A 的次佳是 B，而 B 也實際出現在別格，B 會被整筆丟掉（terra 漏菜常見原因）。
            alt = _maybe_adopt_alternative(pred["name"], pred["confidence"], second)
            if alt:
                pred["alternative"] = alt
                print(
                    f"[debug] 採用相近候選: "
                    f"{pred['name']} ({pred['confidence']:.2f}) / "
                    f"{alt['name']} ({alt['confidence']:.2f})"
                )
        predictions.append(pred)

    # 信心高的排前面，再合併重疊且信心相近的獨立兩筆
    predictions.sort(key=lambda x: x["confidence"], reverse=True)
    predictions = _merge_overlapping_close_pairs(predictions)
    _nudge_bboxes(predictions)
    print(f"[debug] 主預測共 {len(predictions)} 道: {[p.get('name') for p in predictions]}")
    _debug_print_close_candidates(predictions)
    return {"predictions": predictions, "model": MODEL, "raw": raw_text}


_CHOICE_PAD = 0.02
_CHOICE_STATE = "這是餐盤上的一個食物區域。請只依這個區域判斷。"
_CHOICE_INSTRUCTIONS = "這個區域的主要食物最符合哪一項？依食物本身的外觀判斷。"
_CHOICE_OTHER = "其他"
_CHOICE_OTHER_TEXT = "以上都不是這個區域的主要食物。"
_CHOICE_BLANK = "菜單中的這道菜"


def _expand_choice_box(bbox: list[float]) -> list[float]:
    """裁圖前四邊各外擴一點，並夾在整張圖裡面。"""
    x, y, width, height = [float(value) for value in bbox]
    left = max(0.0, x - _CHOICE_PAD)
    top = max(0.0, y - _CHOICE_PAD)
    right = min(1.0, x + width + _CHOICE_PAD)
    bottom = min(1.0, y + height + _CHOICE_PAD)
    return [
        round(left, 4),
        round(top, 4),
        round(right - left, 4),
        round(bottom - top, 4),
    ]


def _group_choice_regions(primaries: list[dict]) -> list[dict]:
    """重疊的框合成一格，並留下這一格的全部候選。"""
    remaining = [dict(item) for item in primaries]
    regions: list[dict] = []
    while remaining:
        primary = remaining.pop(0)
        candidates = [primary]
        index = 0
        while index < len(remaining):
            other = remaining[index]
            if _bbox_iou(primary.get("bbox"), other.get("bbox")) < JEV_SAME_CELL_IOU:
                index += 1
                continue
            candidates.append(other)
            del remaining[index]
        regions.append(
            {
                "bbox": list(primary["bbox"]),
                "primary": primary,
                "candidates": candidates,
            }
        )
    return regions


def _jpeg_data_url_for_choice_crop(path: Path, bbox: list[float]) -> str:
    """依轉正後的整張圖裁出這一格，再套用 Jev 的 JPEG 尺寸。"""
    x, y, width, height = _expand_choice_box(bbox)
    with Image.open(path) as img:
        rgb = img.convert("RGB")
        image_w, image_h = rgb.size
        left = int(round(x * image_w))
        top = int(round(y * image_h))
        right = int(round((x + width) * image_w))
        bottom = int(round((y + height) * image_h))
        right = min(image_w, max(left + 1, right))
        bottom = min(image_h, max(top + 1, bottom))
        cropped = rgb.crop((left, top, right, bottom))
        fitted = _fit_jev_size(*cropped.size)
        if fitted != cropped.size:
            cropped = cropped.resize(fitted, Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        cropped.save(buffer, format="JPEG", quality=90)
    encoded = base64.standard_b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


def _choice_body(
    path: Path, region: dict, dishes_by_id: dict[int, Dish]
) -> dict:
    """這一格的裁圖，加上候選菜與「其他」。"""
    criteria: dict[str, str] = {}
    for candidate in region["candidates"]:
        dish = dishes_by_id[candidate["dish_id"]]
        appearance = _clip(dish.cooking_method, 200)
        criteria[dish.name] = appearance or _CHOICE_BLANK
    criteria[_CHOICE_OTHER] = _CHOICE_OTHER_TEXT
    return {
        "model": JEV_MODEL,
        "state": [
            _CHOICE_STATE,
            {
                "type": "image_url",
                "image_url": {"url": _jpeg_data_url_for_choice_crop(path, region["bbox"])},
            },
        ],
        "questions": {
            "region": {
                "type": "choice",
                "instructions": _CHOICE_INSTRUCTIONS,
                "criteria": criteria,
            }
        },
    }


def _choice_region_answer(payload: dict) -> dict:
    answers = payload.get("answers") if isinstance(payload, dict) else None
    answer = answers.get("region") if isinstance(answers, dict) else None
    probabilities = answer.get("probabilities") if isinstance(answer, dict) else None
    if not isinstance(answer, dict) or "choice" not in answer or not isinstance(probabilities, dict):
        raise RuntimeError("Decisions API 回覆缺少 answers.region")
    return answer


def _log_choice_probabilities(probabilities: dict) -> None:
    ranked = sorted(
        probabilities.items(),
        key=lambda item: float(item[1]),
        reverse=True,
    )
    text = " / ".join(f"{name} {float(prob):.2f}" for name, prob in ranked)
    print(f"[debug] choice {text}")


def _apply_choice_answer(
    region: dict, answer: dict, dishes_by_id: dict[int, Dish]
) -> dict | None:
    """用選項機率換掉這一格的菜名。選到「其他」就拿掉這一格。"""
    probabilities = answer["probabilities"]
    _log_choice_probabilities(probabilities)
    selected = answer["choice"]
    if selected == _CHOICE_OTHER:
        return None
    by_name = {
        dishes_by_id[candidate["dish_id"]].name: candidate
        for candidate in region["candidates"]
    }
    if selected not in by_name or selected not in probabilities:
        raise RuntimeError("Decisions API 回覆缺少 answers.region")
    dish = dishes_by_id[by_name[selected]["dish_id"]]
    chosen = _prediction_core(
        dish,
        {"confidence": float(probabilities[selected]), "reason": None},
        list(region["bbox"]),
    )
    runners: list[tuple[float, dict]] = []
    for candidate in region["candidates"]:
        name = candidate["name"]
        if name == selected or name not in probabilities:
            continue
        runners.append((float(probabilities[name]), candidate))
    if not runners:
        return chosen
    probability, second = max(runners, key=lambda item: item[0])
    alternate = dict(second)
    alternate["confidence"] = probability
    alternate["reason"] = None
    return _with_second(chosen, alternate)


def _stage1_body(image_path: Path, dishes: list[Dish]) -> dict:
    return {
        "model": JEV_MODEL,
        "state": [
            "餐盤照片。請只依照片判斷。",
            {
                "type": "image_url",
                "image_url": {"url": _jpeg_data_url_for_jev(image_path)},
            },
        ],
        "questions": _jev_questions(dishes),
    }


def recognize_dishes_jev(image_path: Path, dishes: list[Dish]) -> dict:
    """Jev：每道菜問在不在與外框，同一格再合併。"""
    if not dishes:
        return {"predictions": [], "model": JEV_MODEL, "raw": "菜單為空"}

    _ensure_upright_image(image_path)
    payload = _post_jev_decisions(_stage1_body(image_path, dishes))
    predictions = _predictions_from_jev(
        dishes, payload["answers"], NOUL_PRESENT_MIN
    )
    _nudge_bboxes(predictions)
    print(f"[debug] 主預測共 {len(predictions)} 道: {[p.get('name') for p in predictions]}")
    _debug_print_close_candidates(predictions)
    return {"predictions": predictions, "model": JEV_MODEL, "raw": payload}


def recognize_dishes_choice(image_path: Path, dishes: list[Dish]) -> dict:
    """第一段與 Jev 相同。一格有兩道以上候選時，才裁圖做 Choice。"""
    if not dishes:
        return {"predictions": [], "model": JEV_MODEL, "raw": "菜單為空"}

    _ensure_upright_image(image_path)
    stage1 = _post_jev_decisions(_stage1_body(image_path, dishes))
    primaries, rejected = _collect_jev_hits(
        dishes, stage1["answers"], NOUL_PRESENT_MIN
    )
    dishes_by_id = {dish.id: dish for dish in dishes}
    predictions: list[dict] = []
    choice_payloads: list[dict] = []
    for region in _group_choice_regions(primaries):
        if len(region["candidates"]) < 2:
            kept = _fill_rejected_seconds([region["primary"]], rejected)
            print(f"[debug] choice 略過 {kept[0]['name']}，候選只有一道")
            predictions.extend(kept)
            continue
        payload = _post_jev_decisions(
            _choice_body(image_path, region, dishes_by_id)
        )
        choice_payloads.append(payload)
        chosen = _apply_choice_answer(
            region, _choice_region_answer(payload), dishes_by_id
        )
        if chosen is not None:
            predictions.append(chosen)
    predictions.sort(key=lambda item: float(item["confidence"]), reverse=True)
    _nudge_bboxes(predictions)
    print(f"[debug] 主預測共 {len(predictions)} 道: {[p.get('name') for p in predictions]}")
    _debug_print_close_candidates(predictions)
    return {
        "predictions": predictions,
        "model": JEV_MODEL,
        "raw": {"stage1": stage1, "choices": choice_payloads},
    }


def _model_for(engine: InferenceEngine) -> str:
    if engine is InferenceEngine.CHAT:
        return MODEL
    if engine is InferenceEngine.JEV or engine is InferenceEngine.CHOICE:
        return JEV_MODEL
    assert_never(engine)


def recognize_dishes(
    image_path: Path,
    dishes: list[Dish],
    engine: InferenceEngine = InferenceEngine.CHAT,
) -> dict:
    """依引擎辨識。菜單是空的就不打外部 API。"""
    if not dishes:
        return {"predictions": [], "model": _model_for(engine), "raw": "菜單為空"}
    if engine is InferenceEngine.CHAT:
        return recognize_dishes_chat(image_path, dishes)
    if engine is InferenceEngine.JEV:
        return recognize_dishes_jev(image_path, dishes)
    if engine is InferenceEngine.CHOICE:
        return recognize_dishes_choice(image_path, dishes)
    assert_never(engine)
