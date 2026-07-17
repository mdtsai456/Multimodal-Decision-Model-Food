// 管理頁：登入、菜單管理、辨識紀錄管理

// --- DOM 元素 ---
const TOKEN_KEY = "healthweb_admin_token";
const toastEl = document.getElementById("toast");
const loginPanel = document.getElementById("login-panel");
const adminPanel = document.getElementById("admin-panel");
const loginForm = document.getElementById("login-form");
const logoutBtn = document.getElementById("logout-btn");
const dishTbody = document.getElementById("dish-tbody");
const excelInput = document.getElementById("excel-input");
const dialog = document.getElementById("dish-dialog");
const dishForm = document.getElementById("dish-form");
const dialogTitle = document.getElementById("dialog-title");
const cancelDialog = document.getElementById("cancel-dialog");
const recordDateInput = document.getElementById("record-date");
const recordsList = document.getElementById("records-list");
const recordsEmpty = document.getElementById("records-empty");
const tabMenu = document.getElementById("tab-menu");
const tabRecords = document.getElementById("tab-records");

let expandedRecordId = null;

function showToast(message) {
  toastEl.textContent = message;
  toastEl.classList.remove("hidden");
  clearTimeout(showToast._t);
  showToast._t = setTimeout(() => toastEl.classList.add("hidden"), 2800);
}

function getToken() {
  return localStorage.getItem(TOKEN_KEY) || "";
}

function setToken(token) {
  if (token) localStorage.setItem(TOKEN_KEY, token);
  else localStorage.removeItem(TOKEN_KEY);
}

function authHeaders(extra = {}) {
  const token = getToken();
  return {
    ...extra,
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  };
}

function showAdmin(isLoggedIn) {
  loginPanel.classList.toggle("hidden", isLoggedIn);
  adminPanel.classList.toggle("hidden", !isLoggedIn);
}

function escapeHtml(text) {
  return String(text ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

async function api(url, options = {}) {
  const res = await fetch(url, options);
  let data = null;
  try {
    data = await res.json();
  } catch {
    data = null;
  }
  if (!res.ok) {
    if (res.status === 401) {
      setToken("");
      showAdmin(false);
    }
    const detail = data?.detail || "請求失敗";
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return data;
}

function todayIso() {
  const d = new Date();
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

function colorForIndex(index, total) {
  const n = Math.max(total, 1);
  const hue = Math.round((index * 360) / n) % 360;
  return `hsl(${hue}, 58%, 40%)`;
}

function altColorForIndex(index, total) {
  const n = Math.max(total, 1);
  const hue = Math.round((index * 360) / n + 28) % 360;
  return `hsl(${hue}, 52%, 38%)`;
}

function pct(confidence) {
  return `${Math.round(Number(confidence) * 100)}%`;
}

// --- 分頁切換 ---
function switchTab(tab) {
  const isMenu = tab === "menu";
  tabMenu.classList.toggle("hidden", !isMenu);
  tabRecords.classList.toggle("hidden", isMenu);
  document.querySelectorAll(".admin-tab").forEach((btn) => {
    const active = btn.dataset.tab === tab;
    btn.classList.toggle("active", active);
    btn.setAttribute("aria-selected", active ? "true" : "false");
  });
  if (!isMenu && recordDateInput.value) {
    loadRecords(recordDateInput.value);
  }
}

document.querySelectorAll(".admin-tab").forEach((btn) => {
  btn.addEventListener("click", () => switchTab(btn.dataset.tab));
});

// --- 登入 ---
loginForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const password = document.getElementById("admin-password").value;
  try {
    const data = await api("/api/admin/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password }),
    });
    setToken(data.token);
    showAdmin(true);
    switchTab("menu");
    await loadDishes();
    showToast("登入成功");
  } catch (err) {
    showToast(err.message);
  }
});

logoutBtn.addEventListener("click", () => {
  setToken("");
  showAdmin(false);
  showToast("已登出");
});

async function loadDishes() {
  const dishes = await api("/api/dishes");
  dishTbody.innerHTML = dishes
    .map(
      (d) => `
    <tr>
      <td>${d.dish_code ?? "-"}</td>
      <td>${escapeHtml(d.meal_type)}</td>
      <td>${escapeHtml(d.name)}</td>
      <td>${escapeHtml(d.composition)}</td>
      <td>${escapeHtml(d.calories_100g)}</td>
      <td>
        <div class="row-actions">
          <button class="btn ghost" data-edit="${d.id}">編輯</button>
          <button class="btn danger" data-del="${d.id}">刪除</button>
        </div>
      </td>
    </tr>`
    )
    .join("");
}

dishTbody.addEventListener("click", async (e) => {
  const editId = e.target.getAttribute("data-edit");
  const delId = e.target.getAttribute("data-del");

  if (editId) {
    const dish = await api(`/api/dishes/${editId}`);
    openDialog(dish);
  }

  if (delId) {
    if (!confirm("確定刪除此菜色？")) return;
    try {
      await api(`/api/dishes/${delId}`, {
        method: "DELETE",
        headers: authHeaders(),
      });
      await loadDishes();
      showToast("已刪除");
    } catch (err) {
      showToast(err.message);
    }
  }
});

function field(id) {
  return document.getElementById(id);
}

function openDialog(dish) {
  dialogTitle.textContent = "編輯菜色";
  field("dish-id").value = dish.id;
  field("f-meal_type").value = dish.meal_type || "";
  field("f-dish_code").value = dish.dish_code ?? "";
  field("f-name").value = dish.name || "";
  field("f-composition").value = dish.composition || "";
  field("f-tfda_name").value = dish.tfda_name || "";
  field("f-fluffiness").value = dish.fluffiness || "";
  field("f-calories_100g").value = dish.calories_100g || "";
  field("f-protein_100g").value = dish.protein_100g || "";
  field("f-fat_100g").value = dish.fat_100g || "";
  field("f-carb_100g").value = dish.carb_100g || "";
  field("f-sodium_mg").value = dish.sodium_mg || "";
  field("f-potassium_mg").value = dish.potassium_mg || "";
  field("f-cooking_method").value = dish.cooking_method || "";
  field("f-oil_flavor_note").value = dish.oil_flavor_note || "";
  field("f-notes").value = dish.notes || "";
  dialog.showModal();
}

function collectForm() {
  const codeVal = field("f-dish_code").value;
  return {
    meal_type: field("f-meal_type").value || null,
    dish_code: codeVal === "" ? null : Number(codeVal),
    name: field("f-name").value.trim(),
    composition: field("f-composition").value || null,
    tfda_name: field("f-tfda_name").value || null,
    fluffiness: field("f-fluffiness").value || null,
    calories_100g: field("f-calories_100g").value || null,
    protein_100g: field("f-protein_100g").value || null,
    fat_100g: field("f-fat_100g").value || null,
    carb_100g: field("f-carb_100g").value || null,
    sodium_mg: field("f-sodium_mg").value || null,
    potassium_mg: field("f-potassium_mg").value || null,
    cooking_method: field("f-cooking_method").value || null,
    oil_flavor_note: field("f-oil_flavor_note").value || null,
    notes: field("f-notes").value || null,
  };
}

cancelDialog.addEventListener("click", () => dialog.close());

dishForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const id = field("dish-id").value;
  const body = collectForm();
  if (!id || !body.name) {
    showToast("菜名必填");
    return;
  }
  try {
    await api(`/api/dishes/${id}`, {
      method: "PUT",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify(body),
    });
    showToast("已更新");
    dialog.close();
    await loadDishes();
  } catch (err) {
    showToast(err.message);
  }
});

