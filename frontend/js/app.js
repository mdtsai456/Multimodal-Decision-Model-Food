// 使用者頁：上傳餐盤圖並顯示辨識結果

// --- DOM 元素 ---
const toastEl = document.getElementById("toast");
const plateInput = document.getElementById("plate-input");   // 隱藏的 file input
const dropzone = document.getElementById("dropzone");         // 拖曳／點擊上傳區
const preview = document.getElementById("preview");           // 預覽圖
const predictBtn = document.getElementById("predict-btn");
const predictForm = document.getElementById("predict-form");
const resultsPanel = document.getElementById("results-panel"); // 結果整塊區塊
const resultCanvas = document.getElementById("result-canvas"); // 結果圖 + 外框
const resultBoxedPair = document.getElementById("result-boxed-pair");
const resultBoxed1 = document.getElementById("result-boxed-1");
const resultBoxed2 = document.getElementById("result-boxed-2");
const resultsList = document.getElementById("results-list");   // 結果卡片列表
const emptyResult = document.getElementById("empty-result");   // 沒辨到菜時的提示
const modelTag = document.getElementById("model-tag");         // 顯示目前模型名稱

let selectedFile = null; // 目前選到要辨識的圖片
let previewUrl = null;   // createObjectURL 產生的本機網址（用完要 revoke）

// 依這次辨識的菜色數量，在色環上均分色相，保證每道菜顏色不同
function colorForIndex(index, total) {
  const n = Math.max(total, 1);
  const hue = Math.round((index * 360) / n) % 360;
  return `hsl(${hue}, 58%, 40%)`;
}

// 雙候選右側用略偏的色相，與左側區隔
function altColorForIndex(index, total) {
  const n = Math.max(total, 1);
  const hue = Math.round((index * 360) / n + 28) % 360;
  return `hsl(${hue}, 52%, 38%)`;
}

// 畫面底部短暫提示訊息
function showToast(message) {
  toastEl.textContent = message;
  toastEl.classList.remove("hidden");
  clearTimeout(showToast._t);
  showToast._t = setTimeout(() => toastEl.classList.add("hidden"), 2800);
}

// 設定預覽圖，並依有無檔案啟用／停用「開始辨識」按鈕
function setPreview(file) {
  selectedFile = file;
  predictBtn.disabled = !file;

  // 釋放舊的本機預覽網址，避免記憶體佔用
  if (previewUrl) {
    URL.revokeObjectURL(previewUrl);
    previewUrl = null;
  }

  if (!file) {
    preview.classList.add("hidden");
    preview.removeAttribute("src");
    resultCanvas.classList.add("hidden");
    hideBoxedPair();
    return;
  }
  previewUrl = URL.createObjectURL(file); // 本機預覽，不用靠伺服器存檔
  preview.src = previewUrl;
  preview.classList.remove("hidden");
}

// 用檔案選擇器選圖
plateInput.addEventListener("change", () => {
  setPreview(plateInput.files?.[0] || null);
});

// --- 拖曳上傳 ---
["dragenter", "dragover"].forEach((evt) => {
  dropzone.addEventListener(evt, (e) => {
    e.preventDefault();
    dropzone.classList.add("dragover");
  });
});
["dragleave", "drop"].forEach((evt) => {
  dropzone.addEventListener(evt, (e) => {
    e.preventDefault();
    dropzone.classList.remove("dragover");
  });
});
dropzone.addEventListener("drop", (e) => {
  const file = e.dataTransfer?.files?.[0];
  if (file && file.type.startsWith("image/")) setPreview(file);
  else showToast("請放置圖片檔");
});

function pct(confidence) {
  return `${Math.round(Number(confidence) * 100)}%`;
}

