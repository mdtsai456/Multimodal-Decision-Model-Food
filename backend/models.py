from __future__ import annotations

"""菜色資料表（對應 Excel 匯入欄位）。"""
from datetime import datetime  # 建立／更新時間戳記
from typing import Optional

# SQLAlchemy 欄位型別
from sqlalchemy import DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column  # 宣告欄位用

from .database import Base  # 所有資料表都要繼承 Base


class Dish(Base):
    """一道菜在資料庫裡的一筆紀錄。"""

    __tablename__ = "dishes"  # 資料表名稱

    # --- 基本識別 ---
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)  # 主鍵（自動產生）
    meal_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)  # 餐別：早／午／晚
    dish_code: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)  # Excel 菜色編號
    name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)  # 實際菜名（必填）

    # --- 描述與 AI 特徵（辨識時會餵給模型）---
    composition: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # 組成（食材）
    cooking_method: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # AI 烹調方式／外觀特徵
    oil_flavor_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # AI 吸油調味估算
    photo_files: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # 對應照片檔名（JSON 字串）
    tfda_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)  # TFDA 替代品名稱

    # --- 每 100g 營養資訊（Excel 多半帶單位，所以用字串存）---
    calories_100g: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)  # 熱量
    protein_100g: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)  # 粗蛋白
    fat_100g: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)  # 粗脂肪
    carb_100g: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)  # 總碳水
    sodium_mg: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)  # 鈉
    potassium_mg: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)  # 鉀
    calcium_mg: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)  # 鈣
    magnesium_mg: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)  # 鎂

    # --- 份量／分類 ---
    fluffiness: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)  # AI 食物蓬鬆度分類
    weight_staple_g: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # 主食重 (g)
    weight_main1_g: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # 主菜1重 (g)
    weight_main2_g: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # 主菜2重 (g)
    weight_side1_g: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # 副菜1重 (g)
    weight_side2_g: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # 副菜2重 (g)
    weight_side3_g: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # 副菜3重 (g)
    weight_side4_g: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # 副菜4重 (g)
    plate_photo: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)  # 完整一盤菜照片檔名
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # 備註

    # --- 系統時間 ---
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)  # 建立時間
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow  # 每次更新自動改時間
    )
