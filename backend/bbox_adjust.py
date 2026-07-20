"""bbox Y 軸微調：上排／下排可分開設定（不依賴 AI 套件）。"""

from __future__ import annotations

import os

# 舊版單一變數：若未另設 TOP/BOTTOM，兩邊都沿用此值
_LEGACY = os.getenv("BBOX_Y_SHIFT")
_DEFAULT = float(_LEGACY) if _LEGACY is not None else 0.08
_DEFAULT1 =  0.03

# 正值＝往下，負值＝往上（相對整張圖高度 0~1）
BBOX_TOP_Y_SHIFT = float(os.getenv("BBOX_TOP_Y_SHIFT", str(_DEFAULT1)))
BBOX_BOTTOM_Y_SHIFT = float(os.getenv("BBOX_BOTTOM_Y_SHIFT", str(_DEFAULT)))

# 相容舊程式讀 BBOX_Y_SHIFT（等同整體預設）
BBOX_Y_SHIFT = _DEFAULT

# 舊紀錄遷移標記（result_store 用）
LEGACY_GLOBAL_Y_SHIFT = 0.12
BBOX_SHIFT_VERSION = 2


def _apply_shift(x: float, y: float, bw: float, bh: float, shift: float) -> list[float]:
    """套用 Y 位移；往下時略加高，往上時只平移。"""
    y2 = y + shift
    bh2 = bh + shift * 0.5 if shift > 0 else bh
    y2 = max(0.0, min(1.0 - 0.02, y2))
    bh2 = max(0.02, min(1.0 - y2, bh2))
    if bw < 0.02:
        return [round(x, 4), round(y, 4), round(bw, 4), round(bh, 4)]
    return [round(x, 4), round(y2, 4), round(bw, 4), round(bh2, 4)]


def nudge_bboxes(predictions: list[dict]) -> None:
    """
    依框中心 y 分成上排／下排，分別套用 BBOX_TOP_Y_SHIFT / BBOX_BOTTOM_Y_SHIFT。
    中心 y < 0.5 → 上排；其餘 → 下排。
    """
    for p in predictions:
        box = p.get("bbox")
        if not isinstance(box, (list, tuple)) or len(box) != 4:
            continue
        try:
            x, y, bw, bh = [float(v) for v in box]
        except (TypeError, ValueError):
            continue
        cy = y + bh / 2
        shift = BBOX_TOP_Y_SHIFT if cy < 0.5 else BBOX_BOTTOM_Y_SHIFT
        if abs(shift) < 1e-9:
            continue
        p["bbox"] = _apply_shift(x, y, bw, bh, shift)


def denudge_top_bboxes(items: list[dict]) -> bool:
    """還原舊版「全體下移」對上排的偏移（載入舊紀錄用）。"""
    shift = max(0.0, LEGACY_GLOBAL_Y_SHIFT)
    changed = False
    for p in items:
        box = p.get("bbox")
        if not isinstance(box, (list, tuple)) or len(box) != 4:
            continue
        try:
            x, y, bw, bh = [float(v) for v in box]
        except (TypeError, ValueError):
            continue
        cy = y + bh / 2
        if cy >= 0.55:
            continue
        y2 = max(0.0, y - shift)
        bh2 = max(0.02, bh - shift * 0.5)
        if y2 + bh2 > 1.0:
            bh2 = max(0.02, 1.0 - y2)
        if abs(y2 - y) < 1e-6 and abs(bh2 - bh) < 1e-6:
            continue
        p["bbox"] = [round(x, 4), round(y2, 4), round(bw, 4), round(bh2, 4)]
        changed = True
    return changed