function escapeHtml(text) {
  return String(text)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

// 在 canvas 上畫原圖，並依 bbox 框出每道菜
function drawResultImage(items) {
  if (!previewUrl) {
    resultCanvas.classList.add("hidden");
    return;
  }

  const img = new Image();
  img.onload = () => {
    // 結果圖縮小顯示，避免佔滿整欄
    const maxW = Math.min(420, resultsPanel.clientWidth || 420);
    const scale = Math.min(1, maxW / img.width);
    const w = Math.round(img.width * scale);
    const h = Math.round(img.height * scale);

    resultCanvas.width = w;
    resultCanvas.height = h;
    resultCanvas.classList.remove("hidden");

    const ctx = resultCanvas.getContext("2d");
    ctx.clearRect(0, 0, w, h);
    ctx.drawImage(img, 0, 0, w, h);

    items.forEach((item, index) => {
      const box = item.bbox;
      if (!Array.isArray(box) || box.length !== 4) return;

      const [x, y, bw, bh] = box;
      const color = colorForIndex(index, items.length);
      const px = x * w;
      const py = y * h;
      const pw = bw * w;
      const ph = bh * h;

      // 外框
      ctx.lineWidth = Math.max(2, Math.round(w / 220));
      ctx.strokeStyle = color;
      ctx.strokeRect(px, py, pw, ph);

      // 有相近候選時標籤並排顯示兩個菜名
      const alt = getAlternative(item);
      const label = alt
        ? `${item.name || ""}／${alt.name}`
        : item.name || "";
      ctx.font = `600 ${Math.max(12, Math.round(w / 40))}px "Noto Sans TC", sans-serif`;
      const pad = 6;
      const tw = ctx.measureText(label).width;
      const th = Math.max(16, Math.round(w / 36));
      const ly = Math.max(0, py - th - 4);
      ctx.fillStyle = color;
      ctx.fillRect(px, ly, tw + pad * 2, th + 4);

      ctx.fillStyle = "#fff";
      ctx.fillText(label, px + pad, ly + th - 2);
    });
  };
  img.src = previewUrl;
}

function halfMeta(item) {
  const code = item.dish_code != null ? `編號 ${item.dish_code}` : "—";
  const reason = item.reason ? ` · ${escapeHtml(item.reason)}` : "";
  return `${code}${reason}`;
}

function getAlternative(item) {
  const alt = item?.alternative;
  if (!alt || typeof alt !== "object") return null;
  if (!alt.name) return null;
  return alt;
}

function renderHalf(item, color) {
  return `
    <div class="result-half" style="border-left-color:${color}">
      <div>
        <div class="result-name">${escapeHtml(item.name)}</div>
        <div class="result-meta">${halfMeta(item)}</div>
      </div>
      <div class="confidence" style="color:${color}">${pct(item.confidence)}</div>
      <div class="confidence-bar"><span style="width:${pct(item.confidence)};background:${color}"></span></div>
    </div>
  `;
}

function showBoxedPair(resultDir) {
  if (!resultDir || !resultBoxedPair) {
    resultCanvas.classList.remove("hidden");
    resultBoxedPair?.classList.add("hidden");
    return;
  }
  const bust = Date.now();
  resultBoxed1.src = `/uploads/${encodeURIComponent(resultDir)}/result1/result_boxed.jpg?t=${bust}`;
  resultBoxed2.src = `/uploads/${encodeURIComponent(resultDir)}/result2/result_boxed.jpg?t=${bust}`;
  resultBoxedPair.classList.remove("hidden");
  resultCanvas.classList.add("hidden");
}

function hideBoxedPair() {
  resultBoxedPair?.classList.add("hidden");
  if (resultBoxed1) resultBoxed1.removeAttribute("src");
  if (resultBoxed2) resultBoxed2.removeAttribute("src");
}

// 把後端辨識結果畫成卡片，並在圖上框出每道菜
function renderResults(data) {
  resultsPanel.hidden = false;
  modelTag.textContent = `模型：${data.model || "-"}`;
  resultsList.innerHTML = "";

  const items = data.predictions || [];
  const closePairs = items.filter((item) => getAlternative(item));

  console.log("[debug] predictions", items);
  items.forEach((item) => {
    const second = item.second_best || item.alternative;
    const best = `${item.name} (${pct(item.confidence)})`;
    if (second) {
      const flag = item.alternative ? "採用雙結果" : "僅 debug（未存 result2）";
      console.log(
        `[debug] 最佳/次佳: ${best} / ${second.name} (${pct(second.confidence)}) [${flag}]`
      );
    } else {
      console.log(`[debug] 最佳/次佳: ${best} / 無次佳`);
    }
  });
  console.log(
    "[debug] close pairs (有 alternative)",
    closePairs.map(
      (p) =>
        `${p.name} (${pct(p.confidence)}) / ${p.alternative.name} (${pct(p.alternative.confidence)})`
    )
  );

  emptyResult.classList.toggle("hidden", items.length > 0);

  if (closePairs.length > 0 && data.result_dir) {
    // 有相近雙候選：顯示後端存的 result1 / result2 標註圖
    showBoxedPair(data.result_dir);
  } else {
    hideBoxedPair();
    drawResultImage(items);
  }

  items.forEach((item, index) => {
    const color = colorForIndex(index, items.length);
    const card = document.createElement("article");
    card.style.animationDelay = `${index * 60}ms`;
    const alt = getAlternative(item);

    if (alt) {
      // 信心相近：左右各半條（例如 醬燒蘿蔔 | 醬燒冬瓜）
      const colorAlt = altColorForIndex(index, items.length);
      card.className = "result-card result-card--split";
      card.innerHTML = renderHalf(item, color) + renderHalf(alt, colorAlt);
    } else {
      card.className = "result-card";
      card.style.borderLeft = `4px solid ${color}`;
      card.innerHTML = `
        <div>
          <div class="result-name">${escapeHtml(item.name)}</div>
          <div class="result-meta">${halfMeta(item)}</div>
        </div>
        <div class="confidence" style="color:${color}">${pct(item.confidence)}</div>
        <div class="confidence-bar"><span style="width:${pct(item.confidence)};background:${color}"></span></div>
      `;
    }
    resultsList.appendChild(card);
  });
}

// 送出表單：上傳圖片到 /api/predict 並顯示結果
predictForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  if (!selectedFile) return;

  predictBtn.disabled = true;
  predictBtn.textContent = "辨識中…";

  const formData = new FormData();
  formData.append("file", selectedFile);

  try {
    const res = await fetch("/api/predict", { method: "POST", body: formData });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "辨識失敗");
    renderResults(data);
    showToast("辨識完成");
  } catch (err) {
    showToast(err.message || "辨識失敗");
  } finally {
    predictBtn.disabled = !selectedFile;
    predictBtn.textContent = "開始辨識";
  }
});
