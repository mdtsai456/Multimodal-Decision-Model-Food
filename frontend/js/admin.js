// 管理頁：登入、匯入 Excel、編輯／刪除菜色

// --- DOM 元素 ---
const TOKEN_KEY = "healthweb_admin_token"; // localStorage 存 token 的 key
const toastEl = document.getElementById("toast");
const loginPanel = document.getElementById("login-panel");   // 登入區塊
const adminPanel = document.getElementById("admin-panel");   // 登入後的管理區塊
const loginForm = document.getElementById("login-form");
const logoutBtn = document.getElementById("logout-btn");
const dishTbody = document.getElementById("dish-tbody");     // 菜單表格內容
const excelInput = document.getElementById("excel-input");   // 匯入 Excel 的檔案選擇
const dialog = document.getElementById("dish-dialog");       // 編輯對話框
const dishForm = document.getElementById("dish-form");
const dialogTitle = document.getElementById("dialog-title");
const cancelDialog = document.getElementById("cancel-dialog");

// 畫面底部短暫提示訊息
function showToast(message) {
  toastEl.textContent = message;
  toastEl.classList.remove("hidden");
  clearTimeout(showToast._t);
  showToast._t = setTimeout(() => toastEl.classList.add("hidden"), 2800);
}

// 讀取已存的管理員 token
function getToken() {
  return localStorage.getItem(TOKEN_KEY) || "";
}

// 寫入或清除 token（登出時傳空值）
function setToken(token) {
  if (token) localStorage.setItem(TOKEN_KEY, token);
  else localStorage.removeItem(TOKEN_KEY);
}

// 組出帶 Authorization 的 headers（管理 API 需要）
function authHeaders(extra = {}) {
  const token = getToken();
  return {
    ...extra,
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  };
}

// 切換登入畫面／管理畫面
function showAdmin(isLoggedIn) {
  loginPanel.classList.toggle("hidden", isLoggedIn);
  adminPanel.classList.toggle("hidden", !isLoggedIn);
}

// 避免菜名等文字被當成 HTML 插入（防 XSS）
function escapeHtml(text) {
  return String(text ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

// 統一打 API：失敗就丟 Error；401 則清 token 並退回登入頁
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
    await loadDishes();
    showToast("登入成功");
  } catch (err) {
    showToast(err.message);
  }
});

// --- 登出 ---
logoutBtn.addEventListener("click", () => {
  setToken("");
  showAdmin(false);
  showToast("已登出");
});

// 從後端拉菜單，畫成表格列
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

// 表格按鈕：編輯 or 刪除（用 data-edit / data-del 分辨）
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

// 依 id 取表單欄位
function field(id) {
  return document.getElementById(id);
}

// 打開編輯對話框，把菜色資料填進表單
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

// 從表單收集要送出的更新資料
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

// 儲存編輯：PUT /api/dishes/{id}
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

// 匯入 Excel：只新增未重複菜色（後端不會覆蓋舊資料）
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
    excelInput.value = ""; // 清掉，方便下次選同一檔也能觸發 change
  }
});

// 頁面載入：有 token 就直接進管理頁並載菜單
async function boot() {
  if (!getToken()) {
    showAdmin(false);
    return;
  }
  showAdmin(true);
  try {
    await loadDishes();
  } catch {
    // token 可能過期，退回登入
    showAdmin(false);
  }
}

boot();
