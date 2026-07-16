"""餐盤圖片辨識：把菜單餵給 Zeabur AI Hub，回傳菜名與信心分數。"""

# --- 標準函式庫 ---
import base64      # 圖片轉成 base64，才能塞進 API 請求
import json        # 解析模型回傳的 JSON
import mimetypes   # 依副檔名判斷 image/jpeg、image/png 等
import os          # 讀取 .env 裡的環境變數
import re          # 從模型文字中抽出 ```json ... ``` 區塊
from pathlib import Path

# --- AI 客戶端（連 Zeabur AI Hub，目前用 OpenAI 格式呼叫 GPT）---
from openai import OpenAI

from .models import Dish

# 從環境變數讀設定（見專案根目錄 .env）
API_KEY = os.getenv("ZEABUR_AI_HUB_API_KEY", "")
BASE_URL = os.getenv("ZEABUR_AI_HUB_BASE_URL", "https://hnd1.aihub.zeabur.ai/")
MODEL = os.getenv("AI_MODEL", "gpt-5.6-luna")


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
4. 顏色相近、同為燉煮/醬燒外觀的菜色（例如醬燒冬瓜與番茄豆包）常被誤併成一道，請依外觀特徵分開判斷。
5. 若畫面明顯只有一道菜，請精準對到最吻合外觀特徵的那一道，不要硬湊其他菜。
6. 一張餐盤也可能有多道菜，請全部列出，不要過早停止；但也不可為了湊數而亂猜。
7. 每道菜給 confidence（0~1 浮點數）；外觀特徵吻合度高才給高分。
8. 每道菜都要給 bbox（大概外框即可）。
9. 若幾乎無法判斷，可回傳空陣列。
10. 只輸出 JSON，不要其他說明文字。

JSON 格式：
{{
  "predictions": [
    {{
      "name": "菜名（必須與候選菜單完全一致）",
      "dish_id": 123,
      "dish_code": 6,
      "confidence": 0.92,
      "reason": "簡短理由（對應到哪個外觀特徵）",
      "bbox": [0.12, 0.20, 0.35, 0.28]
    }}
  ]
}}

bbox 說明（務必提供）：
- 格式 [x, y, w, h]，皆為相對整張圖片的 0～1 小數
- x,y = 外框左上角；w,h = 寬與高
- 框出該道菜「大概」範圍即可，同一格有兩道菜時請分開框
- 座標以整張照片為準（含餐盤外背景），不要只用餐盤內部座標系

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
        max_tokens=2048,
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
    return (res.choices[0].message.content or "").strip()


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
def _normalize_bbox(raw) -> list[float] | None:
    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
        return None
    try:
        x, y, w, h = [float(v) for v in raw]
    except (TypeError, ValueError):
        return None
    # 若模型誤傳成像素很大，粗略當成已是相對座標失敗
    if max(x, y, w, h) > 1.5:
        return None
    x = max(0.0, min(1.0, x))
    y = max(0.0, min(1.0, y))
    w = max(0.0, min(1.0 - x, w))
    h = max(0.0, min(1.0 - y, h))
    if w < 0.02 or h < 0.02: # 太小的框忽略
        return None
    return [round(x, 4), round(y, 4), round(w, 4), round(h, 4)]


# 主流程：圖片 + 菜單 → 呼叫模型 → 整理成 predictions
def recognize_dishes(image_path: Path, dishes: list[Dish]) -> dict:
    if not dishes:
        return {"predictions": [], "model": MODEL, "raw": "菜單為空"}

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
    seen = set()  # 同一道菜只留一次，結果裡只出現一次，避免重複
    for item in parsed.get("predictions", []):
        dish = _match_dish(item, dishes)
        if not dish or dish.id in seen:
            continue
        seen.add(dish.id)
        # confidence 強制夾在 0～1
        conf = max(0.0, min(1.0, float(item.get("confidence", 0))))
        predictions.append(
            {
                "name": dish.name,
                "dish_id": dish.id,
                "dish_code": dish.dish_code,
                "confidence": conf,
                "reason": item.get("reason"),
                "bbox": _normalize_bbox(item.get("bbox")),
            }
        )

    # 信心高的排前面
    predictions.sort(key=lambda x: x["confidence"], reverse=True)
    return {"predictions": predictions, "model": MODEL, "raw": raw_text}
