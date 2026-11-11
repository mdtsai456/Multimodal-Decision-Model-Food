# HealthWeb 餐盤菜色辨識

台鋼餐飲智慧辨識 Web：管理員以 Excel 維護菜單；使用者上傳餐盤照片後，系統只從資料庫菜單裡選菜，回傳菜名、信心分數，並在圖上標出外框。辨識引擎有兩種，由 `.env` 的 `INFERENCE_ENGINE` 決定，網頁沒有引擎開關。

- `chat`：OpenAI 相容的看圖對話。位址是 `ZEABUR_AI_HUB_BASE_URL`，模型是 `AI_MODEL`。
- `jev`：OpenRouter Decisions API。模型預設 `typesafe/jev-1.14`。

兩條都用同一把 `ZEABUR_AI_HUB_API_KEY`。每次辨識會在伺服器存檔（原圖、標註圖、JSON）。

---

## 功能摘要

| 角色 | 功能 |
|------|------|
| 使用者 | 上傳餐盤圖 → AI 辨識 → 顯示菜名、confidence、彩色外框標註。同一格有接近的次佳菜時，並排兩張圖 |
| 管理員 | 密碼登入 → 查看／編輯／刪除菜單 → 匯入 Excel（只追加，不覆蓋舊資料）→ 依日期查看辨識紀錄 |

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
       │
       ├─ chat → OpenAI 相容看圖 API（AI_MODEL）
       └─ jev  → OpenRouter Decisions API（JEV_MODEL）
       │
       ▼
後端 result_store.py
       │  建立 uploads/日期_時間/ 資料夾
       │  寫入 original.*、result_boxed.jpg、result.json
       │  任一格有 alternative 時，另寫 result1/、result2/
       ▼
回傳辨識結果給前端
       │
       ▼
前端用本機預覽畫圖 + 外框，並列出結果卡片
（外框顏色依本次菜色數量動態分配，互不相同）
有 alternative 時，整頁改成兩張圖：result1 是主菜名，result2 把該格改成次佳菜名，框相同
```

管理員流程：

```
登入（密碼）→ 取得 token
     │
     ├─ GET /api/dishes　　　　　　列出菜單
     ├─ GET /api/dishes/{id}　　　單筆查詢
     ├─ PUT /api/dishes/{id}　　　編輯
     ├─ DELETE /api/dishes/{id}　刪除
     ├─ POST /api/admin/import-excel　匯入 Excel（重複略過）
     ├─ GET /api/admin/records?date=YYYY-MM-DD　當日辨識紀錄
     └─ GET /api/admin/records/{session_id}　　單次紀錄明細
