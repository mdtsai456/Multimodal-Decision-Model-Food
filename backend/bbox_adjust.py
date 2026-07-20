"""bbox Y 軸微調：上排／下排可分開設定。"""

from __future__ import annotations

import os

# 舊版單一變數：若未另設 TOP/BOTTOM，兩邊都沿用此值

_DEFAULT = 0.03
_DEFAULT1 =  0.01

# 正值＝往下，負值＝往上（相對整張圖高度 0~1）
BBOX_TOP_Y_SHIFT = _DEFAULT1 # 上排 Y 軸位移
BBOX_BOTTOM_Y_SHIFT = _DEFAULT # 下排 Y 軸位移


# 舊紀錄遷移標記（result_store 用）
LEGACY_GLOBAL_Y_SHIFT = 0.12
# 存進 result.json 的版本號；舊檔 < 此值時才做 denudge
BBOX_SHIFT_VERSION = 2

# 相容舊程式讀 BBOX_Y_SHIFT（整體預設）
BBOX_Y_SHIFT = _DEFAULT


def _apply_shift(x: float, y: float, bw: float, bh: float, shift: float) -> list[float]:
    """套用 Y 位移；往下時略加高，往上時只平移。"""
    # x,y = 框左上角；bw,bh = 寬高（都是 0~1）
    # shift：正＝往下，負＝往上
    y2 = y + shift # 上緣先上下移

    # 往下：高度再加一點，框比較蓋得住食物
    # 往上：只平移，高度不變
    bh2 = bh + shift * 0.5 if shift > 0 else bh

    y2 = max(0.0, min(1.0 - 0.02, y2)) # 上緣不要超出圖頂／圖底
    bh2 = max(0.02, min(1.0 - y2, bh2)) # 高度不能太矮，下緣也不能超出圖底

    if bw < 0.02: # 寬度太窄
        return [round(x, 4), round(y, 4), round(bw, 4), round(bh, 4)]
    return [round(x, 4), round(y2, 4), round(bw, 4), round(bh2, 4)]


def nudge_bboxes(predictions: list[dict]) -> None:
    """
    依框中心 y 分成上排／下排，分別套用 BBOX_TOP_Y_SHIFT / BBOX_BOTTOM_Y_SHIFT。
    中心 y < 0.5 → 上排；其餘 → 下排。
    """
    for p in predictions:
        box = p.get("bbox")  # 拿出這道菜的框 [x, y, 寬, 高]
        if not isinstance(box, (list, tuple)) or len(box) != 4: # 沒框或格式不對，跳過
            continue
        try:
            x, y, bw, bh = [float(v) for v in box]
        except (TypeError, ValueError):
            continue
        cy = y + bh / 2 # 算框的垂直中心（0=最上面，1=最下面）
        shift = BBOX_TOP_Y_SHIFT if cy < 0.5 else BBOX_BOTTOM_Y_SHIFT # 中心在圖上半 → 當上排；否則當下排
        if abs(shift) < 1e-9:
            continue
        p["bbox"] = _apply_shift(x, y, bw, bh, shift) # 套用位移，更新回去


def denudge_top_bboxes(items: list[dict]) -> bool:
    """還原舊版「全體下移」對上排的偏移（載入舊紀錄用；新存檔不會走到）。"""
     # 用途：載入「舊紀錄」時，把上排框拉回正確位置
    shift = max(0.0, LEGACY_GLOBAL_Y_SHIFT)
    changed = False # 有沒有改過任何一筆
    for p in items:
        box = p.get("bbox")  # 拿出這道菜的框 [x, y, 寬, 高]
        if not isinstance(box, (list, tuple)) or len(box) != 4: # 沒框或格式不對，跳過
            continue
        try:
            x, y, bw, bh = [float(v) for v in box]
        except (TypeError, ValueError):
            continue
        cy = y + bh / 2 # 算框的垂直中心（0=最上面，1=最下面）
        if cy >= 0.55: # 偏下的當下排，不還原；只修上排
            continue
        y2 = max(0.0, y - shift) # 上緣往回上移
        bh2 = max(0.02, bh - shift * 0.5) # 高度也扣回當初多加的部分
        if y2 + bh2 > 1.0:
            bh2 = max(0.02, 1.0 - y2)
        if abs(y2 - y) < 1e-6 and abs(bh2 - bh) < 1e-6:
            continue
        p["bbox"] = [round(x, 4), round(y2, 4), round(bw, 4), round(bh2, 4)]
        changed = True
    return changed
