// 上傳餐盤圖並顯示辨識結果。

const toastEl = document.getElementById("toast");
const plateInput = document.getElementById("plate-input");
const dropzone = document.getElementById("dropzone");
const preview = document.getElementById("preview");
const predictBtn = document.getElementById("predict-btn");
const predictForm = document.getElementById("predict-form");
const resultsPanel = document.getElementById("results-panel");
const resultCanvas = document.getElementById("result-canvas");
const resultBoxedPair = document.getElementById("result-boxed-pair");
const resultCanvas1 = document.getElementById("result-canvas-1");
const resultCanvas2 = document.getElementById("result-canvas-2");
const resultsList = document.getElementById("results-list");
const emptyResult = document.getElementById("empty-result");
const modelTag = document.getElementById("model-tag");

let selectedFile = null;
let previewUrl = null;

// 依菜色數量均分色相。每道菜使用不同顏色。
function colorForIndex(index, total) {
  const n = Math.max(total, 1);
  const hue = Math.round((index * 360) / n) % 360;
  return `hsl(${hue}, 58%, 40%)`;
}

// 次佳使用偏移 28 度的色相。
function altColorForIndex(index, total) {
  const n = Math.max(total, 1);
  const hue = Math.round((index * 360) / n + 28) % 360;
  return `hsl(${hue}, 52%, 38%)`;
}

// 在畫面底部顯示訊息 2.8 秒。
function showToast(message) {
  toastEl.textContent = message;
  toastEl.classList.remove("hidden");
  clearTimeout(showToast._t);
  showToast._t = setTimeout(() => toastEl.classList.add("hidden"), 2800);
}

// 設定預覽圖。沒有檔案時停用「開始辨識」。
function setPreview(file) {
  selectedFile = file;
  predictBtn.disabled = !file;

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
  previewUrl = URL.createObjectURL(file);
  preview.src = previewUrl;
  preview.classList.remove("hidden");
}

plateInput.addEventListener("change", () => {
  setPreview(plateInput.files?.[0] || null);
});

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

// 將 0 到 1 的信心分數轉成百分比文字。
function pct(confidence) {
  return `${Math.round(Number(confidence) * 100)}%`;
}

// 將文字中的 HTML 特殊字元轉成實體。
function escapeHtml(text) {
  return String(text)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

// 卡片次列顯示菜色編號與理由。
function halfMeta(item) {
  const code = item.dish_code != null ? `編號 ${item.dish_code}` : "—";
  const reason = item.reason ? ` · ${escapeHtml(item.reason)}` : "";
  return `${code}${reason}`;
}

// 有 alternative 且有菜名時回傳該物件。
function getAlternative(item) {
  const alt = item?.alternative;
  if (!alt || typeof alt !== "object") return null;
  if (!alt.name) return null;
  return alt;
}

// 雙結果卡片的左半或右半。
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

// 在 canvas 畫原圖與外框。每格只畫一個菜名。
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
      const pw = bw * w;
      const ph = bh * h;

      ctx.lineWidth = Math.max(2, Math.round(w / 220));
      ctx.strokeStyle = color;
      ctx.strokeRect(px, py, pw, ph);

      const label = item.name || "";
      if (!label) return;
      ctx.font = `600 ${Math.max(12, Math.round(w / 40))}px "Noto Sans TC", sans-serif`;
      const pad = 6;
      const tw = ctx.measureText(label).width;
      const th = Math.max(16, Math.round(w / 36));

      // 標籤位於 bbox 的水平中心。垂直位置在中線上方。
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

// result1 只畫主預測菜名。bbox 與 result2 相同。
function toResult1Items(items) {
  return (items || []).map((item) => ({
    name: item.name,
    dish_code: item.dish_code,
    confidence: item.confidence,
    reason: item.reason,
    bbox: item.bbox,
  }));
}

// result2 有次佳時畫次佳菜名。bbox 與 result1 相同。
function toResult2Items(items) {
  return (items || []).map((item) => {
    const src = getAlternative(item) || item;
    return {
      name: src.name,
      dish_code: src.dish_code,
      confidence: src.confidence,
      reason: src.reason,
      bbox: item.bbox,
    };
  });
}

// 有 alternative 時並排顯示兩張標註圖。
function showDualCanvases(items) {
  resultCanvas.classList.add("hidden");
  resultBoxedPair?.classList.remove("hidden");
  paintBoxedCanvas(resultCanvas1, toResult1Items(items));
  paintBoxedCanvas(resultCanvas2, toResult2Items(items));
}

function hideBoxedPair() {
  resultBoxedPair?.classList.add("hidden");
}

// 只顯示一張標註圖。
function showSingleCanvas(items) {
  hideBoxedPair();
  paintBoxedCanvas(resultCanvas, toResult1Items(items));
}

// 將辨識結果畫成卡片，並在圖上畫外框。
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

// 將圖片上傳到 /api/predict，並顯示結果。
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