```

---

## 技術棧

- **後端**：Python 3.9 以上、FastAPI、SQLAlchemy、SQLite、Pillow（畫標註圖）。Zeabur 建置使用 `zbpack.json` 的 Python 3.12
- **前端**：HTML / CSS / JavaScript（無框架）
- **AI**：`chat` 用 `openai` SDK；`jev` 用 HTTP 呼叫 OpenRouter Decisions API
- **資料**：Excel（openpyxl）匯入 → SQLite `data/menu.db`

---

## 目錄結構

```
healthWeb/
├── backend/
│   ├── main.py              # API 入口、路由、靜態檔掛載
│   ├── auth.py              # 管理員密碼與簽章 token
│   ├── database.py          # DB 連線、路徑、.env 載入
│   ├── models.py            # Dish 資料表定義
│   ├── schemas.py           # 請求／回應 Pydantic 格式
│   ├── excel_import.py      # 讀 Excel、追加寫入 DB
│   ├── vision.py            # chat / jev 辨識、同一格合併、次佳菜
│   ├── bbox_adjust.py       # 畫面上的外框上下位移（常數，不讀 .env）
│   ├── plate_recognition.py # 轉成食物攝影機 JSON
│   └── result_store.py      # 辨識結果存檔與紀錄讀取
├── frontend/
│   ├── index.html           # 使用者：上傳與辨識
│   ├── admin.html           # 管理員：登入、菜單、辨識紀錄
│   ├── css/styles.css
│   └── js/
│       ├── app.js           # 辨識頁（預覽、畫框、雙結果）
│       └── admin.js         # 管理頁（登入、編輯刪除、匯入、紀錄）
├── data/
│   ├── menu.db              # SQLite（執行後產生）
│   └── uploads/
│       └── YYYYMMDD_HHMMSS/
│           ├── original.jpg
│           ├── result_boxed.jpg
│           ├── result.json
│           ├── result1/     # 有 alternative 才有。主菜名
│           └── result2/     # 有 alternative 才有。次佳菜名，框與 result1 相同
├── docs/
│   └── inference-spec.md    # 雙引擎與同一格合併的判斷規則
├── tests/
│   ├── test_jev_inference.py
│   └── test_plate_recognition_api.py
├── scripts/
│   └── seed_excel.py        # 初次整批匯入 Excel（會清空舊資料）
├── .env                     # 本機密鑰與設定（勿提交 git）
├── .env.example
├── requirements.txt
├── run.bat                  # Windows 啟動腳本（0.0.0.0:8000）
├── zbpack.json
└── README.md
```

---

## 環境變數（`.env`）

| 變數 | 說明 |
|------|------|
| `ZEABUR_AI_HUB_API_KEY` | `chat` 與 `jev` 共用。走 OpenRouter 時填 OpenRouter 的 key |
| `ZEABUR_AI_HUB_BASE_URL` | `chat` 的位址。Zeabur 例：`https://hnd1.aihub.zeabur.ai/`。OpenRouter 例：`https://openrouter.ai/api/v1` |
| `AI_MODEL` | 只給 `chat`。未設定時程式預設 `gpt-5.6-luna` |
| `INFERENCE_ENGINE` | `chat`（未設定時的預設）或 `jev` |
| `JEV_MODEL` | Jev 模型，預設 `typesafe/jev-1.14` |
| `JEV_DECISIONS_URL` | Decisions API，預設 `https://openrouter.ai/api/alpha/decisions` |
| `NOUL_PRESENT_MIN` | Jev：低於這個值就當這道菜不在盤上，預設 `0.50` |
| `CLOSE_CONFIDENCE_DELTA` | 同一格兩道菜的信心差小於或等於這個值，才寫入 `alternative` 並顯示雙結果，預設 `0.30` |
| `ADMIN_PASSWORD` | 管理員登入密碼 |
| `SECRET_KEY` | 簽發管理 token 用的密鑰 |

可複製 `.env.example` 再填入真實值。

外框的上下位移寫在 `backend/bbox_adjust.py`：上排 `0.01`、下排 `0.08`。這是畫圖用的微調，不從環境變數讀取。`.env.example` 裡的 `BBOX_Y_SHIFT` 等註解是舊設定，程式不讀。

修改 `.env` 後要停掉整個伺服器行程再啟動。`uvicorn --reload` 不會重讀 `.env`；金鑰與引擎在程式載入時就讀進記憶體。

---

## 使用 Jev 辨識

網頁還是同一頁。要整站改走 Jev，在 `.env` 設：

```env
INFERENCE_ENGINE=jev
JEV_MODEL=typesafe/jev-1.14
JEV_DECISIONS_URL=https://openrouter.ai/api/alpha/decisions
NOUL_PRESENT_MIN=0.50
```

`ZEABUR_AI_HUB_API_KEY` 填 OpenRouter 的 key。存檔後完整重啟：

```bat
uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
```

啟動日誌要看到 `INFERENCE_ENGINE=jev`。接著打開 http://127.0.0.1:8000/ ，上傳餐盤照片並按「開始辨識」。畫面上的百分比是這道菜在不在的分數（Noul）；外框來自另一題 Box，Box 的分數不用來決定菜在不在。

只想某一次用 Jev、其餘維持看圖對話時，不要改 `INFERENCE_ENGINE`，改打 API：

```bat
curl -F "file=@餐盤.jpg" -F "engine=jev" http://127.0.0.1:8000/api/predict
```

`engine` 只接受 `chat` 或 `jev`。其他值回 400。沒帶這個欄位時，用 `.env` 的 `INFERENCE_ENGINE`。

Jev 對菜單裡的每一道菜問兩題：在不在、外框。外觀文字來自管理頁的「烹調方式」，接到「在不在」那一題，大約 200 字。分數低於 `NOUL_PRESENT_MIN` 的菜不進主結果。兩個框的 IoU 達到 `0.45` 就合成同一格，不論信心差多少：分數高的是主菜，分數低的寫進 `second_best`。信心差小於或等於 `CLOSE_CONFIDENCE_DELTA`（預設 `0.30`，含剛好 0.30）才同時寫入 `alternative`。這時畫面才並排 result1 / result2。差更大時，畫面只留主菜；次佳仍在 `second_best`，食物攝影機的 JSON 則是 `alternative: null`。

