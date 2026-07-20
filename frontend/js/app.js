// 使用者頁：上傳餐盤圖並顯示辨識結果

// --- DOM 元素 ---
const toastEl = document.getElementById("toast");
const plateInput = document.getElementById("plate-input");   // 隱藏的 file input
const dropzone = document.getElementById("dropzone");         // 拖曳／點擊上傳區
const preview = document.getElementById("preview");           // 預覽圖
const predictBtn = document.getElementById("predict-btn");
const predictForm = document.getElementById("predict-form");
const resultsPanel = document.getElementById("results-panel"); // 結果整塊區塊
const resultCanvas = document.getElementById("result-canvas"); // 單結果標註圖
const resultBoxedPair = document.getElementById("result-boxed-pair");
const resultCanvas1 = document.getElementById("result-canvas-1");
const resultCanvas2 = document.getElementById("result-canvas-2");
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

// 信心分數 0~1 → 顯示用百分比字串
function pct(confidence) {
  return `${Math.round(Number(confidence) * 100)}%`;
}

// 避免菜名／理由含 HTML 特殊字元造成 XSS
function escapeHtml(text) {
  return String(text)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

// 卡片次要資訊：菜色編號 + 模型理由
function halfMeta(item) {
  const code = item.dish_code != null ? `編號 ${item.dish_code}` : "—";
  const reason = item.reason ? ` · ${escapeHtml(item.reason)}` : "";
  return `${code}${reason}`;
}

// 取出「已採用」的次佳候選（有 alternative 才會雙條顯示）
function getAlternative(item) {
  const alt = item?.alternative;
  if (!alt || typeof alt !== "object") return null;
  if (!alt.name) return null;
  return alt;
}

// 雙結果卡片的左／右半邊 HTML
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

// 在指定 canvas 上畫原圖與外框（瀏覽器繁中字型；不合併雙菜名）
function paintBoxedCanvas(canvas, items) {
  if (!canvas || !previewUrl) {
    canvas?.classList.add("hidden");
    return;
  }

  const img = new Image();
  img.onload = () => {
    const maxW = Math.min(420, resultsPanel.clientWidth || 420);
    const scale = Math.min(1, maxW / img.width);
    const w = Math.round(img.width * scale);
    const h = Math.round(img.height * scale);

    canvas.width = w;
    canvas.height = h;
    canvas.classList.remove("hidden");

    const ctx = canvas.getContext("2d");
    ctx.clearRect(0, 0, w, h);
    ctx.drawImage(img, 0, 0, w, h);

    items.forEach((item, index) => {
      const box = item.bbox;
      if (!Array.isArray(box) || box.length !== 4) return;

      const [x, y, bw, bh] = box;
      const color = colorForIndex(index, items.length);
      const px = x * w;
      const py = y * h;
      const pw = bw * w; // bbox 寬（標籤置中用；暫時不畫外框）
      const ph = bh * h; // bbox 高（標籤偏上用；暫時不畫外框）

      // 暫時不顯示外框矩形，只保留菜名標籤（bbox 座標仍用來定位標籤）
      // ctx.lineWidth = Math.max(2, Math.round(w / 220));
      // ctx.strokeStyle = color;
      // ctx.strokeRect(px, py, pw, ph);

      const label = item.name || "";
      if (!label) return;
      ctx.font = `600 ${Math.max(12, Math.round(w / 40))}px "Noto Sans TC", sans-serif`;
      const pad = 6;
      const tw = ctx.measureText(label).width;
      const th = Math.max(16, Math.round(w / 36));

      // --- 原本：標籤貼在外框左上角上方 ---
      // const ly = Math.max(0, py - th - 4);
      // ctx.fillStyle = color;
      // ctx.fillRect(px, ly, tw + pad * 2, th + 4);
      // ctx.fillStyle = "#fff";
      // ctx.fillText(label, px + pad, ly + th - 2);

      // --- 現在：標籤在 bbox 水平置中、垂直中間偏上 ---
      const labelW = tw + pad * 2;
      const labelH = th + 4;
      const lx = px + (pw - labelW) / 2;
      const ly = py + ph * 0.2 - labelH / 2;
      const clampedX = Math.max(0, Math.min(w - labelW, lx));
      const clampedY = Math.max(0, Math.min(h - labelH, ly));
      ctx.fillStyle = color;
      ctx.fillRect(clampedX, clampedY, labelW, labelH);
      ctx.fillStyle = "#fff";
      ctx.fillText(label, clampedX + pad, clampedY + th - 2);
    });
  };
  img.src = previewUrl;
}

// result1：每格只畫主預測菜名（共用 bbox）
function toResult1Items(items) {
  return (items || []).map((item) => ({
    name: item.name,
    dish_code: item.dish_code,
    confidence: item.confidence,
    reason: item.reason,
    bbox: item.bbox,
  }));
}

// result2：有次佳則改畫次佳菜名，bbox 仍用同一格
function toResult2Items(items) {
  return (items || []).map((item) => {
    const alt = getAlternative(item);
    if (!alt) {
      return {
        name: item.name,
        dish_code: item.dish_code,
        confidence: item.confidence,
        reason: item.reason,
        bbox: item.bbox,
      };
    }
    return {
      name: alt.name,
      dish_code: alt.dish_code,
      confidence: alt.confidence,
      reason: alt.reason,
      bbox: item.bbox,
    };
  });
}

// 信心相近時並排顯示兩張標註圖
function showDualCanvases(items) {
  resultCanvas.classList.add("hidden");
  resultBoxedPair?.classList.remove("hidden");
  paintBoxedCanvas(resultCanvas1, toResult1Items(items));
  paintBoxedCanvas(resultCanvas2, toResult2Items(items));
}

function hideBoxedPair() {
  resultBoxedPair?.classList.add("hidden");
}

// 一般情況：只顯示一張標註圖
function showSingleCanvas(items) {
  hideBoxedPair();
  paintBoxedCanvas(resultCanvas, toResult1Items(items));
}

// 把後端辨識結果畫成卡片，並在圖上框出每道菜
function renderResults(data) {
  resultsPanel.hidden = false;
  modelTag.textContent = `模型：${data.model || "-"}`;
  resultsList.innerHTML = "";

  const items = data.predictions || [];
  const closePairs = items.filter((item) => getAlternative(item));

  console.log("[debug] predictions", items);
  console.log(
    `[debug] 雙結果門檻 CLOSE_CONFIDENCE_DELTA=${data.close_confidence_delta ?? "?"}`
  );
  items.forEach((item) => {
    const second = item.second_best || item.alternative;
    const best = `${item.name} (${pct(item.confidence)})`;
    if (second) {
      const delta = Math.abs(Number(item.confidence) - Number(second.confidence));
      const flag = item.alternative ? "採用雙結果" : "僅 debug（未存 result2）";
      console.log(
        `[debug] 最佳/次佳: ${best} / ${second.name} (${pct(second.confidence)}) ` +
          `差=${delta.toFixed(2)} [${flag}]`
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

  if (closePairs.length > 0) {
    // 雙結果：只顯示 result1 / result2 兩張（canvas 繁中），不畫合併圖
    showDualCanvases(items);
  } else {
    showSingleCanvas(items);
  }

  items.forEach((item, index) => {
    const color = colorForIndex(index, items.length);
    const card = document.createElement("article");
    card.style.animationDelay = `${index * 60}ms`;
    const alt = getAlternative(item);

    if (alt) {
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
