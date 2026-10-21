# HealthWeb 餐盤菜色辨識

台鋼餐飲智慧辨識 Web：管理員以 Excel 維護菜單；使用者上傳餐盤照片後，系統依後端菜單呼叫 Zeabur AI Hub（目前 `gpt-5.6-luna`）辨識菜色，回傳菜名、信心分數，並在圖上標出大概外框。每次辨識也會在伺服器存檔（原圖、標註圖、JSON）。

---

## 功能摘要

| 角色 | 功能 |
|------|------|
| 使用者 | 上傳餐盤圖 → AI 辨識 → 顯示菜名、confidence、彩色外框標註 |
| 管理員 | 密碼登入 → 查看／編輯／刪除菜單 → 匯入 Excel（只追加，不覆蓋舊資料） |

---

## 系統流程

```
使用者上傳圖片
       │
       ▼
前端 (index.html / app.js)
       │  POST /api/predict
       ▼
後端 FastAPI (main.py)
       │  暫存圖 → 讀資料庫菜單 → vision.recognize_dishes()
       ▼
Zeabur AI Hub（OpenAI 相容 API + 看圖）
       │  回傳 JSON：菜名、confidence、bbox
       ▼
後端 result_store.py
       │  建立 uploads/日期_時間/ 資料夾
       │  寫入 original.*、result_boxed.jpg、result.json
       ▼
回傳辨識結果給前端
       │
       ▼
前端用本機預覽畫圖 + 外框，並列出結果卡片
（外框顏色依本次菜色數量動態分配，互不相同）
```

管理員流程：

```
登入（密碼）→ 取得 token
     │
     ├─ GET /api/dishes　　　　列出菜單
     ├─ GET /api/dishes/{id}　　單筆查詢
     ├─ PUT /api/dishes/{id}　　編輯
     ├─ DELETE /api/dishes/{id}　刪除
     └─ POST /api/admin/import-excel　匯入 Excel（重複略過）
```

---

## 技術棧

- **後端**：Python、FastAPI、SQLAlchemy、SQLite、Pillow（畫標註圖）
- **前端**：HTML / CSS / JavaScript（無框架）
- **AI**：Zeabur AI Hub（`openai` SDK，模型見 `.env` 的 `AI_MODEL`）
- **資料**：Excel（openpyxl）匯入 → SQLite `data/menu.db`

---

## 目錄結構

```
healthWeb/
├── backend/                 # FastAPI 後端
│   ├── main.py              # API 入口、路由、靜態檔掛載
│   ├── auth.py              # 管理員密碼與簽章 token
│   ├── database.py          # DB 連線、路徑、.env 載入
│   ├── models.py            # Dish 資料表定義
│   ├── schemas.py           # 請求／回應 Pydantic 格式
│   ├── excel_import.py      # 讀 Excel、追加寫入 DB
│   ├── vision.py            # 組 prompt、呼叫模型、解析結果
│   └── result_store.py      # 辨識結果存檔（原圖／標註圖／JSON）
├── frontend/                # 前端靜態頁
│   ├── index.html           # 使用者：上傳與辨識
│   ├── admin.html           # 管理員：登入與菜單管理
│   ├── css/styles.css
│   └── js/
│       ├── app.js           # 辨識頁（預覽、畫框、結果卡片）
│       └── admin.js         # 管理頁（登入、編輯刪除、匯入）
├── data/
│   ├── menu.db              # SQLite（執行後產生）
│   └── uploads/             # 每次辨識一個「日期_時間」資料夾
│       └── YYYYMMDD_HHMMSS/
│           ├── original.jpg       # 上傳原圖（副檔名依實際上傳）
│           ├── result_boxed.jpg   # 有外框＋菜名的結果圖
│           └── result.json        # 辨識結果 JSON
├── scripts/
│   └── seed_excel.py        # 初次整批匯入 Excel（會清空舊資料）
├── .env                     # 本機密鑰與設定（勿提交 git）
├── .env.example             # 環境變數範例
├── requirements.txt
├── run.bat                  # Windows 啟動腳本
└── README.md
```

---

## 環境變數（`.env`）

| 變數 | 說明 |
|------|------|
| `ZEABUR_AI_HUB_API_KEY` | 看圖對話用的 API Key。改走 Jev 時，這把 key 要換成 OpenRouter 的 key |
| `ZEABUR_AI_HUB_BASE_URL` | 看圖對話的位址，例如 `https://hnd1.aihub.zeabur.ai/` |
| `AI_MODEL` | 看圖對話的模型，例如 `gpt-5.6-luna`。Jev 不讀這個變數 |
| `INFERENCE_ENGINE` | `chat`（預設，看圖對話）或 `jev` |
| `JEV_MODEL` | Jev 模型，預設 `typesafe/jev-1.14` |
| `JEV_DECISIONS_URL` | Decisions API，預設 `https://openrouter.ai/api/alpha/decisions` |
| `NOUL_PRESENT_MIN` | Jev 低於這個值就當這道菜不在盤上，預設 `0.50` |
| `CLOSE_CONFIDENCE_DELTA` | 同一格兩道菜的信心差在這個值以內才顯示雙結果，預設 `0.30` |
| `ADMIN_PASSWORD` | 管理員登入密碼 |
| `SECRET_KEY` | 簽發管理 token 用的密鑰 |

