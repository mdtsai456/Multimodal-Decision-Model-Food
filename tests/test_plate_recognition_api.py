"""食物攝影機辨識 API 的回傳格式。這些測試不呼叫模型。"""

import unittest
from unittest import mock

from fastapi.testclient import TestClient

from backend.main import app
from backend.plate_recognition import plate_recognition_body, plate_recognition_failure


def _prediction() -> dict:
    return {
        "name": "季節時蔬(高麗菜)",
        "dish_id": 12,
        "dish_code": 17,
        "confidence": 0.87,
        "reason": None,
        "bbox": [0.27, 0.27, 0.41, 0.315],
        "alternative": {
            "name": "紅燒牛肉",
            "dish_id": 10,
            "dish_code": 15,
            "confidence": 0.62,
            "reason": None,
        },
        "second_best": {
            "name": "不該出現",
            "dish_id": 1,
            "dish_code": 1,
            "confidence": 0.2,
            "reason": None,
        },
    }


class PlateRecognitionBodyTest(unittest.TestCase):
    def test_matches_meal_analysis_item(self):
        body = plate_recognition_body(
            {"predictions": [_prediction()], "model": "typesafe/jev-1.14"},
            "20261027_103900",
        )
        self.assertTrue(body["succeeded"])
        self.assertIsNone(body["errorMessage"])
        self.assertEqual(body["modelName"], "typesafe/jev-1.14")
        self.assertIsNone(body["modelVersion"])
        self.assertEqual(body["resultDir"], "20261027_103900")
        item = body["items"][0]
        self.assertEqual(item["detectedLabel"], "季節時蔬(高麗菜)")
        self.assertEqual(item["sourceFoodCode"], "17")
        self.assertEqual(item["confidence"], 0.87)
        self.assertIsNone(item["grams"])
        self.assertNotIn("foodId", item)
        self.assertEqual(item["alternative"]["detectedLabel"], "紅燒牛肉")
        self.assertEqual(item["alternative"]["sourceFoodCode"], "15")
        self.assertNotIn("second_best", item)
        self.assertNotIn("不該出現", str(item))

    def test_empty_plate_still_succeeds(self):
        body = plate_recognition_body(
            {"predictions": [], "model": "typesafe/jev-1.14"},
            "20261027_103901",
        )
        self.assertTrue(body["succeeded"])
        self.assertEqual(body["items"], [])

    def test_failure_has_message_and_no_items(self):
        body = plate_recognition_failure("連線中斷")
        self.assertFalse(body["succeeded"])
        self.assertEqual(body["errorMessage"], "連線中斷")
        self.assertEqual(body["items"], [])


class PlateRecognitionRouteTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_rejects_non_image(self):
        response = self.client.post(
            "/api/v1/plate-recognitions",
            files={"file": ("note.txt", b"hello", "text/plain")},
        )
        self.assertEqual(response.status_code, 400)

    def test_rejects_unknown_engine(self):
        response = self.client.post(
            "/api/v1/plate-recognitions",
            data={"engine": "choice"},
            files={"file": ("plate.jpg", b"\xff\xd8\xff", "image/jpeg")},
        )
        self.assertEqual(response.status_code, 400)

    def test_success_json_for_camera(self):
        def _save(_upload_dir, saved_path, _predictions, _dish_by_id):
            saved_path.unlink(missing_ok=True)
            return type("SessionDir", (), {"name": "20261027_103900"})()

        with (
            mock.patch(
                "backend.main.recognize_dishes",
                return_value={
                    "predictions": [_prediction()],
                    "model": "typesafe/jev-1.14",
                    "raw": {},
                },
            ),
            mock.patch("backend.main.save_recognition_bundle", side_effect=_save),
        ):
            response = self.client.post(
                "/api/v1/plate-recognitions",
                files={"file": ("plate.jpg", b"\xff\xd8\xff", "image/jpeg")},
            )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["succeeded"])
        self.assertEqual(body["modelName"], "typesafe/jev-1.14")
        self.assertEqual(body["items"][0]["sourceFoodCode"], "17")
        self.assertIsNone(body["items"][0]["grams"])
        self.assertNotIn("foodId", body["items"][0])

    def test_model_error_is_failed_outcome(self):
        with mock.patch(
            "backend.main.recognize_dishes",
            side_effect=RuntimeError("模型沒有回應"),
        ):
            response = self.client.post(
                "/api/v1/plate-recognitions",
                files={"file": ("plate.jpg", b"\xff\xd8\xff", "image/jpeg")},
            )
        self.assertEqual(response.status_code, 502)
        body = response.json()
        self.assertFalse(body["succeeded"])
        self.assertEqual(body["errorMessage"], "模型沒有回應")
        self.assertEqual(body["items"], [])