excelInput.addEventListener("change", async () => {
  const file = excelInput.files?.[0];
  if (!file) return;
  const formData = new FormData();
  formData.append("file", file);
  try {
    const data = await api("/api/admin/import-excel", {
      method: "POST",
      headers: authHeaders(),
      body: formData,
    });
    await loadDishes();
    const skipped = data.skipped ?? 0;
    showToast(
      skipped > 0
        ? `新增 ${data.imported} 筆，略過重複 ${skipped} 筆`
        : `新增 ${data.imported} 筆`
    );
  } catch (err) {
    showToast(err.message);
  } finally {
    excelInput.value = "";
  }
});

// --- 記錄管理 ---
function renderHalf(item, color) {
  const code = item.dish_id ? `編號 ${escapeHtml(item.dish_id)}` : "—";
  return `
    <div class="result-half" style="border-left-color:${color}">
      <div>
        <div class="result-name">${escapeHtml(item.dish_name)}</div>
        <div class="result-meta">${code}</div>
      </div>
      <div class="confidence" style="color:${color}">${pct(item.confidence)}</div>
      <div class="confidence-bar"><span style="width:${pct(item.confidence)};background:${color}"></span></div>
    </div>
  `;
}

function renderRecordDetail(detail) {
  const dishes = detail.dishes || [];
  const bust = Date.now();
  let imageHtml = "";

  if (detail.has_dual && detail.boxed1_url && detail.boxed2_url) {
    imageHtml = `
      <div class="result-boxed-pair">
        <figure class="result-boxed-fig">
          <img class="result-boxed-img" src="${escapeHtml(detail.boxed1_url)}?t=${bust}" alt="result1 標註圖" />
          <figcaption>result1</figcaption>
        </figure>
        <figure class="result-boxed-fig">
          <img class="result-boxed-img" src="${escapeHtml(detail.boxed2_url)}?t=${bust}" alt="result2 標註圖" />
          <figcaption>result2</figcaption>
        </figure>
      </div>`;
  } else {
    imageHtml = `
      <div class="result-image-wrap">
        <img class="result-boxed-img record-boxed-single" src="${escapeHtml(detail.boxed_url)}?t=${bust}" alt="辨識標註圖" />
      </div>`;
  }

  const cards = dishes
    .map((item, index) => {
      const color = colorForIndex(index, dishes.length);
      const alt = item.alternative;
      if (alt && alt.dish_name) {
        const colorAlt = altColorForIndex(index, dishes.length);
        return `
          <article class="result-card result-card--split" style="animation-delay:${index * 60}ms">
            ${renderHalf(item, color)}
            ${renderHalf(alt, colorAlt)}
          </article>`;
      }
      const code = item.dish_id ? `編號 ${escapeHtml(item.dish_id)}` : "—";
      return `
        <article class="result-card" style="border-left:4px solid ${color};animation-delay:${index * 60}ms">
          <div>
            <div class="result-name">${escapeHtml(item.dish_name)}</div>
            <div class="result-meta">${code}</div>
          </div>
          <div class="confidence" style="color:${color}">${pct(item.confidence)}</div>
          <div class="confidence-bar"><span style="width:${pct(item.confidence)};background:${color}"></span></div>
        </article>`;
    })
    .join("");

  const empty = dishes.length
    ? ""
    : `<p class="empty">此紀錄沒有菜色結果。</p>`;

  const meal = detail.meal_type
    ? `<p class="model-tag">餐別：${escapeHtml(detail.meal_type)}</p>`
    : "";

  return `
    <div class="record-detail results-panel">
      <div class="results-head">
        <h2>辨識結果</h2>
        ${meal}
      </div>
      ${imageHtml}
      <div class="results-list">${cards}</div>
      ${empty}
    </div>`;
}

