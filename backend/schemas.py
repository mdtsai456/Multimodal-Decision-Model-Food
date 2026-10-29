"""API 請求與回應的資料格式。"""
from typing import Optional
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class DishBase(BaseModel):
    """菜色欄位。photo_files 在 API 是 list。資料庫存 JSON 字串。"""

    meal_type: Optional[str] = None
    dish_code: Optional[int] = None
    name: str
    composition: Optional[str] = None
    cooking_method: Optional[str] = None
    oil_flavor_note: Optional[str] = None
    photo_files: Optional[list[str]] = None
    tfda_name: Optional[str] = None
    calories_100g: Optional[str] = None
    protein_100g: Optional[str] = None
    fat_100g: Optional[str] = None
    carb_100g: Optional[str] = None
    sodium_mg: Optional[str] = None
    potassium_mg: Optional[str] = None
    calcium_mg: Optional[str] = None
    magnesium_mg: Optional[str] = None
    fluffiness: Optional[str] = None
    weight_staple_g: Optional[float] = None
    weight_main1_g: Optional[float] = None
    weight_main2_g: Optional[float] = None
    weight_side1_g: Optional[float] = None
    weight_side2_g: Optional[float] = None
    weight_side3_g: Optional[float] = None
    weight_side4_g: Optional[float] = None
    plate_photo: Optional[str] = None
    notes: Optional[str] = None


class DishUpdate(DishBase):
    """更新菜色。未送出的欄位不修改。"""

    name: Optional[str] = None


class DishOut(DishBase):
    """菜色回應。包含 id 與時間。可從 Dish 物件建立。"""

    id: int
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    model_config = {"from_attributes": True}


class AdminLogin(BaseModel):
    """管理員登入請求。"""

    password: str


class TokenOut(BaseModel):
    """登入成功回應。token 放在 Authorization: Bearer。"""

    token: str
    message: str = "ok"


class PredictionAlt(BaseModel):
    """次佳候選。與主預測使用同一個 bbox。"""

    model_config = ConfigDict(extra="ignore")

    name: str
    dish_id: Optional[int] = None
    dish_code: Optional[int] = None
    confidence: float = Field(..., ge=0, le=1)
    reason: Optional[str] = None


class PredictionItem(PredictionAlt):
    """一道菜的辨識結果。"""

    bbox: Optional[list[float]] = None  # [x, y, w, h]。相對整張圖。範圍是 0 到 1。
    # 信心差在門檻內時填入。前端顯示兩筆。系統寫入 result1 與 result2。
    alternative: Optional[PredictionAlt] = None
    second_best: Optional[PredictionAlt] = None  # 同一格的次佳。只寫入記錄。


class PredictResponse(BaseModel):
    """餐盤辨識回應。"""

    model_config = ConfigDict(extra="ignore")

    predictions: list[PredictionItem]
    model: str
    raw: Optional[Any] = None
    result_dir: Optional[str] = None  # uploads 底下的資料夾名稱。
    close_confidence_delta: Optional[float] = None  # 採用 alternative 的信心差上限。


class PlateRecognitionAlternative(BaseModel):
    """食物攝影機的次佳菜。不另建一筆餐點項目。"""

    model_config = ConfigDict(populate_by_name=True)

    detected_label: str = Field(alias="detectedLabel")
    source_food_code: Optional[str] = Field(default=None, alias="sourceFoodCode")
    confidence: float = Field(..., ge=0, le=1)


class PlateRecognitionItem(PlateRecognitionAlternative):
    """一筆餐點項目。不含 foodId。不含重量。"""

    bbox: Optional[list[float]] = None
    grams: Optional[float] = None
    alternative: Optional[PlateRecognitionAlternative] = None


class PlateRecognitionOut(BaseModel):
    """食物攝影機的辨識結果。成功時可作為分析 outcome。"""

    model_config = ConfigDict(populate_by_name=True)

    succeeded: bool
    error_message: Optional[str] = Field(default=None, alias="errorMessage")
    model_name: Optional[str] = Field(default=None, alias="modelName")
    model_version: Optional[str] = Field(default=None, alias="modelVersion")
    items: list[PlateRecognitionItem] = []
    result_dir: Optional[str] = Field(default=None, alias="resultDir")


class RecordDishAlt(BaseModel):
    """紀錄中的次佳候選。"""

    dish_id: str = ""
    dish_name: str
    confidence: float = Field(..., ge=0, le=1)
    reason: Optional[str] = None


class RecordDishItem(RecordDishAlt):
    """紀錄中的一道菜。"""

    bbox: Optional[list[float]] = None
    alternative: Optional[RecordDishAlt] = None


class RecordSummary(BaseModel):
    """一筆辨識紀錄的摘要。"""

    id: str
    date: str
    time: str
    meal_type: str = ""
    dish_count: int = 0
    dish_names: list[str] = Field(default_factory=list)
    has_dual: bool = False
    boxed_url: str


class RecordListOut(BaseModel):
    """指定日期的辨識紀錄清單。"""

    date: str
    records: list[RecordSummary]


class RecordDetailOut(BaseModel):
    """一筆辨識紀錄的詳情。"""

    id: str
    date: str
    time: str
    meal_type: str = ""
    has_dual: bool = False
    original_url: Optional[str] = None
    boxed_url: str
    boxed1_url: Optional[str] = None
    boxed2_url: Optional[str] = None
    dishes: list[RecordDishItem] = Field(default_factory=list)
