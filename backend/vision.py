"""餐盤圖片辨識。依菜單回傳菜名與信心分數。"""

import base64
import io
import json
import mimetypes
import os
import re
from enum import Enum
from pathlib import Path
from typing import assert_never

import httpx
from openai import OpenAI
from PIL import Image, ImageOps

from .bbox_adjust import (
    BBOX_BOTTOM_Y_SHIFT,
    BBOX_TOP_Y_SHIFT,
    nudge_bboxes as _nudge_bboxes,
)
from .models import Dish

API_KEY = os.getenv("ZEABUR_AI_HUB_API_KEY", "")
BASE_URL = os.getenv("ZEABUR_AI_HUB_BASE_URL", "https://hnd1.aihub.zeabur.ai/")
MODEL = os.getenv("AI_MODEL", "gpt-5.6-luna")
# 兩道菜的信心差小於或等於此次值時，前端顯示兩筆。系統寫入 result1 與 result2。
CLOSE_CONFIDENCE_DELTA = float(os.getenv("CLOSE_CONFIDENCE_DELTA", "0.30"))
JEV_MODEL = os.getenv("JEV_MODEL", "typesafe/jev-1.14")
JEV_DECISIONS_URL = os.getenv(
    "JEV_DECISIONS_URL", "https://openrouter.ai/api/alpha/decisions"
)
NOUL_PRESENT_MIN = float(os.getenv("NOUL_PRESENT_MIN", "0.50"))
INFERENCE_ENGINE = os.getenv("INFERENCE_ENGINE", "chat")
# IoU 大於或等於此次值時，兩框視為同一格食物。
JEV_SAME_CELL_IOU = 0.45
print(
    f"[config] INFERENCE_ENGINE={INFERENCE_ENGINE} AI_MODEL={MODEL} "
    f"JEV_MODEL={JEV_MODEL} NOUL_PRESENT_MIN={NOUL_PRESENT_MIN} "
    f"CLOSE_CONFIDENCE_DELTA={CLOSE_CONFIDENCE_DELTA} "
    f"BBOX_TOP_Y_SHIFT={BBOX_TOP_Y_SHIFT} BBOX_BOTTOM_Y_SHIFT={BBOX_BOTTOM_Y_SHIFT}"
)


class InferenceEngine(str, Enum):
    """辨識引擎。CHAT 使用看圖對話。JEV 使用 Jev。"""

    CHAT = "chat"
    JEV = "jev"


def resolve_engine(override: str | None) -> InferenceEngine:
    """表單有值時使用表單。否則使用 INFERENCE_ENGINE。空白為 chat。"""
    raw = override if override is not None else os.getenv("INFERENCE_ENGINE", "")
    text = (raw or "").strip().lower()
    if not text:
        return InferenceEngine.CHAT
    try:
        return InferenceEngine(text)
    except ValueError as exc:
        raise ValueError("engine 只接受 chat 或 jev") from exc


def _guess_media_type(path: Path) -> str:
    """由檔名取得 MIME。無法判斷時回傳 image/jpeg。"""
    mime, _ = mimetypes.guess_type(str(path))
    if mime in {"image/jpeg", "image/png", "image/gif", "image/webp"}:
        return mime
    return "image/jpeg"


def _clip(text: str | None, max_len: int = 420) -> str:
    """將文字收成一行。長度超過 max_len 時截斷。"""
    if not text:
        return ""
    cleaned = " ".join(str(text).split())
    return cleaned if len(cleaned) <= max_len else cleaned[: max_len - 1] + "…"


def _dish_catalog(dishes: list[Dish]) -> str:
    """將菜單編成文字。此文字是模型的候選清單。"""
    blocks = []
    for d in dishes:
        parts = [
            f"【{d.name}】",
            f"id={d.id}",
            f"code={d.dish_code}",
            f"餐別={d.meal_type or ''}",
            f"組成={d.composition or ''}",
        ]
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


def _extract_json(text: str) -> dict:
    """從模型文字取出 JSON。先解析全文。失敗時解析程式碼區塊。再失敗時解析第一個 { 到最後一個 }。"""
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fenced:
        try:
            return json.loads(fenced.group(1).strip())
        except json.JSONDecodeError:
            pass

    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        return json.loads(text[start : end + 1])
    raise ValueError("模型回應無法解析為 JSON")


def _build_prompt(catalog: str) -> str:
    """組合模型指示與候選菜單。"""
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