可複製 `.env.example` 再填入真實值。 
修改 `.env` 後請**重啟伺服器**才會生效。

---

## 使用 Jev 辨識

網頁還是同一頁，沒有引擎開關。預設走看圖對話。要整站改走 Jev，在 `.env` 設：

```env
INFERENCE_ENGINE=jev
JEV_MODEL=typesafe/jev-1.14
JEV_DECISIONS_URL=https://openrouter.ai/api/alpha/decisions
NOUL_PRESENT_MIN=0.50
```

`ZEABUR_AI_HUB_API_KEY` 填 OpenRouter 的 key。Jev 會用它呼叫上面的 Decisions API，模型是 `typesafe/jev-1.14`。存檔後重啟：

```bat
uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
```

啟動日誌要看到 `INFERENCE_ENGINE=jev`。接著打開 http://127.0.0.1:8000/ ，上傳餐盤照片並按「開始辨識」。畫面上的百分比是這道菜在不在的分數；外框畫在食物上。

只想某一次用 Jev、其餘維持看圖對話時，不要改 `INFERENCE_ENGINE`，改打 API：

```bat
curl -F "file=@餐盤.jpg" -F "engine=jev" http://127.0.0.1:8000/api/predict
```

`engine` 接受 `chat`、`jev` 或 `choice`。其他值回 400。沒帶這個欄位時，用 `.env` 的 `INFERENCE_ENGINE`。`choice` 先做與 `jev` 相同的判斷，再對重疊的食物區域用 Choice 選一個菜名，見 `docs/choice-path-spec.md`。

Jev 對菜單裡的每一道菜問兩題：在不在、外框。外觀文字來自管理頁的「烹調方式」，接到「在不在」那一題，大約 200 字。分數低於 `NOUL_PRESENT_MIN` 的菜不會出現。兩個框重疊很多時合成同一格；信心差在 `CLOSE_CONFIDENCE_DELTA` 以內，畫面才會同時出現兩種菜名（result1 / result2）。

判斷規則的細節寫在 `docs/inference-spec.md`。

---

## 安裝與啟動

