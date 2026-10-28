"""bbox 的 Y 軸位移。上排與下排使用不同數值。"""

from __future__ import annotations

# 正值使框往下。負值使框往上。數值相對整張圖高度，範圍是 0 到 1。
BBOX_TOP_Y_SHIFT = 0.01
BBOX_BOTTOM_Y_SHIFT = 0.08

# 舊紀錄使用的全體下移量。result_store 載入舊檔時使用。
LEGACY_GLOBAL_Y_SHIFT = 0.12
# result.json 的版本。版本小於此次值時，還原上排位移。
BBOX_SHIFT_VERSION = 2


def _apply_shift(x: float, y: float, bw: float, bh: float, shift: float) -> list[float]:
    """套用 Y 位移。shift 大於 0 時，高度增加 shift 的一半。shift 小於 0 時，只移動上緣。"""
    y2 = y + shift

    bh2 = bh + shift * 0.5 if shift > 0 else bh

    y2 = max(0.0, min(1.0 - 0.02, y2))
    bh2 = max(0.02, min(1.0 - y2, bh2))

    if bw < 0.02:
        return [round(x, 4), round(y, 4), round(bw, 4), round(bh, 4)]
    return [round(x, 4), round(y2, 4), round(bw, 4), round(bh2, 4)]


def nudge_bboxes(predictions: list[dict]) -> None:
    """框中心 y 小於 0.5 時使用 BBOX_TOP_Y_SHIFT。其餘使用 BBOX_BOTTOM_Y_SHIFT。"""
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
    """載入舊紀錄時，取消上排的舊版下移。新存檔不呼叫此函式。"""
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
