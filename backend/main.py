from __future__ import annotations

"""API 入口：菜單 CRUD、Excel 匯入、餐盤辨識。"""

# --- 標準函式庫 ---
import json          # 處理 photo_files 的 JSON 字串 ↔ list
import shutil        # 把上傳檔案寫到磁碟
import uuid          # 產生不重複的暫存／上傳檔名
from pathlib import Path  # 處理檔案路徑
from typing import Optional
from dotenv import load_dotenv
load_dotenv()

# --- 第三方套件 ---
from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
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
from .database import BASE_DIR, BASE_FOR_FRONT_DIR, UPLOAD_DIR, Base, engine, get_db
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
    RecordDetailOut,  # 辨識紀錄詳情
    RecordListOut,    # 辨識紀錄列表
    TokenOut,         # 登入成功回傳 token
)
from .result_store import (
    list_records_for_date,    # 依日期列出 uploads 辨識紀錄摘要
    load_record_detail,       # 讀單筆紀錄詳情（圖、菜色、bbox）
    save_recognition_bundle,  # 辨識後存原圖／標註圖／result.json
)
from .vision import CLOSE_CONFIDENCE_DELTA, recognize_dishes, resolve_engine

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

FRONTEND_DIR = BASE_FOR_FRONT_DIR / "frontend"  # 前端 HTML/CSS/JS 目錄


# 資料庫裡 photo_files 是 JSON 字串，轉成 list 給前端較好用
def _photo_list(value: Optional[str]) -> Optional[list[str]]:
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
    # 從資料庫物件 Dish，轉成要回給前端的 DishOut
    # 先把資料表每個欄位名 → 對應的值，收成一個 dict
    # 例如 {"id": 1, "name": "炒青菜", "calories_100g": "80", ...}
    data = {col.name: getattr(dish, col.name) for col in dish.__table__.columns}
    # photo_files 在 DB 是 JSON 字串，API 要回 list，所以另外轉
    data["photo_files"] = _photo_list(dish.photo_files)
    # 用這個 dict 建立 DishOut（Pydantic 會檢查型別）
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
    file: UploadFile = File(...),       # 上傳的 Excel 檔（必填）
    db: Session = Depends(get_db),      # 自動注入資料庫連線
    _: str = Depends(require_admin),    # 需有效管理員 token；_ 表示回傳值用不到
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
@app.post("/api/predict", response_model=PredictResponse)  # POST；回傳格式固定為 PredictResponse
async def predict_plate(
    file: UploadFile = File(...),   # 上傳的餐盤圖片（必填）
    engine: Optional[str] = Form(None),  # 這次改走 chat 或 jev；不送就用環境變數
    db: Session = Depends(get_db),  # 自動注入資料庫連線
):
    try:
        resolved = resolve_engine(engine)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # 不是圖片（或沒有 content_type）→ 400 拒絕
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="請上傳圖片檔")

    # 先暫存到 uploads，辨識後會移入「日期_時間」資料夾
    suffix = Path(file.filename or "plate.jpg").suffix or ".jpg"  # 副檔名，沒有就用 .jpg
    saved_name = f"{uuid.uuid4().hex}{suffix}"  # 亂數檔名，避免撞名
    saved_path = UPLOAD_DIR / saved_name        # 完整暫存路徑
    with saved_path.open("wb") as f:
        shutil.copyfileobj(file.file, f)  # 把上傳內容寫進磁碟

    # 讀出全部菜單（依菜色編號排序），當 AI 的候選清單
    dishes = db.query(Dish).order_by(Dish.dish_code.asc()).all()
    try:
        # 呼叫 vision：圖片 + 菜單 → 模型辨識
        result = recognize_dishes(saved_path, dishes, resolved)
    except Exception as exc:
        # 辨識失敗：刪掉暫存圖，回 502
        if saved_path.exists():
            saved_path.unlink()
        raise HTTPException(status_code=502, detail=f"模型辨識失敗：{exc}") from exc

    predictions = result["predictions"]  # 辨識到的菜色列表（dict）
    dish_by_id = {d.id: d for d in dishes}  # id → Dish，存檔時查餐別等用
    # 存：原圖 / 標註圖 / JSON（暫存圖會移進 session 資料夾）
    session_dir = save_recognition_bundle(
        UPLOAD_DIR, saved_path, predictions, dish_by_id
    )

    api_items = []  # 轉成 Pydantic，準備回傳給前端 Pydantic 會檢查：缺欄位、型別錯、信心分數超出範圍 → 直接報錯；通過才繼續。
    for p in predictions:
        item = PredictionItem(**p)  # dict → PredictionItem # 用字典填進「規格表」
        # 次佳：有採用的 alternative，或僅供 debug 的 second_best
        second = item.second_best or item.alternative
        if second:
            # 有 alternative＝前端會雙結果；只有 second_best＝只印 log
            flag = "採用雙結果" if item.alternative else "僅 debug"
            print(
                f"[debug] API 最佳/次佳: {item.name} ({item.confidence:.2f}) / "
                f"{second.name} ({second.confidence:.2f}) [{flag}]"
            )
        else:
            print(f"[debug] API 最佳/次佳: {item.name} ({item.confidence:.2f}) / 無次佳")
        api_items.append(item)

    # 組整份 API 回應
    return PredictResponse(
        predictions=api_items,              # 菜名、信心、bbox、次佳等
        model=result["model"],             # 實際使用的模型名稱
        raw=result.get("raw"),             # 模型原始文字（除錯用）
        result_dir=session_dir.name,        # 存檔資料夾名，例如 20260716_125930
        close_confidence_delta=CLOSE_CONFIDENCE_DELTA,  # 雙結果信心差門檻
    )


# 依日期列出辨識紀錄（需管理員；資料夾名稱以 YYYYMMDD 開頭）
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


# 單筆辨識紀錄詳情（需管理員）
@app.get("/api/admin/records/{session_id}", response_model=RecordDetailOut)
def get_recognition_record(
    session_id: str,
    _: str = Depends(require_admin),
):
    detail = load_record_detail(UPLOAD_DIR, session_id)
    if not detail:
        raise HTTPException(status_code=404, detail="找不到辨識紀錄")
    return RecordDetailOut(**detail)


# 健康檢查：確認伺服器有在跑
@app.get("/api/health")
def health():
    return {"ok": True}


# 靜態檔掛載（順序：先 /uploads，最後掛 / 當前端）
app.mount("/uploads", StaticFiles(directory=str(UPLOAD_DIR)), name="uploads")
app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
