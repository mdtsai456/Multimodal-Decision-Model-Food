"""API 入口。包含菜單、Excel 匯入與餐盤辨識。"""

import json
import shutil
import uuid
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from .auth import check_admin_password, create_admin_token, require_admin
from .database import BASE_FOR_FRONT_DIR, UPLOAD_DIR, Base, engine, get_db
from .excel_import import import_excel_to_db
from .models import Dish
from .schemas import (
    AdminLogin,
    DishOut,
    DishUpdate,
    PlateRecognitionOut,
    PredictResponse,
    RecordDetailOut,
    RecordListOut,
    TokenOut,
)
from .plate_recognition import plate_recognition_body, plate_recognition_failure
from .result_store import (
    list_records_for_date,
    load_record_detail,
    save_recognition_bundle,
)
from .vision import CLOSE_CONFIDENCE_DELTA, recognize_dishes, resolve_engine

# 資料表已存在時不重建。
Base.metadata.create_all(bind=engine)

app = FastAPI(title="HealthWeb 餐盤辨識", version="1.0.0")

# 本機開發允許所有來源呼叫 API。
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

FRONTEND_DIR = BASE_FOR_FRONT_DIR / "frontend"


def _photo_list(value: str | None) -> list[str] | None:
    """將 photo_files 的 JSON 字串轉成 list。"""
    if not value:
        return None
    try:
        data = json.loads(value)
        if isinstance(data, list):
            return [str(x) for x in data]
    except json.JSONDecodeError:
        pass
    return [value]


def dish_to_out(dish: Dish) -> DishOut:
    """將 Dish 轉成 DishOut。photo_files 轉成 list。"""
    data = {col.name: getattr(dish, col.name) for col in dish.__table__.columns}
    data["photo_files"] = _photo_list(dish.photo_files)
    return DishOut(**data)


@app.post("/api/admin/login", response_model=TokenOut)
def admin_login(body: AdminLogin):
    if not check_admin_password(body.password):
        raise HTTPException(status_code=401, detail="密碼錯誤")
    return TokenOut(token=create_admin_token(), message="登入成功")


@app.get("/api/dishes", response_model=list[DishOut])
def list_dishes(db: Session = Depends(get_db)):
    dishes = db.query(Dish).order_by(Dish.dish_code.asc(), Dish.id.asc()).all()
    return [dish_to_out(d) for d in dishes]


@app.get("/api/dishes/{dish_id}", response_model=DishOut)
def get_dish(dish_id: int, db: Session = Depends(get_db)):
    dish = db.query(Dish).filter(Dish.id == dish_id).first()
    if not dish:
        raise HTTPException(status_code=404, detail="找不到菜色")
    return dish_to_out(dish)


@app.put("/api/dishes/{dish_id}", response_model=DishOut)
def update_dish(
    dish_id: int,
    body: DishUpdate,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
):
    dish = db.query(Dish).filter(Dish.id == dish_id).first()
    if not dish:
        raise HTTPException(status_code=404, detail="找不到菜色")

    # 只更新請求有送出的欄位。
    for key, value in body.model_dump(exclude_unset=True).items():
        if key == "photo_files":
            dish.photo_files = (
                json.dumps(value, ensure_ascii=False) if value is not None else None
            )
        else:
            setattr(dish, key, value)

    db.commit()
    db.refresh(dish)
    return dish_to_out(dish)


@app.delete("/api/dishes/{dish_id}")
def delete_dish(
    dish_id: int,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
):
    dish = db.query(Dish).filter(Dish.id == dish_id).first()
    if not dish:
        raise HTTPException(status_code=404, detail="找不到菜色")
    db.delete(dish)
    db.commit()
    return {"ok": True}


@app.post("/api/admin/import-excel")
def import_excel(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
):
    if not file.filename or not file.filename.lower().endswith((".xlsx", ".xlsm", ".xls")):
        raise HTTPException(status_code=400, detail="請上傳 Excel 檔案")

    temp_path = UPLOAD_DIR / f"import_{uuid.uuid4().hex}.xlsx"
    try:
        with temp_path.open("wb") as f:
            shutil.copyfileobj(file.file, f)
        result = import_excel_to_db(db, temp_path, replace=False)
    finally:
        temp_path.unlink(missing_ok=True)
    return {"ok": True, **result}


