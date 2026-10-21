# Choice 路徑規格

這是第三條推論。`chat` 仍是看圖對話，`jev` 仍是 Noul 加 Box。這條路不取代它們，也不改它們的回傳。

Choice 從同一區的幾道菜裡選一個。它不負責判斷整張餐盤有幾道菜，也不負責畫框。存在與否仍用 Noul，位置仍用 Box。官方說明見 [Choice](https://docs.typesafe.ai/primitives/choice)。

## 目標

`POST /api/predict` 的表單欄位 `engine=choice`，或環境變數 `INFERENCE_ENGINE=choice`，走這條路。沒有指定時仍是 `chat`。

回傳維持現在的 `PredictResponse`。前端、存檔、雙結果畫面不改。`model` 回 `JEV_MODEL`。

同一張圖可以留下多道菜。禁止對整張餐盤只問一個 Choice。

## 兩段請求

同一請求裡的題目看的是同一張圖，而且彼此獨立。框是第一段的答案，裁切要用這個框，所以 Choice 必須是第二段請求。

```
餐盤照片
  │
  ▼
第一段：與 jev 相同的 Noul + Box
  │  低於 NOUL_PRESENT_MIN 或外框無效 → 丟掉
  │  IoU ≥ 0.45 的框合成一格，並留下這一格的全部候選
  ▼
第二段：只對「候選至少兩道」的格子
  │  依該格的框裁圖
  │  一題 Choice，選項是這些候選，再加上「其他」
  ▼
該格改用 Choice 的菜名與機率
候選只有一道的格子不打第二段，維持 Noul 的結果
```

第一段的題目、門檻、外框檢查、IoU，沿用 `docs/inference-spec.md` 的 jev 規則。垂直微調只用於最後畫框，不用來裁圖。

## 什麼叫一格

第一段留下的菜先依 Noul 由高到低排序。兩框 IoU ≥ 0.45 就視為同一格，不論信心差。

合併時要留下這一格的全部候選菜，不能只留主結果和 `second_best`。三道菜框在一起時，三道都要進入後面的 Choice。

沒有跟別人重疊的菜自己成一格。008 這種左、中、右三個框若彼此 IoU 低於 0.45，就是三格，各走各的。Choice 不會把右邊的框改去跟中間比較。

低於 `NOUL_PRESENT_MIN` 的菜不進入候選，即使它的框和這一格重疊。

## 何時打 Choice

| 這一格的候選數 | 動作 |
|---|---|
| 0 | 不會出現。第一段沒留下菜就沒有這一格 |
| 1 | 不打 Choice。菜名、信心、外框都用第一段的 Noul 結果 |
| 2 以上 | 打一次 Choice |

因此同一張照片的回應裡，有的百分比來自 Noul，有的來自 Choice 機率。日誌要標出這一格走了哪一種。

## 裁圖

裁切用這一格 Noul 最高那道菜的框，格式是 `[x, y, w, h]`，座標是轉正後的整張圖。四邊各往外 0.02，再夾回 0～1。不把同格其他框聯集進來，避免框進旁邊的另一道菜。

裁下來的圖再走 Jev 的 JPEG 規則：EXIF 轉正、不送 WebP、邊長與像素落在 Jev 接受的範圍。

## Choice 請求

每一格各打一次 `JEV_DECISIONS_URL`。金鑰與模型與 jev 相同：`ZEABUR_AI_HUB_API_KEY`、`JEV_MODEL`。

`state` 只有這張裁圖，加上一句「這是餐盤上的一個食物區域。請只依這個區域判斷。」

只有一題，鍵名 `region`：

| 欄位 | 值 |
|---|---|
| `type` | `choice` |
| `instructions` | 這個區域的主要食物最符合哪一項？依食物本身的外觀判斷。 |
| `criteria` | 每個候選菜名對應它的烹調方式，最多 200 字。烹調方式是空的就寫「菜單中的這道菜」。另外固定一個選項「其他」，說明是「以上都不是這個區域的主要食物。」 |

菜名當選項鍵，才能對回資料庫。不要把整份菜單放進 `criteria`。

## 答案怎麼用

Choice 回傳 `choice`、`probabilities`、`confidence`。`probabilities` 加總為 1，是選項之間的相對比較。`confidence` 只表示這個分布有多集中，不用來當菜色分數，也不拿來決定留不留。

| Choice 結果 | 這一格的輸出 |
|---|---|
| `choice` 是候選菜名 | 菜名、`dish_id`、`dish_code` 改成那道菜。`confidence` 改成 `probabilities` 裡該菜的機率。外框仍用裁圖前的那一格 |
| `choice` 是「其他」 | 這一格不輸出。不退回 Noul 的菜名 |
| 第二段 HTTP 不是 200，或缺少 `answers.region` | 整次辨識失敗，回 502，刪掉暫存圖。不要悄悄改回第一段結果 |

`second_best` 取「其他」以外、機率次高的那道菜。沒有第二道菜就空著。

`alternative` 沿用現在的規則：主結果與 `second_best` 的分數差 ≤ `CLOSE_CONFIDENCE_DELTA`（0.30）才填。這裡的分數是 Choice 機率，不是 Noul。差更大時只留在 `second_best`。

`reason` 留空。機率分布寫進日誌，例如 `[debug] choice 黑胡椒杏鮑菇 0.86 / 番茄豆包 0.11 / 其他 0.03`。

只有一道候選、沒打 Choice 的格子，日誌寫 `[debug] choice 略過 季節時蔬(四季豆)，候選只有一道`。它的 `confidence` 仍是 Noul。

## 不做的事

- 不修改 `chat` 與 `jev` 的題目、合併和回傳。
- 不對整張圖問「這是哪一道菜」。
- 不用 Score。
- 不降低 `NOUL_PRESENT_MIN`。
- 不依菜名寫不同的外框題或 Choice 題。
- Choice 不新增外框。同一道菜在盤上有兩堆、第一段只回一個框時，這條路也只會有一個框。

## 驗收

用假的 Decisions 回覆測，不打外部 API。

- `engine=jev` 的既有測試結果不變，而且不會出現第二段請求。
- `engine=choice`，第一段只留下一道菜：不打第二段，回應與 jev 相同。
- `engine=choice`，兩框 IoU ≥ 0.45，Noul 分別是 0.82 與 0.69。第二段 `probabilities` 為杏鮑菇 0.86、豆包 0.11、其他 0.03：回應只有杏鮑菇，`confidence` 是 0.86，`second_best` 是豆包，沒有 `alternative`。
- 同上，但機率是 0.55 與 0.40：主結果帶 `alternative`，畫面會雙條。
- `choice` 回「其他」：這一格不在 `predictions` 裡。
- 三個框兩兩 IoU 都低於 0.45：不打 Choice，三道菜都留著，信心仍是各自的 Noul。
- 第二段回 500：整次 502，不回第一段的菜名。
- `engine=nope` 仍回 400。
