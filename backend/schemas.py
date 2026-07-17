"""請求／回應資料格式（Pydantic：檢查 API 進出的資料型別）。"""
from datetime import datetime
from typing import Any  # raw 欄位可能是任意內容

from pydantic import BaseModel, ConfigDict, Field


class DishBase(BaseModel):
    """菜色共用欄位（對應資料庫／Excel）。"""

    meal_type: str | None = None          # 餐別
    dish_code: int | None = None          # 菜色編號
    name: str                             # 菜名（必填）
    composition: str | None = None        # 組成
    cooking_method: str | None = None     # AI 烹調方式
    oil_flavor_note: str | None = None    # 吸油調味估算
    photo_files: list[str] | None = None  # 照片檔名列表（API 用 list，DB 存 JSON 字串）
    tfda_name: str | None = None          # TFDA 替代名
    calories_100g: str | None = None      # 熱量
    protein_100g: str | None = None       # 粗蛋白
    fat_100g: str | None = None           # 粗脂肪
    carb_100g: str | None = None          # 總碳水
    sodium_mg: str | None = None          # 鈉
    potassium_mg: str | None = None       # 鉀
    calcium_mg: str | None = None         # 鈣
    magnesium_mg: str | None = None       # 鎂
    fluffiness: str | None = None         # 蓬鬆度
    weight_staple_g: float | None = None  # 主食重
    weight_main1_g: float | None = None   # 主菜1重
    weight_main2_g: float | None = None   # 主菜2重
    weight_side1_g: float | None = None   # 副菜1重
    weight_side2_g: float | None = None   # 副菜2重
    weight_side3_g: float | None = None   # 副菜3重
    weight_side4_g: float | None = None   # 副菜4重
    plate_photo: str | None = None        # 完整一盤菜照片
    notes: str | None = None              # 備註


class DishUpdate(BaseModel):
    """編輯菜色用：每個欄位都可選，只傳要改的即可。"""

    meal_type: str | None = None
    dish_code: int | None = None
    name: str | None = None
    composition: str | None = None
    cooking_method: str | None = None
    oil_flavor_note: str | None = None
    photo_files: list[str] | None = None
    tfda_name: str | None = None
    calories_100g: str | None = None
    protein_100g: str | None = None
    fat_100g: str | None = None
    carb_100g: str | None = None
    sodium_mg: str | None = None
    potassium_mg: str | None = None
    calcium_mg: str | None = None
    magnesium_mg: str | None = None
    fluffiness: str | None = None
    weight_staple_g: float | None = None
    weight_main1_g: float | None = None
    weight_main2_g: float | None = None
    weight_side1_g: float | None = None
    weight_side2_g: float | None = None
    weight_side3_g: float | None = None
    weight_side4_g: float | None = None
    plate_photo: str | None = None
    notes: str | None = None


class DishOut(DishBase):
    """回傳給前端的菜色（在 DishBase 上多 id 與時間）。"""

    id: int
    created_at: datetime | None = None
    updated_at: datetime | None = None
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
    dish_id: int | None = None
    dish_code: int | None = None
    confidence: float = Field(..., ge=0, le=1)
    reason: str | None = None


class PredictionItem(BaseModel):
    """單一辨識結果（一道菜）。"""

    model_config = ConfigDict(extra="ignore")

    name: str
    dish_id: int | None = None
    dish_code: int | None = None
    confidence: float = Field(..., ge=0, le=1)  # 信心分數，限制在 0～1
    reason: str | None = None                   # 模型給的簡短理由
    bbox: list[float] | None = None             # [x,y,w,h] 相對座標 0～1，大概外框
    alternative: PredictionAlt | None = None    # 信心相近時的另一候選


class PredictResponse(BaseModel):
    """餐盤辨識整份回傳。"""

    model_config = ConfigDict(extra="ignore")

    predictions: list[PredictionItem]  # 辨識到的菜色列表
    model: str                         # 使用的模型名稱（如 gpt-5.6-luna）
    raw: Any | None = None             # 模型原始文字（除錯用）
    result_dir: str | None = None      # uploads 下的結果資料夾名稱
