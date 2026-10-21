"""Choice 路徑：第一段沿用 Noul 與 Box，重疊格才打第二段。"""

import io
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image
from fastapi.testclient import TestClient

from backend.main import app
from backend.models import Dish
from backend.vision import (
    InferenceEngine,
    _expand_choice_box,
    recognize_dishes,
    resolve_engine,
)
from tests.test_jev_inference import OTHER, SAME, _answers, _dish


TOP = {"left": 0.10, "top": 0.05, "right": 0.40, "bottom": 0.30}
MID = {"left": 0.55, "top": 0.05, "right": 0.90, "bottom": 0.30}
BOT = {"left": 0.10, "top": 0.60, "right": 0.40, "bottom": 0.90}


def _plate(folder: Path) -> Path:
    path = folder / "plate.jpg"
    Image.new("RGB", (400, 400), (240, 240, 240)).save(path, format="JPEG")
    return path


def _choice_payload(name: str, probabilities: dict, confidence: float = 0.99) -> dict:
    return {
        "answers": {
            "region": {
                "type": "choice",
                "choice": name,
                "confidence": confidence,
                "probabilities": probabilities,
            }
        }
    }


class ChoiceEngineTest(unittest.TestCase):
    def test_choice_is_a_valid_engine(self):
        self.assertEqual(resolve_engine("choice"), InferenceEngine.CHOICE)

    def test_unknown_engine_still_rejected(self):
        with self.assertRaises(ValueError):
            resolve_engine("nope")


class ChoiceBoxTest(unittest.TestCase):
    def test_padding_stays_inside_the_image(self):
        self.assertEqual(_expand_choice_box([0.0, 0.0, 0.1, 0.1]), [0.0, 0.0, 0.12, 0.12])
        self.assertEqual(_expand_choice_box([0.5, 0.5, 0.5, 0.5]), [0.48, 0.48, 0.52, 0.52])


