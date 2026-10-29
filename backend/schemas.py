from __future__ import annotations

"""請求／回應資料格式（Pydantic：檢查 API 進出的資料型別）。"""
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class DishBase(BaseModel):
    """菜色共用欄位（對應資料庫／Excel）。"""

    meal_type: Optional[str] = None          # 餐別
    dish_code: Optional[int] = None          # 菜色編號
    name: str                             # 菜名（必填）
    composition: Optional[str] = None        # 組成
    cooking_method: Optional[str] = None     # AI 烹調方式
    oil_flavor_note: Optional[str] = None    # 吸油調味估算
    photo_files: Optional[list[str]] = None  # 照片檔名列表（API 用 list，DB 存 JSON 字串）
    tfda_name: Optional[str] = None          # TFDA 替代名
    calories_100g: Optional[str] = None      # 熱量
    protein_100g: Optional[str] = None       # 粗蛋白
    fat_100g: Optional[str] = None           # 粗脂肪
    carb_100g: Optional[str] = None          # 總碳水
    sodium_mg: Optional[str] = None          # 鈉
    potassium_mg: Optional[str] = None       # 鉀
    calcium_mg: Optional[str] = None         # 鈣
    magnesium_mg: Optional[str] = None       # 鎂
    fluffiness: Optional[str] = None         # 蓬鬆度
    weight_staple_g: Optional[float] = None  # 主食重
    weight_main1_g: Optional[float] = None   # 主菜1重
    weight_main2_g: Optional[float] = None   # 主菜2重
    weight_side1_g: Optional[float] = None   # 副菜1重
    weight_side2_g: Optional[float] = None   # 副菜2重
    weight_side3_g: Optional[float] = None   # 副菜3重
    weight_side4_g: Optional[float] = None   # 副菜4重
    plate_photo: Optional[str] = None        # 完整一盤菜照片
    notes: Optional[str] = None              # 備註


class DishUpdate(BaseModel):
    """編輯菜色用：每個欄位都可選，只傳要改的即可。"""

    meal_type: Optional[str] = None
    dish_code: Optional[int] = None
    name: Optional[str] = None
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


class DishOut(DishBase):
    """回傳給前端的菜色（在 DishBase 上多 id 與時間）。"""

    id: int
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    # 允許直接從 SQLAlchemy 的 Dish 物件轉換
    model_config = {"from_attributes": True}


class AdminLogin(BaseModel):
    """管理員登入請求。"""

    password: str  # 管理密碼


class TokenOut(BaseModel):
    """登入成功回傳。"""

    token: str            # 之後呼叫管理 API 要帶的 Bearer token
    message: str = "ok"   # 提示訊息


class PredictionAlt(BaseModel):
    """信心相近時的次佳候選（與主預測共用 bbox）。"""

    model_config = ConfigDict(extra="ignore")

    name: str
    dish_id: Optional[int] = None
    dish_code: Optional[int] = None
    confidence: float = Field(..., ge=0, le=1)
    reason: Optional[str] = None


class PredictionItem(BaseModel):
    """單一辨識結果（一道菜）。"""

    model_config = ConfigDict(extra="ignore")

    name: str
    dish_id: Optional[int] = None
    dish_code: Optional[int] = None
    confidence: float = Field(..., ge=0, le=1)  # 信心分數，限制在 0～1
    reason: Optional[str] = None                   # 模型給的簡短理由
    bbox: Optional[list[float]] = None             # [x,y,w,h] 相對座標 0～1，大概外框
    alternative: Optional[PredictionAlt] = None    # 信心相近時採用（雙條 UI + result1/2）
    second_best: Optional[PredictionAlt] = None    # 模型次佳（一律回傳，方便 console debug）


class PredictResponse(BaseModel):
    """餐盤辨識整份回傳。"""

    model_config = ConfigDict(extra="ignore")

    predictions: list[PredictionItem]  # 辨識到的菜色列表
    model: str                         # 使用的模型名稱（如 gpt-5.6-luna）
    raw: Optional[Any] = None             # 模型原始文字（除錯用）
    result_dir: Optional[str] = None      # uploads 下的結果資料夾名稱
    close_confidence_delta: Optional[float] = None  # 採用雙結果的信心差門檻


class RecordDishAlt(BaseModel):
    """紀錄詳情中的次佳候選。"""

    dish_id: str = ""
    dish_name: str
    confidence: float = Field(..., ge=0, le=1)
    reason: Optional[str] = None


class RecordDishItem(BaseModel):
    """紀錄詳情中的一道菜。"""

    dish_id: str = ""
    dish_name: str
    confidence: float = Field(..., ge=0, le=1)
    reason: Optional[str] = None
    bbox: Optional[list[float]] = None
    alternative: Optional[RecordDishAlt] = None


class RecordSummary(BaseModel):
    """某日辨識紀錄列表的一筆摘要。"""

    id: str
    date: str
    time: str
    meal_type: str = ""
    dish_count: int = 0
    dish_names: list[str] = Field(default_factory=list)
    has_dual: bool = False
    boxed_url: str


class RecordListOut(BaseModel):
    """依日期列出辨識紀錄。"""

    date: str
    records: list[RecordSummary]


class RecordDetailOut(BaseModel):
    """單筆辨識紀錄詳情（供管理員展開檢視）。"""

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
