"""API 入口：菜單 CRUD、Excel 匯入、餐盤辨識。"""

# --- 標準函式庫 ---
import json          # 處理 photo_files 的 JSON 字串 ↔ list
import shutil        # 把上傳檔案寫到磁碟
import uuid          # 產生不重複的暫存／上傳檔名
from pathlib import Path  # 處理檔案路徑
from dotenv import load_dotenv
load_dotenv()

# --- 第三方套件 ---
from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
# Depends：注入依賴（如 get_db、require_admin）
# FastAPI：建立 Web API 應用
# File / UploadFile：接收上傳檔（Excel、圖片）
# HTTPException：回傳錯誤狀態碼與訊息
from fastapi.middleware.cors import CORSMiddleware  # 允許前端跨網域呼叫
from fastapi.staticfiles import StaticFiles         # 提供前端頁面與上傳圖靜態檔
from sqlalchemy.orm import Session                  # 資料庫 Session 型別

# --- 專案內部模組 ---
from .auth import check_admin_password, create_admin_token, require_admin
# check_admin_password：比對管理密碼
# create_admin_token：登入成功後發 token
# require_admin：保護需管理員權限的 API
from .database import BASE_DIR, UPLOAD_DIR, Base, engine, get_db
# BASE_DIR / UPLOAD_DIR：專案與上傳路徑
# Base / engine：建立資料表
# get_db：每個請求取得資料庫連線
from .excel_import import import_excel_to_db  # Excel → 寫入資料庫
from .models import Dish                      # 菜色資料表
from .schemas import (
    AdminLogin,       # 登入請求格式
    DishOut,          # 菜色回傳格式
    DishUpdate,       # 編輯菜色請求格式
    PredictResponse,  # 辨識結果回傳格式
    PredictionItem,   # 單一辨識項目（菜名、confidence）
    TokenOut,         # 登入成功回傳 token
)
from .result_store import save_recognition_bundle
from .vision import recognize_dishes  # 呼叫 AI 模型辨識餐盤

# 啟動時依 model 建立資料表（若已存在則略過）
Base.metadata.create_all(bind=engine)

app = FastAPI(title="HealthWeb 餐盤辨識", version="1.0.0")

# 允許前端跨網域呼叫 API（本機開發用）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

FRONTEND_DIR = BASE_DIR / "healthWeb" / "frontend"  # 前端 HTML/CSS/JS 目錄


# 資料庫裡 photo_files 是 JSON 字串，轉成 list 給前端較好用
def _photo_list(value: str | None) -> list[str] | None:
    if not value:
        return None
    try:
        data = json.loads(value)
        if isinstance(data, list):
            return [str(x) for x in data]
    except json.JSONDecodeError:
        pass
    return [value]


# 把 ORM 的 Dish 轉成 API 回傳格式 DishOut
def dish_to_out(dish: Dish) -> DishOut:
    data = {col.name: getattr(dish, col.name) for col in dish.__table__.columns}
    data["photo_files"] = _photo_list(dish.photo_files)
    return DishOut(**data)


# 管理員登入：密碼正確就回傳 token
@app.post("/api/admin/login", response_model=TokenOut)
def admin_login(body: AdminLogin):
    if not check_admin_password(body.password):
        raise HTTPException(status_code=401, detail="密碼錯誤")
    return TokenOut(token=create_admin_token(), message="登入成功")


# 列出全部菜色（管理頁表格、辨識時也會用資料庫菜單）
@app.get("/api/dishes", response_model=list[DishOut])
def list_dishes(db: Session = Depends(get_db)):
    dishes = db.query(Dish).order_by(Dish.dish_code.asc(), Dish.id.asc()).all()
    return [dish_to_out(d) for d in dishes]


# 取得單一道菜（編輯前先撈資料）
@app.get("/api/dishes/{dish_id}", response_model=DishOut)
def get_dish(dish_id: int, db: Session = Depends(get_db)):
    dish = db.query(Dish).filter(Dish.id == dish_id).first()
    if not dish:
        raise HTTPException(status_code=404, detail="找不到菜色")
    return dish_to_out(dish)


# 更新菜色（需管理員 token）
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

    # exclude_unset=True：只更新有傳進來的欄位
    for key, value in body.model_dump(exclude_unset=True).items():
        if key == "photo_files":
            # list 存回資料庫前再轉成 JSON 字串
            dish.photo_files = (
                json.dumps(value, ensure_ascii=False) if value is not None else None
            )
        else:
            setattr(dish, key, value)

    db.commit()
    db.refresh(dish)  # 重新讀取最新資料（含 updated_at）
    return dish_to_out(dish)


# 刪除菜色（需管理員 token）
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


# 匯入 Excel：只追加新菜，不會清掉舊資料（需管理員 token）
@app.post("/api/admin/import-excel")
async def import_excel(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
):
    if not file.filename or not file.filename.lower().endswith((".xlsx", ".xlsm", ".xls")):
        raise HTTPException(status_code=400, detail="請上傳 Excel 檔案")

    # 先存成暫存檔再匯入，結束後刪除暫存檔
    temp_path = UPLOAD_DIR / f"import_{uuid.uuid4().hex}.xlsx"
    try:
        with temp_path.open("wb") as f:
            shutil.copyfileobj(file.file, f)
        result = import_excel_to_db(db, temp_path, replace=False)
    finally:
        if temp_path.exists():
            temp_path.unlink()
    return {"ok": True, **result}  # 含 imported / skipped / total_in_file


# 餐盤辨識：上傳圖片 → 依資料庫菜單呼叫 AI 模型 → 存結果資料夾
@app.post("/api/predict", response_model=PredictResponse)
async def predict_plate(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="請上傳圖片檔")

    # 先暫存到 uploads，辨識後會移入「日期_時間」資料夾
    suffix = Path(file.filename or "plate.jpg").suffix or ".jpg"
    saved_name = f"{uuid.uuid4().hex}{suffix}"
    saved_path = UPLOAD_DIR / saved_name
    with saved_path.open("wb") as f:
        shutil.copyfileobj(file.file, f)

    dishes = db.query(Dish).order_by(Dish.dish_code.asc()).all()
    try:
        result = recognize_dishes(saved_path, dishes)
    except Exception as exc:
        if saved_path.exists():
            saved_path.unlink()
        raise HTTPException(status_code=502, detail=f"模型辨識失敗：{exc}") from exc

    predictions = result["predictions"]
    dish_by_id = {d.id: d for d in dishes}
    # 存：原圖 / 標註圖 / JSON
    session_dir = save_recognition_bundle(
        UPLOAD_DIR, saved_path, predictions, dish_by_id
    )

    return PredictResponse(
        predictions=[PredictionItem(**p) for p in predictions],
        model=result["model"],
        raw=result.get("raw"),
        result_dir=session_dir.name,  # 例如 20260716_125930
    )


# 健康檢查：確認伺服器有在跑
@app.get("/api/health")
def health():
    return {"ok": True}


# 靜態檔掛載（順序：先 /uploads，最後掛 / 當前端）
app.mount("/uploads", StaticFiles(directory=str(UPLOAD_DIR)), name="uploads")
app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