class ChoicePathTest(unittest.TestCase):
    def setUp(self):
        self.folder = Path(__file__).resolve().parent / "_tmp_choice"
        self.folder.mkdir(exist_ok=True)
        self.image = _plate(self.folder)
        self.mushroom = Dish(
            id=8, name="黑胡椒杏鮑菇", dish_code=8, cooking_method="厚實菇肉"
        )
        self.tofu = Dish(id=6, name="番茄豆包", dish_code=6, cooking_method="皺褶豆皮")
        self.melon = Dish(id=7, name="醬燒冬瓜", dish_code=7, cooking_method=None)
        self.calls: list[dict] = []

    def tearDown(self):
        if self.image.exists():
            self.image.unlink()

    def _post(self, stage1: dict, choice_payloads: list[dict]):
        pending = list(choice_payloads)

        def post(body: dict) -> dict:
            self.calls.append(body)
            if "region" in body["questions"]:
                if not pending:
                    raise AssertionError("多打了 Choice")
                item = pending.pop(0)
                if isinstance(item, Exception):
                    raise item
                return item
            return {"answers": stage1}

        return post

    def test_jev_overlapping_pair_does_not_ask_choice(self):
        stage1 = {}
        stage1.update(_answers(8, 0.82, SAME))
        stage1.update(_answers(6, 0.69, SAME))
        post = self._post(stage1, [])
        with mock.patch("backend.vision._post_jev_decisions", side_effect=post):
            result = recognize_dishes(
                self.image, [self.mushroom, self.tofu], InferenceEngine.JEV
            )
        self.assertEqual(len(self.calls), 1)
        self.assertNotIn("region", self.calls[0]["questions"])
        self.assertEqual(result["predictions"][0]["name"], "黑胡椒杏鮑菇")
        self.assertEqual(result["predictions"][0]["confidence"], 0.82)

    def test_single_candidate_matches_jev_and_skips_choice(self):
        greens = _dish(1, "炒青菜", 11)
        stage1 = _answers(1, 0.80, SAME)
        post = self._post(stage1, [])
        with mock.patch("backend.vision._post_jev_decisions", side_effect=post):
            jev = recognize_dishes(self.image, [greens], InferenceEngine.JEV)
            choice = recognize_dishes(self.image, [greens], InferenceEngine.CHOICE)
        self.assertEqual(choice["predictions"], jev["predictions"])
        self.assertEqual(choice["model"], jev["model"])
        self.assertEqual(len(self.calls), 2)
        self.assertTrue(all("region" not in call["questions"] for call in self.calls))

    def test_overlapping_pair_uses_choice_probability(self):
        stage1 = {}
        stage1.update(_answers(8, 0.82, SAME))
        stage1.update(_answers(6, 0.69, SAME))
        chosen = _choice_payload(
            "黑胡椒杏鮑菇",
            {"黑胡椒杏鮑菇": 0.86, "番茄豆包": 0.11, "其他": 0.03},
        )
        post = self._post(stage1, [chosen])
        with mock.patch("backend.vision._post_jev_decisions", side_effect=post):
            result = recognize_dishes(
                self.image, [self.mushroom, self.tofu], InferenceEngine.CHOICE
            )
        self.assertEqual(len(result["predictions"]), 1)
        pred = result["predictions"][0]
        self.assertEqual(pred["name"], "黑胡椒杏鮑菇")
        self.assertEqual(pred["dish_id"], 8)
        self.assertEqual(pred["confidence"], 0.86)
        self.assertEqual(pred["second_best"]["name"], "番茄豆包")
        self.assertEqual(pred["second_best"]["confidence"], 0.11)
        self.assertNotIn("alternative", pred)
        self.assertIsNone(pred["reason"])
        criteria = self.calls[1]["questions"]["region"]["criteria"]
        self.assertEqual(set(criteria), {"黑胡椒杏鮑菇", "番茄豆包", "其他"})
        self.assertEqual(criteria["黑胡椒杏鮑菇"], "厚實菇肉")
        self.assertEqual(criteria["其他"], "以上都不是這個區域的主要食物。")
        self.assertEqual(
            self.calls[1]["questions"]["region"]["instructions"],
            "這個區域的主要食物最符合哪一項？依食物本身的外觀判斷。",
        )
        self.assertEqual(
            self.calls[1]["state"][0],
            "這是餐盤上的一個食物區域。請只依這個區域判斷。",
        )

    def test_close_choice_probabilities_show_both(self):
        stage1 = {}
        stage1.update(_answers(8, 0.82, SAME))
        stage1.update(_answers(6, 0.69, SAME))
        chosen = _choice_payload(
            "黑胡椒杏鮑菇",
            {"黑胡椒杏鮑菇": 0.55, "番茄豆包": 0.40, "其他": 0.05},
        )
        post = self._post(stage1, [chosen])
        with mock.patch("backend.vision._post_jev_decisions", side_effect=post):
            result = recognize_dishes(
                self.image, [self.mushroom, self.tofu], InferenceEngine.CHOICE
            )
        pred = result["predictions"][0]
        self.assertEqual(pred["alternative"]["name"], "番茄豆包")
        self.assertEqual(pred["alternative"]["confidence"], 0.40)

    def test_other_drops_the_region(self):
        stage1 = {}
        stage1.update(_answers(8, 0.82, SAME))
        stage1.update(_answers(6, 0.69, SAME))
        chosen = _choice_payload(
            "其他",
            {"其他": 0.70, "黑胡椒杏鮑菇": 0.20, "番茄豆包": 0.10},
        )
        post = self._post(stage1, [chosen])
        with mock.patch("backend.vision._post_jev_decisions", side_effect=post):
            result = recognize_dishes(
                self.image, [self.mushroom, self.tofu], InferenceEngine.CHOICE
            )
        self.assertEqual(result["predictions"], [])

    def test_separate_boxes_keep_noul_scores(self):
        stage1 = {}
        stage1.update(_answers(8, 0.91, TOP))
        stage1.update(_answers(6, 0.82, MID))
        stage1.update(_answers(7, 0.73, BOT))
        post = self._post(stage1, [])
        with mock.patch("backend.vision._post_jev_decisions", side_effect=post):
            result = recognize_dishes(
                self.image,
                [self.mushroom, self.tofu, self.melon],
                InferenceEngine.CHOICE,
            )
        self.assertEqual(len(self.calls), 1)
        scored = {item["name"]: item["confidence"] for item in result["predictions"]}
        self.assertEqual(
            scored,
            {"黑胡椒杏鮑菇": 0.91, "番茄豆包": 0.82, "醬燒冬瓜": 0.73},
        )

    def test_three_overlapping_dishes_all_enter_choice(self):
        greens = _dish(1, "炒青菜", 11)
        cabbage = _dish(2, "炒高麗菜", 12)
        soup = _dish(4, "味噌湯", 14)
        stage1 = {}
        stage1.update(_answers(1, 0.90, SAME))
        stage1.update(_answers(2, 0.70, SAME))
        stage1.update(_answers(4, 0.60, SAME))
        chosen = _choice_payload(
            "炒高麗菜",
            {"炒青菜": 0.20, "炒高麗菜": 0.70, "味噌湯": 0.05, "其他": 0.05},
        )
        post = self._post(stage1, [chosen])
        with mock.patch("backend.vision._post_jev_decisions", side_effect=post):
            result = recognize_dishes(
                self.image, [greens, cabbage, soup], InferenceEngine.CHOICE
            )
        criteria = self.calls[1]["questions"]["region"]["criteria"]
        self.assertEqual(set(criteria), {"炒青菜", "炒高麗菜", "味噌湯", "其他"})
        self.assertEqual(result["predictions"][0]["name"], "炒高麗菜")
        self.assertEqual(result["predictions"][0]["confidence"], 0.70)
        self.assertEqual(result["predictions"][0]["bbox"], [0.1, 0.11, 0.45, 0.455])

    def test_blank_cooking_method_uses_fallback_text(self):
        plain = Dish(id=7, name="醬燒冬瓜", dish_code=7, cooking_method=None)
        seasoned = Dish(id=6, name="番茄豆包", dish_code=6, cooking_method="皺褶豆皮")
        stage1 = {}
        stage1.update(_answers(7, 0.90, SAME))
        stage1.update(_answers(6, 0.80, SAME))
        chosen = _choice_payload(
            "醬燒冬瓜",
            {"醬燒冬瓜": 0.80, "番茄豆包": 0.10, "其他": 0.10},
        )
        post = self._post(stage1, [chosen])
        with mock.patch("backend.vision._post_jev_decisions", side_effect=post):
            recognize_dishes(self.image, [plain, seasoned], InferenceEngine.CHOICE)
        criteria = self.calls[1]["questions"]["region"]["criteria"]
        self.assertEqual(criteria["醬燒冬瓜"], "菜單中的這道菜")

    def test_second_request_failure_does_not_return_noul(self):
        stage1 = {}
        stage1.update(_answers(8, 0.82, SAME))
        stage1.update(_answers(6, 0.69, SAME))
        post = self._post(
            stage1, [RuntimeError("Decisions API HTTP 500: boom")]
        )
        with mock.patch("backend.vision._post_jev_decisions", side_effect=post):
            with self.assertRaises(RuntimeError):
                recognize_dishes(
                    self.image, [self.mushroom, self.tofu], InferenceEngine.CHOICE
                )

    def test_missing_region_answer_fails(self):
        stage1 = {}
        stage1.update(_answers(8, 0.82, SAME))
        stage1.update(_answers(6, 0.69, SAME))
        post = self._post(stage1, [{"answers": {}}])
        with mock.patch("backend.vision._post_jev_decisions", side_effect=post):
            with self.assertRaises(RuntimeError):
                recognize_dishes(
                    self.image, [self.mushroom, self.tofu], InferenceEngine.CHOICE
                )


class ChoiceHttpTest(unittest.TestCase):
    def test_second_stage_http_500_returns_502(self):
        def post(body: dict) -> dict:
            if "region" in body["questions"]:
                raise RuntimeError("Decisions API HTTP 500: boom")
            answers = {}
            present_ids = [
                int(key.split("_", 1)[1])
                for key in body["questions"]
                if key.startswith("present_")
            ]
            for index, dish_id in enumerate(present_ids):
                box = SAME if index < 2 else OTHER
                noul = 0.90 - index * 0.10 if index < 2 else 0.10
                answers.update(_answers(dish_id, noul, box))
            return {"answers": answers}

        image = io.BytesIO()
        Image.new("RGB", (400, 400), (255, 255, 255)).save(image, format="JPEG")
        image.seek(0)
        with mock.patch("backend.vision._post_jev_decisions", side_effect=post):
            response = TestClient(app).post(
                "/api/predict",
                files={"file": ("plate.jpg", image, "image/jpeg")},
                data={"engine": "choice"},
            )
        self.assertEqual(response.status_code, 502)
        self.assertIn("模型辨識失敗", response.json()["detail"])


if __name__ == "__main__":
    unittest.main()
