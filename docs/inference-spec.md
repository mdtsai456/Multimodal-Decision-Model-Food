# 餐盤辨識雙引擎規格

舊的看圖對話維持預設。Jev 是另一條推論。兩條最後都交回同一份 `predictions`，前端、存檔和雙結果畫面不用改。

## 目標

`POST /api/predict` 仍上傳餐盤圖、讀完整菜單、存 `uploads/日期_時間/`，回傳現在的 `PredictResponse`。差別只在圖怎麼變成 `predictions`。

兩條分支的回傳都要長這樣：

| 欄位 | 型別 | 意義 |
|---|---|---|
| `name` | 菜單菜名 | 以資料庫為準 |
| `dish_id` | int | 資料庫 id |
| `dish_code` | int 或空 | Excel 菜色編號 |
| `confidence` | 0～1 | 這道菜在盤上的信心 |
| `reason` | 字串或空 | Jev 沒有文字理由，留空 |
| `bbox` | `[x, y, w, h]`，0～1 | 相對整張圖的外框 |
| `second_best` | 次佳或空 | 同一格的第二名，只供記錄 |
| `alternative` | 次佳或空 | 信心差 ≤ `CLOSE_CONFIDENCE_DELTA` 才填，前端才會雙條顯示 |

## 分支選擇

環境變數 `INFERENCE_ENGINE` 決定預設。這份規格只規定 `chat` 與 `jev`。沒設定時用 `chat`，現有行為不變。`choice` 是另一條路，見 `docs/choice-path-spec.md`，不改變下面兩條分支的結果。

`POST /api/predict` 可另帶表單欄位 `engine`。有送就覆寫這一次；值不是 `chat`、`jev` 或 `choice` 時回 400。沒送就用環境變數。

`AI_MODEL` 繼續只給舊分支。Jev 用 `JEV_MODEL`，預設 `typesafe/jev-1.14`。

## chat 分支

把現在的 `recognize_dishes()` 內容收成 `recognize_dishes_chat()`。提示詞、`chat/completions`、菜名對回菜單、次佳與重疊合併都留在這條。使用 `ZEABUR_AI_HUB_BASE_URL` 與 `AI_MODEL`。

這條的重疊合併維持現況：IoU ≥ 0.45 **且**信心差 ≤ 0.30 才合成雙結果。不要改成 Jev 的合併規則。

## jev 分支

`recognize_dishes_jev()` 在 EXIF 轉正之後執行。

`https://openrouter.ai/api/alpha/decisions` 是 Decisions API 的網址，不是模型。`typesafe/jev-1.14` 是請求身體裡的 `model`。外框題只有 Jev 1.14 支援。

### 圖片前處理

送出前把圖轉成 JPEG。寬或高超過 1568、或總像素超過 2,458,624 就等比縮小。任一邊小於 256、或總像素小於 78,400 就等比放大。Jev 不收 WebP，也不讀 EXIF。

### 請求

對 `JEV_DECISIONS_URL`（預設 `https://openrouter.ai/api/alpha/decisions`）發一次 POST。金鑰仍用 `ZEABUR_AI_HUB_API_KEY`，放在 `Authorization: Bearer`。不要用 OpenAI chat client。

`state` 放一句短說明，以及 `data:image/jpeg;base64,...`。

每一道菜問兩題，鍵用資料庫 id：

- `present_{id}`：`noul`。說明含菜名，以及截到約 200 字的烹調／外觀敘述。
- `box_{id}`：`box`。說明是「照片中的這一道〈菜名〉」。

菜不在圖裡時，Box 仍會回一個框。有沒有這道菜只看 Noul。

### 答案轉換

1. `confidence` 用 `answers.present_{id}.noul`。
2. 框從 `left` / `top` / `right` / `bottom` 轉成 `[left, top, right-left, bottom-top]`，再走現有的框檢查。太小或無效就當沒有框。
3. `noul` 低於 `NOUL_PRESENT_MIN`（預設 `0.50`）的菜不進主結果。
4. Box 的 `confidence` 只代表框的位置有多確定，不用來決定這道菜在不在。
5. 題目鍵已帶資料庫 id，不必再做菜名模糊比對。
6. `reason` 留空。
7. `raw` 存 Decisions API 的原始 JSON。`model` 回 `JEV_MODEL`。

