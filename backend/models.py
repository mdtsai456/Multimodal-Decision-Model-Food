"""菜色資料表。欄位對應 Excel。"""
from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


class Dish(Base):
    """一筆菜色。"""

    __tablename__ = "dishes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    meal_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    dish_code: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)

    composition: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 辨識時，此欄接到 Noul 題目的外觀文字。
    cooking_method: Mapped[str | None] = mapped_column(Text, nullable=True)
    oil_flavor_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 資料庫存 JSON 字串。API 回傳 list。
    photo_files: Mapped[str | None] = mapped_column(Text, nullable=True)
    tfda_name: Mapped[str | None] = mapped_column(String(200), nullable=True)

    # Excel 的營養值含單位，因此以字串儲存。
    calories_100g: Mapped[str | None] = mapped_column(String(50), nullable=True)
    protein_100g: Mapped[str | None] = mapped_column(String(50), nullable=True)
    fat_100g: Mapped[str | None] = mapped_column(String(50), nullable=True)
    carb_100g: Mapped[str | None] = mapped_column(String(50), nullable=True)
    sodium_mg: Mapped[str | None] = mapped_column(String(50), nullable=True)
    potassium_mg: Mapped[str | None] = mapped_column(String(50), nullable=True)
    calcium_mg: Mapped[str | None] = mapped_column(String(50), nullable=True)
    magnesium_mg: Mapped[str | None] = mapped_column(String(50), nullable=True)

    fluffiness: Mapped[str | None] = mapped_column(String(50), nullable=True)
    weight_staple_g: Mapped[float | None] = mapped_column(Float, nullable=True)
    weight_main1_g: Mapped[float | None] = mapped_column(Float, nullable=True)
    weight_main2_g: Mapped[float | None] = mapped_column(Float, nullable=True)
    weight_side1_g: Mapped[float | None] = mapped_column(Float, nullable=True)
    weight_side2_g: Mapped[float | None] = mapped_column(Float, nullable=True)
    weight_side3_g: Mapped[float | None] = mapped_column(Float, nullable=True)
    weight_side4_g: Mapped[float | None] = mapped_column(Float, nullable=True)
    plate_photo: Mapped[str | None] = mapped_column(String(200), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )
