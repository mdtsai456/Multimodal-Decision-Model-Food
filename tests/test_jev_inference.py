"""Jev 推論的純邏輯：門檻、外框、同一格合併、引擎選擇。"""

import os
import unittest
from pathlib import Path
from unittest import mock

from backend.models import Dish
from backend.vision import (
    InferenceEngine,
    _fit_jev_size,
    _predictions_from_jev,
    recognize_dishes,
    resolve_engine,
)


def _dish(dish_id: int, name: str, code: int) -> Dish:
    return Dish(id=dish_id, name=name, dish_code=code, cooking_method="清炒、綠色")


def _answers(dish_id: int, noul: float, box: dict) -> dict:
    return {
        f"present_{dish_id}": {"type": "noul", "noul": noul},
        f"box_{dish_id}": {
            "type": "box",
            "box": box,
            "confidence": 0.99,
        },
    }


SAME = {"left": 0.10, "top": 0.10, "right": 0.55, "bottom": 0.55}
OTHER = {"left": 0.60, "top": 0.60, "right": 0.95, "bottom": 0.95}


class EngineSelectionTest(unittest.TestCase):
    def test_default_is_chat(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("INFERENCE_ENGINE", None)
            self.assertEqual(resolve_engine(None), InferenceEngine.CHAT)
            self.assertEqual(resolve_engine("  "), InferenceEngine.CHAT)

    def test_form_overrides_env(self):
        with mock.patch.dict(os.environ, {"INFERENCE_ENGINE": "chat"}):
            self.assertEqual(resolve_engine("jev"), InferenceEngine.JEV)

    def test_unknown_engine_rejected(self):
        with self.assertRaises(ValueError):
            resolve_engine("nope")


class JevSizeTest(unittest.TestCase):
    def test_large_photo_shrinks_inside_limits(self):
        width, height = _fit_jev_size(4000, 3000)
        self.assertLessEqual(max(width, height), 1568)
        self.assertLessEqual(width * height, 2_458_624)

    def test_tiny_photo_grows_to_minimum(self):
        width, height = _fit_jev_size(100, 100)
        self.assertGreaterEqual(min(width, height), 256)
        self.assertGreaterEqual(width * height, 78_400)


class JevPredictionsTest(unittest.TestCase):
    def setUp(self):
        self.greens = _dish(1, "炒青菜", 11)
        self.cabbage = _dish(2, "炒高麗菜", 12)
        self.pork = _dish(3, "泰式豬肉片", 13)

    def test_below_threshold_is_dropped(self):
        answers = _answers(1, 0.40, SAME)
        preds = _predictions_from_jev([self.greens], answers, present_min=0.50)
        self.assertEqual(preds, [])

    def test_same_cell_far_scores_keep_only_primary(self):
        answers = {}
        answers.update(_answers(1, 0.88, SAME))
        answers.update(_answers(2, 0.51, SAME))
        preds = _predictions_from_jev(
            [self.greens, self.cabbage], answers, present_min=0.50
        )
        self.assertEqual(len(preds), 1)
        self.assertEqual(preds[0]["name"], "炒青菜")
        self.assertEqual(preds[0]["second_best"]["name"], "炒高麗菜")
        self.assertNotIn("alternative", preds[0])
        self.assertIsNone(preds[0]["reason"])

    def test_same_cell_close_scores_show_both(self):
        answers = {}
        answers.update(_answers(1, 0.72, SAME))
        answers.update(_answers(2, 0.61, SAME))
        preds = _predictions_from_jev(
            [self.greens, self.cabbage], answers, present_min=0.50
        )
        self.assertEqual(len(preds), 1)
        self.assertEqual(preds[0]["alternative"]["name"], "炒高麗菜")
        self.assertEqual(preds[0]["second_best"]["name"], "炒高麗菜")

    def test_separate_cells_stay_separate(self):
        answers = {}
        answers.update(_answers(1, 0.90, SAME))
        answers.update(_answers(3, 0.80, OTHER))
        preds = _predictions_from_jev(
            [self.greens, self.pork], answers, present_min=0.50
        )
        self.assertEqual([p["name"] for p in preds], ["炒青菜", "泰式豬肉片"])

    def test_three_overlapping_collapse_to_one(self):
        soup = _dish(4, "味噌湯", 14)
        answers = {}
        answers.update(_answers(1, 0.90, SAME))
        answers.update(_answers(2, 0.70, SAME))
        answers.update(_answers(4, 0.60, SAME))
        preds = _predictions_from_jev(
            [self.greens, self.cabbage, soup], answers, present_min=0.50
        )
        self.assertEqual(len(preds), 1)
        self.assertEqual(preds[0]["name"], "炒青菜")
        self.assertEqual(preds[0]["second_best"]["name"], "炒高麗菜")

    def test_rejected_neighbor_can_become_second_best(self):
        answers = {}
        answers.update(_answers(1, 0.60, SAME))
        answers.update(_answers(2, 0.40, SAME))
        preds = _predictions_from_jev(
            [self.greens, self.cabbage], answers, present_min=0.50
        )
        self.assertEqual(len(preds), 1)
        self.assertEqual(preds[0]["second_best"]["name"], "炒高麗菜")
        self.assertEqual(preds[0]["alternative"]["name"], "炒高麗菜")

    def test_box_is_relative_xywh(self):
        preds = _predictions_from_jev(
            [self.greens], _answers(1, 0.80, SAME), present_min=0.50
        )
        self.assertEqual(preds[0]["bbox"], [0.1, 0.1, 0.45, 0.45])


class EmptyMenuTest(unittest.TestCase):
    def test_jev_skips_remote_call(self):
        result = recognize_dishes(Path("missing.jpg"), [], InferenceEngine.JEV)
        self.assertEqual(result["predictions"], [])
        self.assertEqual(result["model"], "typesafe/jev-1.14")
        self.assertEqual(result["raw"], "菜單為空")


if __name__ == "__main__":
    unittest.main()