async function loadRecords(date) {
  if (!date) {
    recordsList.innerHTML = "";
    recordsEmpty.textContent = "請選擇日期以載入辨識紀錄。";
    recordsEmpty.classList.remove("hidden");
    return;
  }

  recordsEmpty.textContent = "載入中…";
  recordsEmpty.classList.remove("hidden");
  recordsList.innerHTML = "";
  expandedRecordId = null;

  try {
    const data = await api(
      `/api/admin/records?date=${encodeURIComponent(date)}`,
      { headers: authHeaders() }
    );
    const records = data.records || [];
    if (!records.length) {
      recordsEmpty.textContent = "此日期沒有辨識紀錄。";
      recordsEmpty.classList.remove("hidden");
      return;
    }

    recordsEmpty.classList.add("hidden");
    recordsList.innerHTML = records
      .map((r) => {
        const names = (r.dish_names || []).join("、") || "（無菜色）";
        const dual = r.has_dual ? '<span class="record-badge">雙結果</span>' : "";
        return `
          <article class="record-item" data-id="${escapeHtml(r.id)}">
            <button type="button" class="record-summary" data-toggle="${escapeHtml(r.id)}">
              <span class="record-time">${escapeHtml(r.time)}</span>
              <span class="record-meta-line">
                ${escapeHtml(r.meal_type || "—")} · ${r.dish_count} 道 · ${escapeHtml(names)}
                ${dual}
              </span>
              <span class="record-chevron" aria-hidden="true">▾</span>
            </button>
            <div class="record-body hidden" data-body="${escapeHtml(r.id)}"></div>
          </article>`;
      })
      .join("");
  } catch (err) {
    recordsEmpty.textContent = err.message || "載入失敗";
    recordsEmpty.classList.remove("hidden");
  }
}

recordsList.addEventListener("click", async (e) => {
  const btn = e.target.closest("[data-toggle]");
  if (!btn) return;
  const id = btn.getAttribute("data-toggle");
  const item = btn.closest(".record-item");
  const body = item?.querySelector(`[data-body="${id}"]`);
  if (!body) return;

  if (expandedRecordId === id) {
    body.classList.add("hidden");
    body.innerHTML = "";
    item.classList.remove("is-open");
    expandedRecordId = null;
    return;
  }

  // 收合其他已展開項目
  recordsList.querySelectorAll(".record-item.is-open").forEach((el) => {
    el.classList.remove("is-open");
    const b = el.querySelector(".record-body");
    if (b) {
      b.classList.add("hidden");
      b.innerHTML = "";
    }
  });

  body.classList.remove("hidden");
  body.innerHTML = `<p class="muted">載入詳情中…</p>`;
  item.classList.add("is-open");
  expandedRecordId = id;

  try {
    const detail = await api(`/api/admin/records/${encodeURIComponent(id)}`, {
      headers: authHeaders(),
    });
    if (expandedRecordId !== id) return;
    body.innerHTML = renderRecordDetail(detail);
  } catch (err) {
    if (expandedRecordId !== id) return;
    body.innerHTML = `<p class="empty">${escapeHtml(err.message)}</p>`;
  }
});

recordDateInput.addEventListener("change", () => {
  loadRecords(recordDateInput.value);
});

async function boot() {
  if (!recordDateInput.value) {
    recordDateInput.value = todayIso();
  }
  if (!getToken()) {
    showAdmin(false);
    return;
  }
  showAdmin(true);
  switchTab("menu");
  try {
    await loadDishes();
  } catch {
    showAdmin(false);
  }
}

boot();
