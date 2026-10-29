"""將辨識結果轉成食物攝影機使用的 JSON。"""

from typing import Optional
from .schemas import (
    PlateRecognitionAlternative,
    PlateRecognitionItem,
    PlateRecognitionOut,
)


def _source_food_code(value: object) -> Optional[str]:
    if value is None or value == "":
        return None
    return str(value)


def _alternative(raw: Optional[dict]) -> Optional[PlateRecognitionAlternative]:
    if not raw or not raw.get("name"):
        return None
    return PlateRecognitionAlternative(
        detected_label=raw["name"],
        source_food_code=_source_food_code(raw.get("dish_code")),
        confidence=raw["confidence"],
    )


def plate_recognition_body(result: dict, result_dir: Optional[str]) -> dict:
    """辨識成功。grams 為 null。此流程不計算重量。"""
    items = [
        PlateRecognitionItem(
            detected_label=pred["name"],
            source_food_code=_source_food_code(pred.get("dish_code")),
            confidence=pred["confidence"],
            bbox=pred.get("bbox"),
            grams=None,
            alternative=_alternative(pred.get("alternative")),
        )
        for pred in result.get("predictions") or []
    ]
    body = PlateRecognitionOut(
        succeeded=True,
        error_message=None,
        model_name=result.get("model"),
        model_version=None,
        items=items,
        result_dir=result_dir,
    )
    return body.model_dump(by_alias=True)


def plate_recognition_failure(message: str) -> dict:
    """辨識失敗。呼叫端將 errorMessage 寫入失敗原因。"""
    body = PlateRecognitionOut(
        succeeded=False,
        error_message=message,
        model_name=None,
        model_version=None,
        items=[],
        result_dir=None,
    )
    return body.model_dump(by_alias=True)