def _ask_model(prompt: str, media_type: str, image_b64: str) -> str:
    """以 OpenAI 相容格式送出文字與圖片。"""
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


def _match_dish(item: dict, dishes: list[Dish]) -> Dish | None:
    """將一筆預測對到菜單。順序是 id、完整菜名、名稱包含。"""
    by_id = {d.id: d for d in dishes}
    by_name = {d.name: d for d in dishes}
    dish_id = item.get("dish_id")
    name = (item.get("name") or "").strip()

    if isinstance(dish_id, int) and dish_id in by_id:
        return by_id[dish_id]
    if name in by_name:
        return by_name[name]
    for d in dishes:
        if name and (name in d.name or d.name in name):
            return d
    return None


def _normalize_bbox(raw) -> list[float] | None:
    """將 bbox 轉成 [x, y, w, h]。範圍是 0 到 1。無效時回傳 None。
    優先接受 [x1, y1, x2, y2]。不符合角點時，改以 [x, y, w, h] 解讀。
    """
    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
        return None
    try:
        a, b, c, d = [float(v) for v in raw]
    except (TypeError, ValueError):
        return None
    if max(a, b, c, d) > 1.5:
        return None

    if c > a and d > b:
        x1, y1, x2, y2 = a, b, c, d
    else:
        x1, y1, x2, y2 = a, b, a + c, b + d

    x1 = max(0.0, min(1.0, x1))
    y1 = max(0.0, min(1.0, y1))
    x2 = max(0.0, min(1.0, x2))
    y2 = max(0.0, min(1.0, y2))
    if x2 <= x1 or y2 <= y1:
        return None
    w = x2 - x1
    h = y2 - y1
    if w < 0.02 or h < 0.02:
        return None
    return [round(x1, 4), round(y1, 4), round(w, 4), round(h, 4)]


def _ensure_upright_image(path: Path) -> None:
    """依 EXIF 轉正並覆寫檔案。模型與畫框使用同一像素方向。"""
    with Image.open(path) as img:
        oriented = ImageOps.exif_transpose(img)
        rgb = oriented.convert("RGB")
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
    """讀取模型的次佳候選。不檢查信心差。結果寫入 second_best。"""
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
    return _second_view(_prediction_core(alt_dish, raw_alt, bbox))


def _maybe_adopt_alternative(
    primary_name: str,
    primary_conf: float,
    second: dict | None,
) -> dict | None:
    """信心差小於或等於 CLOSE_CONFIDENCE_DELTA 時，回傳 alternative。"""
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
    """計算 IoU。IoU 是交集面積除以聯集面積。範圍是 0 到 1。框格式是 [x, y, w, h]。"""
    if not a or not b or len(a) != 4 or len(b) != 4:
        return 0.0
    ax1, ay1 = a[0], a[1]
    ax2, ay2 = a[0] + a[2], a[1] + a[3]
    bx1, by1 = b[0], b[1]
    bx2, by2 = b[0] + b[2], b[1] + b[3]
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    union = a[2] * a[3] + b[2] * b[3] - inter
    return inter / union if union > 0 else 0.0


def _merge_overlapping_close_pairs(predictions: list[dict]) -> list[dict]:
    """chat 路徑的合併。IoU 大於或等於 JEV_SAME_CELL_IOU，且信心差在門檻內時，合成一筆。"""
    if len(predictions) < 2:
        return predictions

    used = set()
    merged: list[dict] = []
    for i, a in enumerate(predictions):
        if i in used:
            continue
        pair_j = None
        for j in range(i + 1, len(predictions)):
            if j in used:
                continue
            b = predictions[j]
            if a.get("alternative") or b.get("alternative"):
                continue
            if abs(a["confidence"] - b["confidence"]) > CLOSE_CONFIDENCE_DELTA:
                continue
            if _bbox_iou(a.get("bbox"), b.get("bbox")) < JEV_SAME_CELL_IOU:
                continue
            pair_j = j
            break

        if pair_j is None:
            merged.append(a)
            continue

        b = predictions[pair_j]
        used.add(pair_j)
        primary, secondary = (a, b) if a["confidence"] >= b["confidence"] else (b, a)
        primary = dict(primary)
        second = _second_view(secondary)
        primary["second_best"] = second
        primary["alternative"] = second
        print(
            f"[debug] bbox 合併相近候選: "
            f"{primary['name']} ({primary['confidence']:.2f}) / "
            f"{secondary['name']} ({secondary['confidence']:.2f})"
        )
        merged.append(primary)

    merged.sort(key=lambda x: x["confidence"], reverse=True)
    return merged