def _resolve_request_engine(engine: str | None):
    try:
        return resolve_engine(engine)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _require_image(file: UploadFile) -> None:
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="請上傳圖片檔")


def _write_upload(file: UploadFile) -> Path:
    suffix = Path(file.filename or "plate.jpg").suffix or ".jpg"
    saved_path = UPLOAD_DIR / f"{uuid.uuid4().hex}{suffix}"
    with saved_path.open("wb") as handle:
        shutil.copyfileobj(file.file, handle)
    return saved_path


def _recognize_upload(saved_path: Path, dishes: list[Dish], resolved):
    try:
        return recognize_dishes(saved_path, dishes, resolved)
    except Exception:
        saved_path.unlink(missing_ok=True)
        raise


def _begin_recognition(file: UploadFile, engine: str | None, db: Session):
    resolved = _resolve_request_engine(engine)
    _require_image(file)
    saved_path = _write_upload(file)
    dishes = db.query(Dish).order_by(Dish.dish_code.asc()).all()
    return resolved, saved_path, dishes


def _save_bundle(saved_path: Path, dishes: list[Dish], result: dict) -> Path:
    return save_recognition_bundle(
        UPLOAD_DIR,
        saved_path,
        result["predictions"],
        {d.id: d for d in dishes},
    )


@app.post("/api/predict", response_model=PredictResponse)
def predict_plate(
    file: UploadFile = File(...),
    engine: str | None = Form(None),
    db: Session = Depends(get_db),
):
    resolved, saved_path, dishes = _begin_recognition(file, engine, db)
    try:
        result = _recognize_upload(saved_path, dishes, resolved)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"模型辨識失敗：{exc}") from exc
    session_dir = _save_bundle(saved_path, dishes, result)
    return PredictResponse(
        predictions=result["predictions"],
        model=result["model"],
        raw=result.get("raw"),
        result_dir=session_dir.name,
        close_confidence_delta=CLOSE_CONFIDENCE_DELTA,
    )


# 辨識步驟與 /api/predict 相同。回傳格式給食物攝影機的餐點分析使用。
@app.post(
    "/api/v1/plate-recognitions",
    response_model=PlateRecognitionOut,
    responses={502: {"model": PlateRecognitionOut}},
)
def recognize_plate_for_camera(
    file: UploadFile = File(...),
    engine: str | None = Form(None),
    db: Session = Depends(get_db),
):
    resolved, saved_path, dishes = _begin_recognition(file, engine, db)
    try:
        result = _recognize_upload(saved_path, dishes, resolved)
    except Exception as exc:
        return JSONResponse(
            status_code=502,
            content=plate_recognition_failure(str(exc)),
        )
    session_dir = _save_bundle(saved_path, dishes, result)
    return plate_recognition_body(result, session_dir.name)


@app.get("/api/admin/records", response_model=RecordListOut)
def list_recognition_records(
    date: str,
    _: str = Depends(require_admin),
):
    compact = date.replace("-", "")
    if len(compact) != 8 or not compact.isdigit():
        raise HTTPException(status_code=400, detail="日期格式請用 YYYY-MM-DD")
    records = list_records_for_date(UPLOAD_DIR, date)
    nice = f"{compact[0:4]}-{compact[4:6]}-{compact[6:8]}"
    return RecordListOut(date=nice, records=records)


@app.get("/api/admin/records/{session_id}", response_model=RecordDetailOut)
def get_recognition_record(
    session_id: str,
    _: str = Depends(require_admin),
):
    detail = load_record_detail(UPLOAD_DIR, session_id)
    if not detail:
        raise HTTPException(status_code=404, detail="找不到辨識紀錄")
    return RecordDetailOut(**detail)


@app.get("/api/health")
def health():
    return {"ok": True}


# 先掛載 /uploads。再掛載 /。後者會接住其餘路徑。
app.mount("/uploads", StaticFiles(directory=str(UPLOAD_DIR)), name="uploads")
app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