### 同一格合併

Jev 每一題獨立判斷，不會知道另一題已經指到同一格。外觀接近的菜（例如炒青菜與炒高麗菜）可能兩題 Noul 都過門檻，兩題 Box 也框住同一塊食物。若不合併，這一格會變成兩道菜。

過了 `NOUL_PRESENT_MIN`、而且外框有效的菜，先依信心由高到低排序。任兩筆 IoU ≥ 0.45，就視為同一格，不論信心差多少，合成一筆：

- 分數高的留為主結果。
- 分數低的寫進 `second_best`。
- 兩分相差 ≤ `CLOSE_CONFIDENCE_DELTA`（0.30）時，才同時寫入 `alternative`。前端這時才顯示雙條、`result1` 和 `result2`。
- 差距更大時，畫面上只留主結果；次佳仍留在 `second_best`，供記錄使用。

三筆以上框在同一區時，重複這個比較，直到該區只剩一筆主結果。

合併之後，主結果若還沒有 `second_best`，再從未過門檻的菜裡找 IoU ≥ 0.45 且信心最高的一筆。找到就寫入 `second_best`；與主結果的差距也在 0.30 以內時，一併寫入 `alternative`。

不要直接呼叫現有的 `_merge_overlapping_close_pairs`。那個函式要 IoU 夠高，而且信心差 ≤ 0.30 才合併。0.88 和 0.51 框住同一格時，差距 0.37，舊函式會把它們留成兩道菜。

最後仍沿用現有的外框垂直微調。

同一道菜若在盤上出現兩份，一題 Box 只會回一個框。以目前菜單用量，這個限制可以接受。

不要把整份菜單放進同一題 `choice`。那一題只會對整張圖選一道菜，分格餐盤上的其他菜會消失，也沒有每格的框。

## 共用與錯誤

`recognize_dishes(image_path, dishes, engine)` 用列舉分流。`chat` 與 `jev` 之外要以 `never` 讓型別檢查失敗。菜單是空的，兩條都回空的 `predictions`，不呼叫外部 API。

Jev 的 HTTP 不是 200、或回覆缺 `answers`，丟出例外。`main.py` 維持現在的做法：刪掉暫存圖，回 502，訊息前綴仍是「模型辨識失敗」。

## 設定

```env
INFERENCE_ENGINE=chat
AI_MODEL=google/gemini-2.5-flash
JEV_MODEL=typesafe/jev-1.14
JEV_DECISIONS_URL=https://openrouter.ai/api/alpha/decisions
NOUL_PRESENT_MIN=0.50
```

`CLOSE_CONFIDENCE_DELTA` 維持 0.30，兩條分支共用。

啟動日誌要印出 `INFERENCE_ENGINE`、`AI_MODEL`、`JEV_MODEL`、`NOUL_PRESENT_MIN`。Jev 每次辨識再印每道菜的 Noul，以及留下或丟掉。

## 驗收

- 不帶 `engine`、預設 `chat`：仍打舊的看圖介面，回應裡的 `model` 是 `AI_MODEL`。
- `engine=jev`：打 Decisions API，`model` 是 `typesafe/jev-1.14`。低於 0.50 的菜不出現。
- `engine=jev` 時，若「炒青菜」0.88、「炒高麗菜」0.51，兩框 IoU ≥ 0.45：回應只有一筆主結果「炒青菜」。`second_best` 是「炒高麗菜」，沒有 `alternative`，畫面不出現雙條。若兩者變成 0.72 和 0.61，則主結果帶 `alternative`，畫面顯示雙條。
- `engine=nope` 回 400。Jev 連線失敗回 502，上傳暫存檔不留下。
- WebP 或帶 EXIF 旋轉的照片，標註框和轉正後的圖對得起來。
- `engine=choice` 的驗收不在這裡，見 `docs/choice-path-spec.md`。