def _debug_print_close_candidates(predictions: list[dict]) -> None:
    """印出每道菜的主結果、次佳，以及是否寫入 alternative。"""
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
    """將寬與高限制在 256 到 1568。將總像素限制在 78400 到 2458624。"""
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
    """轉成 Jev 接受的 JPEG data URL。座標相對轉正後的圖片。"""
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
    """每道菜有一題 Noul 與一題 Box。題目鍵使用資料庫 id。"""
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
    """將 Jev 的 left、top、right、bottom 轉成 [x, y, w, h]。"""
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
    """寫入 second_best。信心差在門檻內時，同時寫入 alternative。"""
    merged = dict(primary)
    second = _second_view(other)
    merged["second_best"] = second
    delta = abs(float(merged["confidence"]) - float(other["confidence"]))
    if delta <= CLOSE_CONFIDENCE_DELTA:
        merged["alternative"] = second
    return merged


def _merge_jev_cells(primaries: list[dict]) -> list[dict]:
    """IoU 大於或等於 JEV_SAME_CELL_IOU 時合成同一格。不檢查信心差。"""
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
    """主結果沒有 second_best 時，從未過門檻且 IoU 足夠的菜補上。"""
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


def _predictions_from_jev(
    dishes: list[Dish],
    answers: dict,
    present_min: float,
) -> list[dict]:
    """Noul 決定菜色是否留下。Box 只提供外框。"""
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


def _finished(predictions: list[dict], model: str, raw) -> dict:
    _nudge_bboxes(predictions)
    print(f"[debug] 主預測共 {len(predictions)} 道: {[p.get('name') for p in predictions]}")
    _debug_print_close_candidates(predictions)
    return {"predictions": predictions, "model": model, "raw": raw}


def recognize_dishes_chat(image_path: Path, dishes: list[Dish]) -> dict:
    """chat 路徑。使用提示詞、菜單對應，以及信心差合併。"""
    if not dishes:
        return {"predictions": [], "model": MODEL, "raw": "菜單為空"}

    _ensure_upright_image(image_path)

    image_b64 = base64.standard_b64encode(image_path.read_bytes()).decode("ascii")

    raw_text = _ask_model(
        _build_prompt(_dish_catalog(dishes)),
        _guess_media_type(image_path),
        image_b64,
    )
    parsed = _extract_json(raw_text)

    predictions = []
    seen = set()  # 只記錄主預測的菜色 id。次佳不加入此集合。
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
            # 次佳的菜色 id 不加入 seen。該菜色出現在另一格時仍要保留。
            alt = _maybe_adopt_alternative(pred["name"], pred["confidence"], second)
            if alt:
                pred["alternative"] = alt
                print(
                    f"[debug] 採用相近候選: "
                    f"{pred['name']} ({pred['confidence']:.2f}) / "
                    f"{alt['name']} ({alt['confidence']:.2f})"
                )
        predictions.append(pred)

    predictions.sort(key=lambda x: x["confidence"], reverse=True)
    predictions = _merge_overlapping_close_pairs(predictions)
    return _finished(predictions, MODEL, raw_text)


def recognize_dishes_jev(image_path: Path, dishes: list[Dish]) -> dict:
    """Jev 路徑。每道菜詢問是否存在與外框。同一格再合併。"""
    if not dishes:
        return {"predictions": [], "model": JEV_MODEL, "raw": "菜單為空"}

    _ensure_upright_image(image_path)
    body = {
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
    payload = _post_jev_decisions(body)
    predictions = _predictions_from_jev(
        dishes, payload["answers"], NOUL_PRESENT_MIN
    )
    return _finished(predictions, JEV_MODEL, payload)


def _model_for(engine: InferenceEngine) -> str:
    if engine is InferenceEngine.CHAT:
        return MODEL
    if engine is InferenceEngine.JEV:
        return JEV_MODEL
    assert_never(engine)


def recognize_dishes(
    image_path: Path,
    dishes: list[Dish],
    engine: InferenceEngine = InferenceEngine.CHAT,
) -> dict:
    """依引擎辨識。菜單為空時不呼叫外部 API。"""
    if not dishes:
        return {"predictions": [], "model": _model_for(engine), "raw": "菜單為空"}
    if engine is InferenceEngine.CHAT:
        return recognize_dishes_chat(image_path, dishes)
    if engine is InferenceEngine.JEV:
        return recognize_dishes_jev(image_path, dishes)
    assert_never(engine)