```bat
cd C:\healthWeb
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

（可選）初次從 Excel 種子匯入：

```bat
python scripts\seed_excel.py
```

啟動伺服器：

```bat
run.bat
```

或：

```bat
uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
```

瀏覽：

- 使用者頁：http://127.0.0.1:8000/
- 管理頁：http://127.0.0.1:8000/admin.html
- API 文件：http://127.0.0.1:8000/docs

預設管理員密碼見 `.env` 的 `ADMIN_PASSWORD`（範例為 `admin123`）。

---

## 主要 API

| 方法 | 路徑 | 說明 |
|------|------|------|
| POST | `/api/admin/login` | 管理員登入，回傳 token |
| GET | `/api/dishes` | 列出菜單 |
| GET | `/api/dishes/{id}` | 取得單一菜色 |
| PUT | `/api/dishes/{id}` | 更新菜色（需 token） |
| DELETE | `/api/dishes/{id}` | 刪除菜色（需 token） |
| POST | `/api/admin/import-excel` | 匯入 Excel，只追加（需 token） |
| POST | `/api/predict` | 上傳餐盤圖辨識。表單可加 `engine=chat` 或 `engine=jev`；成功後寫入 uploads 結果資料夾 |
| GET | `/api/health` | 健康檢查 |

管理 API 請在 Header 帶：`Authorization: Bearer <token>`。

`/api/predict` 回傳重點欄位：

- `predictions[]`：`name`、`dish_id`、`dish_code`、`confidence`、`reason`、`bbox`
- `model`：實際使用的模型名稱
- `result_dir`：本次存檔資料夾名稱（例如 `20260716_131500`）

---

## 辨識結果 JSON 範例（`result.json`）

```json
{
  "meal_type": "午餐",
  "dishes": [
    {
      "dish_id": "10",
      "dish_name": "泰式豬肉片",
      "confidence": 0.9
    },
    {
      "dish_id": "9",
      "dish_name": "季節時蔬(四季豆)",
      "confidence": 0.98
    }
  ]
}
```

說明：`dish_id` 為菜單「菜色編號」字串；`meal_type` 取自本次辨識菜色中最常見的餐別。

---

## 各模組說明（給報告用）

### `backend/main.py`
應用入口：建立資料表、註冊路由、掛載前端與 `uploads`。辨識成功後呼叫 `result_store` 存檔，並回傳結果給前端。

### `backend/auth.py`
簡單管理員驗證：比對 `.env` 密碼；用 `hmac` + `sha256` 簽發有時效（預設 12 小時）的 token，保護編輯／刪除／匯入 API。

### `backend/database.py`
載入 `.env`、設定 SQLite 路徑、提供 `get_db()` 給每個請求開關資料庫連線。

### `backend/models.py`
`Dish` 資料表，欄位對應台鋼餐飲 Excel（菜名、組成、AI 烹調方式、營養、份量等）。

### `backend/schemas.py`
API 進出資料格式（Pydantic），例如 `DishOut`、`PredictResponse`（含 `bbox`、`result_dir`）。

### `backend/excel_import.py`
讀 Excel 列轉成字典寫入 DB。管理頁匯入預設 **不覆蓋**：菜色編號或菜名已存在則略過。

### `backend/vision.py`
組候選菜單文字（含 AI 烹調方式等外觀敘述）→ 呼叫 Zeabur → 解析 JSON → 對回資料庫菜色，並正規化 `bbox`（相對座標 0～1）。

### `backend/result_store.py`
建立 `uploads/日期_時間/`，寫入原圖、有外框的結果圖、`result.json`。外框顏色依本次菜色數量在色環均分，互不相同。

### `frontend/js/app.js`
選圖／拖曳、送辨識、用 canvas 畫圖與外框（標籤只顯示菜名）、列出 confidence 卡片（主題色與外框一致）。畫面預覽用本機 `createObjectURL`；伺服器另有完整存檔。

### `frontend/js/admin.js`
登入存 token、表格編輯／刪除、匯入 Excel 並提示新增／略過筆數（不提供手動新增菜色，改以 Excel 匯入）。

---

## 設計取捨（簡述）

1. **菜單來源**：Excel／管理後台維護，辨識時只能從資料庫候選菜中選，避免模型亂發明菜名。
2. **匯入策略**：一般匯入只追加；`seed_excel.py` 才會清空後重匯（初始化用）。
3. **辨識結果存檔**：每次在 `data/uploads/YYYYMMDD_HHMMSS/` 存原圖、標註圖、`result.json`。
4. **外框**：由模型估大概 `bbox`；顏色依該次菜色數量動態產生，不固定色票數量。
5. **管理驗證**：單密碼 + 簽章 token，夠用且實作簡單。
6. **AI 入口**：透過 Zeabur AI Hub 統一呼叫模型，本機不需自架 GPU。

---

## 部署至 Zeabur

官方 Python／FastAPI 說明可參考：[Deploy Python Projects](https://zeabur.com/docs/en-US/guides/python)

### 部署前準備

1. 將專案推上 **GitHub**（確認 `.env` 已在 `.gitignore`，不要上傳金鑰）
2. 專案根目錄已有 `zbpack.json`，指定進入點為 `backend/main.py`（FastAPI 的 `app`）
3. 本機先能正常跑通辨識與管理功能

### 步驟

1. 登入 [Zeabur](https://zeabur.com/) → 建立 Project
2. **Add Service** → 選 **GitHub** → 選這個 repo
3. 等建置完成後，在服務裡設定 **環境變數**（與 `.env` 相同）：
   - `ZEABUR_AI_HUB_API_KEY`
   - `ZEABUR_AI_HUB_BASE_URL`
   - `AI_MODEL`（例如 `gpt-5.6-luna`）
   - 若要走 Jev：`INFERENCE_ENGINE=jev`、`JEV_MODEL`、`JEV_DECISIONS_URL`、`NOUL_PRESENT_MIN`，且 API Key 須為 OpenRouter
   - `ADMIN_PASSWORD`
   - `SECRET_KEY`（請改成夠長的隨機字串）
4. **Networking / Domain**：產生 `*.zeabur.app` 網域或綁自訂網域
5. **Volumes（重要）**：掛載持久化磁碟到服務內的 `data` 目錄  
   - 例如 Mount Directory：`/data` 或專案實際路徑下的 `data`  
   - 用來保留 `menu.db` 與 `uploads/` 辨識結果  
   - 未掛 Volume 時，服務重啟可能清空 SQLite 與上傳檔

### 部署後檢查

- 開啟網域首頁，確認能上傳辨識
- 開啟 `/admin.html`，用 `ADMIN_PASSWORD` 登入
- 管理頁重新匯入 Excel（Volume 是新的話資料庫是空的）
- 結果頁應顯示 `模型：gpt-5.6-luna`（或你設的 `AI_MODEL`）

### 注意

- **Zeabur AI Hub**（呼叫模型）與 **部署到 Zeabur**（網站上線）是兩件事；上線後仍要用 AI Hub 的 API Key。
- 若建置失敗，到服務 Logs 查看；常見原因是進入點不對或環境變數沒設。
- 掛 Volume 當下該目錄會被清空，請先備份再掛。

---

## 注意事項

- `.env` 含 API Key，已列入 `.gitignore`，請勿提交版本庫。
- 若曾在對話或文件中貼過 Key，建議至 Zeabur 後台輪替。
- 辨識品質受菜單敘述（尤其「AI 烹調方式」）與模型能力影響；單菜／分格多菜情境已在 prompt 中特別處理。
- 外框為「大概範圍」，適合分格餐盤示意，非專業物件偵測精度。
- 前端畫面用本機預覽顯示圖片；伺服器存檔供後續查閱，兩者分開。