Decisions API 回 `429`、`502`、`503`、`529` 時，間隔 2 秒再送，最多再送 2 次。`401`、`400` 這類錯誤不重試。

菜單是空的時，兩種引擎都不呼叫外部 API，回空的 `predictions`。

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

單元測試：

```bat
.\.venv\Scripts\python.exe -m unittest tests.test_plate_recognition_api tests.test_jev_inference
```

---

## 主要 API

| 方法 | 路徑 | 說明 |
|------|------|------|
| POST | `/api/admin/login` | 管理員登入，回傳 token |
| GET | `/api/dishes` | 列出菜單 |
| GET | `/api/dishes/{id}` | 取得單一菜色。找不到回 404「找不到菜色」 |
| PUT | `/api/dishes/{id}` | 更新菜色（需 token） |
| DELETE | `/api/dishes/{id}` | 刪除菜色（需 token） |
| POST | `/api/admin/import-excel` | 匯入 Excel，只追加（需 token） |
| GET | `/api/admin/records?date=YYYY-MM-DD` | 當日辨識紀錄（需 token） |
| GET | `/api/admin/records/{session_id}` | 單次紀錄明細（需 token） |
| POST | `/api/predict` | 上傳餐盤圖辨識。表單可加 `engine=chat` 或 `engine=jev`；成功後寫入 uploads 結果資料夾 |
| POST | `/api/v1/plate-recognitions` | 同一套辨識，回傳給 [smart-athlete-nutrition](https://github.com/mdtsai456/smart-athlete-nutrition) 食物攝影機的 AI worker |
| GET | `/api/health` | 健康檢查 |

管理 API 請在 Header 帶：`Authorization: Bearer <token>`。

`/api/predict` 回傳重點欄位：

- `predictions[]`：`name`、`dish_id`、`dish_code`、`confidence`、`reason`、`bbox`
- `predictions[].second_best`：同一格的次佳。有次佳就寫，只供記錄
- `predictions[].alternative`：與 `second_best` 同一道菜，但只有信心差在 `CLOSE_CONFIDENCE_DELTA` 以內才有。前端靠這個欄位決定要不要雙圖
- `model`：實際使用的模型名稱。`chat` 是 `AI_MODEL`，`jev` 是 `JEV_MODEL`
- `result_dir`：本次存檔資料夾名稱（例如 `20260716_131500`）
- `close_confidence_delta`：這次採用的信心差上限

模型呼叫失敗時，`/api/predict` 回 502，`detail` 前綴是「模型辨識失敗：」。

### 食物攝影機

[smart-athlete-nutrition](https://github.com/mdtsai456/smart-athlete-nutrition) 的食物攝影機不直接呼叫模型。餐點先 `queue_analysis`，之後由 AI worker 拿物件儲存裡的照片來呼叫：

```bash
curl -F "file=@餐盤.jpg" http://127.0.0.1:8000/api/v1/plate-recognitions
```

辨識規則與 `/api/predict` 相同，預設引擎仍是 `.env` 的 `INFERENCE_ENGINE`。成功時 HTTP 200，正文可交給該專案的 `completeMealAnalysis`：

```json
{
  "succeeded": true,
  "errorMessage": null,
  "modelName": "typesafe/jev-1.14",
  "modelVersion": null,
  "resultDir": "20261027_103900",
  "items": [
    {
      "detectedLabel": "季節時蔬(高麗菜)",
      "sourceFoodCode": "17",
      "confidence": 0.87,
      "bbox": [0.27, 0.27, 0.41, 0.315],
      "grams": null,
      "alternative": null
    }
  ]
}
```

- `detectedLabel` 對應餐點項目的辨識菜名。`sourceFoodCode` 是這份菜單的菜色編號（字串），用來之後對上營養平台 `Food.source_food_code`。
- 這份 JSON 沒有 `foodId`。不要把這裡的編號寫進營養平台的 `foodId`。兩邊的資料庫 id 不是同一套。
- `grams` 一律是 `null`。這條辨識不估重量。
- `confidence` 是 0～1。只有信心差在門檻內的次佳才會出現在 `alternative`，而且是巢狀物件，不會變成第二筆 `items`。沒有次佳、或差超過門檻時，`alternative` 是 `null`。`second_best` 不會出現在這份 JSON。
- 照片裡沒有菜單中的菜，仍回 200，`items` 為空陣列。
- 模型失敗回 502，正文是 JSON：`succeeded` 為 `false`，`errorMessage` 是原因，`items` 為空陣列。
- 不是圖片，或 `engine` 不是 `chat` / `jev`，回 400。

---

## 辨識結果 JSON 範例（`result.json`）

```json
{
  "meal_type": "午餐",
  "dishes": [
    {
      "dish_id": "8",
      "dish_name": "黑胡椒杏鮑菇",
      "confidence": 0.85,
      "reason": null,
      "bbox": [0.12, 0.55, 0.28, 0.22],
      "alternative": {
        "dish_id": "6",
        "dish_name": "番茄豆包",
        "confidence": 0.55,
        "reason": null
      }
    },
    {
      "dish_id": "9",
      "dish_name": "季節時蔬(四季豆)",
      "confidence": 0.96,
      "reason": null,
      "bbox": [0.42, 0.2, 0.25, 0.2]
    }
  ],
  "bbox_shift_version": 2
}
```

說明：`dish_id` 為菜單「菜色編號」字串；沒有菜色編號時才用資料庫 id。`meal_type` 取自本次辨識菜色中最常見的餐別。`alternative` 只在信心差達門檻時出現。`bbox_shift_version` 用來分辨外框位移的版本。

---

## 各模組說明（給報告用）

### `backend/main.py`
應用入口：建立資料表、註冊路由、掛載前端與 `uploads`。辨識成功後呼叫 `result_store` 存檔，並回傳結果給前端。查單筆菜色、更新、刪除共用同一個「找不到就回 404」的查詢。

### `backend/auth.py`
簡單管理員驗證：比對 `.env` 密碼；用 `hmac` + `sha256` 簽發有時效（預設 12 小時）的 token，保護編輯／刪除／匯入與辨識紀錄 API。

### `backend/database.py`
載入專案根目錄的 `.env`、設定 SQLite 路徑（`data/menu.db`）、提供 `get_db()` 給每個請求開關資料庫連線。

### `backend/models.py`
`Dish` 資料表，欄位對應台鋼餐飲 Excel（菜名、組成、AI 烹調方式、營養、份量等）。

### `backend/schemas.py`
API 進出資料格式（Pydantic），例如 `DishOut`、`PredictResponse`（含 `bbox`、`alternative`、`second_best`、`result_dir`）、食物攝影機的 `PlateRecognitionOut`。

### `backend/excel_import.py`
讀 Excel 列轉成字典寫入 DB。管理頁匯入預設 **不覆蓋**：菜色編號或菜名已存在則略過。

### `backend/vision.py`
依 `chat` 或 `jev` 辨識。`chat` 把候選菜單（含烹調方式）送進看圖模型，再把菜名對回資料庫。`jev` 對每道菜各問一次「在不在」和「外框」。兩條都會把重疊的框收成同一格，並依信心差決定要不要寫 `alternative`。`bbox` 是相對座標 0～1。

### `backend/bbox_adjust.py`
存檔與回傳前，依框的垂直位置微調外框。上排與下排使用不同常數。這一步只影響畫出來的位置。

### `backend/plate_recognition.py`
把辨識結果轉成食物攝影機 JSON：不含 `foodId`，`grams` 固定 `null`，`alternative` 維持巢狀物件。

### `backend/result_store.py`
建立 `uploads/日期_時間/`，寫入原圖、有外框的結果圖、`result.json`。有 `alternative` 時另建 `result1`（主菜名）與 `result2`（次佳菜名）。外框顏色依本次菜色數量在色環均分。管理頁的紀錄列表也從這裡讀。

### `frontend/js/app.js`
選圖／拖曳、送辨識、用 canvas 畫圖與外框（標籤只顯示菜名）、列出 confidence 卡片（主題色與外框一致）。任一筆有 `alternative` 就並排兩張圖。畫面預覽用本機 `createObjectURL`；伺服器另有完整存檔。

### `frontend/js/admin.js`
登入存 token、表格編輯／刪除、匯入 Excel 並提示新增／略過筆數（不提供手動新增菜色，改以 Excel 匯入）。另可依日期展開辨識紀錄。

---

## 設計取捨（簡述）

1. **菜單來源**：Excel／管理後台維護，辨識時只能從資料庫候選菜中選，避免模型亂發明菜名。
2. **匯入策略**：一般匯入只追加；`seed_excel.py` 才會清空後重匯（初始化用）。
3. **辨識結果存檔**：每次在 `data/uploads/YYYYMMDD_HHMMSS/` 存原圖、標註圖、`result.json`。有接近的次佳菜時另存兩套圖。
4. **外框**：由模型估大概 `bbox`，再做固定的上下微調；顏色依該次菜色數量動態產生。
5. **管理驗證**：單密碼 + 簽章 token，夠用且實作簡單。
6. **AI 入口**：`chat` 與 `jev` 都打外部 API，本機不需自架 GPU。兩條最後都交回同一份 `predictions`。

---

## 部署至 Zeabur

官方 Python／FastAPI 說明可參考：[Deploy Python Projects](https://zeabur.com/docs/en-US/guides/python)

### 部署前準備

1. 將專案推上 **GitHub**（確認 `.env` 已在 `.gitignore`，不要上傳金鑰）
2. 專案根目錄已有 `zbpack.json`，指定進入點為 `backend/main.py`（FastAPI 的 `app`），Python 3.12
3. 本機先能正常跑通辨識與管理功能

### 步驟

1. 登入 [Zeabur](https://zeabur.com/) → 建立 Project
2. **Add Service** → 選 **GitHub** → 選這個 repo
3. 等建置完成後，在服務裡設定 **環境變數**（與 `.env` 相同）：
   - `ZEABUR_AI_HUB_API_KEY`
   - `ZEABUR_AI_HUB_BASE_URL`
   - `AI_MODEL`（`chat` 用）
   - `INFERENCE_ENGINE`（`chat` 或 `jev`）
   - 若要走 Jev：`JEV_MODEL`、`JEV_DECISIONS_URL`、`NOUL_PRESENT_MIN`，且 API Key 須為 OpenRouter
   - `CLOSE_CONFIDENCE_DELTA`（可不設，預設 `0.30`）
   - `ADMIN_PASSWORD`
   - `SECRET_KEY`（請改成夠長的隨機字串）
4. **Networking / Domain**：產生 `*.zeabur.app` 網域或綁自訂網域
5. **Volumes（重要）**：掛載持久化磁碟到服務內的 `data` 目錄
   - 用來保留 `menu.db` 與 `uploads/` 辨識結果
   - 未掛 Volume 時，服務重啟可能清空 SQLite 與上傳檔

### 部署後檢查

- 開啟網域首頁，確認能上傳辨識
- 開啟 `/admin.html`，用 `ADMIN_PASSWORD` 登入，並重新匯入 Excel（Volume 是新的話資料庫是空的）
- 結果頁的「模型：」後面應是這次實際使用的模型：`chat` 為 `AI_MODEL`，`jev` 為 `JEV_MODEL`

### 注意

- **呼叫模型的服務**（Zeabur AI Hub 或 OpenRouter）與 **把這個網站部署到 Zeabur** 是兩件事。上線後仍要設定對應的 API Key 與位址。
- 若建置失敗，到服務 Logs 查看；常見原因是進入點不對或環境變數沒設。
- 掛 Volume 當下該目錄會被清空，請先備份再掛。

---

## 注意事項

- `.env` 含 API Key，已列入 `.gitignore`，請勿提交版本庫。
- 若曾在對話或文件中貼過 Key，建議至金鑰發行端輪替。
- 辨識品質受菜單敘述（尤其「AI 烹調方式」）與模型能力影響。
- 外框為「大概範圍」，適合分格餐盤示意，非專業物件偵測精度。
- 前端畫面用本機預覽顯示圖片；伺服器存檔供後續查閱，兩者分開。
- 同一張餐盤重複辨識時，分數可能在 `0.30` 門檻兩側跳動，畫面就會在單張圖與雙張圖之間切換。這是現有門檻，不是另一次辨識寫壞了前一次的檔案。
