/* Web-интерфейс личного кабинета (Задание №6).
 * Все данные берутся из существующего REST API — бизнес-логики здесь нет:
 * страница только отправляет запросы и отображает ответы (включая ошибки).
 */

const TOKEN_KEY = "ml_service_token";
const token = () => localStorage.getItem(TOKEN_KEY);
const setToken = (t) => localStorage.setItem(TOKEN_KEY, t);
const clearToken = () => localStorage.removeItem(TOKEN_KEY);

/** Запрос к REST API с bearer-токеном. Ошибки backend пробрасываются как есть. */
async function api(path, { method = "GET", body = null, auth = true } = {}) {
  const headers = { Accept: "application/json" };
  if (body) headers["Content-Type"] = "application/json";
  if (auth && token()) headers["Authorization"] = `Bearer ${token()}`;

  const resp = await fetch(path, {
    method,
    headers,
    body: body ? JSON.stringify(body) : null,
  });

  let data = null;
  try { data = await resp.json(); } catch (_) { /* пустой ответ */ }

  if (!resp.ok) {
    const err = new Error(errorText(resp.status, data));
    err.status = resp.status;
    err.detail = data && data.detail;
    throw err;
  }
  return data;
}

/** Понятное сообщение из ответа backend (единый формат {"detail": ...}). */
function errorText(status, data) {
  const d = data && data.detail;
  if (typeof d === "string") return d;
  if (Array.isArray(d)) {
    // 422 от Pydantic: список ошибок по полям
    return d.map((e) => `${(e.loc || []).slice(1).join(".")}: ${e.msg}`).join("; ");
  }
  if (d && typeof d === "object" && d.message) return d.message;
  return `Ошибка ${status}`;
}

function show(id, text, kind = "info") {
  const box = document.getElementById(id);
  if (box) box.innerHTML = `<div class="msg ${kind}"></div>`, box.firstChild.textContent = text;
}

const fmtDate = (iso) => new Date(iso).toLocaleString("ru-RU");
const badge = (v) => `<span class="badge ${v}">${v}</span>`;
const esc = (s) =>
  String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

/* ---------------------------------------------------------------- навигация */

async function initNav() {
  const authed = Boolean(token());
  let me = null;
  if (authed) {
    try {
      me = await api("/users/me");
    } catch (_) {
      clearToken(); // токен протух
    }
  }
  const logged = Boolean(me);
  document.querySelectorAll('[data-auth="yes"]').forEach((el) => (el.hidden = !logged));
  document.querySelectorAll('[data-auth="no"]').forEach((el) => (el.hidden = logged));
  document.querySelectorAll('[data-admin="yes"]').forEach(
    (el) => (el.hidden = !(logged && me.is_admin))
  );
  const nu = document.getElementById("nav-user");
  if (nu && logged) { nu.textContent = me.email; nu.hidden = false; }

  const out = document.getElementById("logout");
  if (out) out.onclick = () => { clearToken(); location.href = "/"; };
  return me;
}

/** Страницы кабинета доступны только после авторизации. */
function requireAuth(me) {
  if (!me) { location.href = "/login"; return false; }
  return true;
}

/* ------------------------------------------------------------------ главная */

async function initIndex() {
  const box = document.getElementById("models-list");
  try {
    const models = await api("/models", { auth: false });
    box.className = "grid-3";
    box.innerHTML = models
      .map(
        (m) => `<article class="card">
          <h3>${esc(m.name)}</h3>
          <p class="hint" style="margin:8px 0 14px">${esc(m.description || "")}</p>
          <p class="spec">${m.cost_per_request} кредитов за запрос</p>
          <p class="spec">признаки: ${m.required_features.map(esc).join(", ")}</p>
        </article>`
      )
      .join("");
  } catch (e) {
    box.textContent = `Каталог моделей не загрузился: ${e.message}`;
  }
}

/* ------------------------------------------------------- вход и регистрация */

function initLogin() {
  document.getElementById("login-form").onsubmit = async (ev) => {
    ev.preventDefault();
    const f = new FormData(ev.target);
    try {
      const data = await api("/auth/login", {
        method: "POST",
        auth: false,
        body: { email: f.get("email"), password: f.get("password") },
      });
      setToken(data.access_token);
      location.href = "/dashboard";
    } catch (e) {
      show("msg", e.message, "err");
    }
  };
}

function initRegister() {
  document.getElementById("register-form").onsubmit = async (ev) => {
    ev.preventDefault();
    const f = new FormData(ev.target);
    const email = f.get("email"), password = f.get("password");
    try {
      await api("/auth/register", { method: "POST", auth: false, body: { email, password } });
      const data = await api("/auth/login", {
        method: "POST", auth: false, body: { email, password },
      });
      setToken(data.access_token);
      location.href = "/dashboard";
    } catch (e) {
      show("msg", e.message, "err");
    }
  };
}

/* ----------------------------------------------------------- личный кабинет */

async function refreshBalance() {
  const { balance } = await api("/balance");
  const el = document.getElementById("balance");
  const prev = el.textContent;
  el.textContent = balance;
  // сообщение о пополнении устаревает, как только баланс изменился по другой причине
  if (prev !== "—" && String(balance) !== prev) {
    const box = document.getElementById("topup-msg");
    if (box) box.innerHTML = "";
  }
  return balance;
}

async function initDashboard(me) {
  document.getElementById("acc-email").textContent = me.email;
  document.getElementById("acc-role").textContent = me.is_admin ? "администратор" : "пользователь";
  document.getElementById("acc-id").textContent = me.id;
  await refreshBalance();

  // --- пополнение баланса
  const topUp = async (amount) => {
    try {
      const { balance } = await api("/balance/top-up", { method: "POST", body: { amount } });
      document.getElementById("balance").textContent = balance;
      show("topup-msg", `Баланс пополнен на ${amount} — теперь ${balance} кредитов.`, "ok");
    } catch (e) {
      show("topup-msg", e.message, "err");
    }
  };
  document.getElementById("topup-form").onsubmit = (ev) => {
    ev.preventDefault();
    topUp(Number(new FormData(ev.target).get("amount")));
  };
  document.querySelectorAll(".topup-quick").forEach(
    (b) => (b.onclick = () => topUp(Number(b.dataset.amount)))
  );

  // --- каталог моделей: подсказка и пример данных
  const models = await api("/models", { auth: false });
  const select = document.getElementById("model-select");
  select.innerHTML = models
    .map((m) => `<option value="${esc(m.name)}">${esc(m.name)} — ${m.cost_per_request} кр.</option>`)
    .join("");

  const applyModel = () => {
    const m = models.find((x) => x.name === select.value);
    if (!m) return;
    document.getElementById("model-hint").textContent =
      `Требуемые признаки: ${m.required_features.join(", ")} · стоимость ${m.cost_per_request} кредитов`;
    const example = {};
    m.required_features.forEach((f, i) => (example[f] = i + 1));
    document.getElementById("rows").value = JSON.stringify([example], null, 2);
  };
  select.onchange = applyModel;
  applyModel();

  // --- загрузка данных файлом (CSV или JSON) вместо ручного ввода
  document.getElementById("file").onchange = async (ev) => {
    const file = ev.target.files[0];
    if (!file) return;
    const text = await file.text();
    try {
      document.getElementById("rows").value = JSON.stringify(parseRows(text, file.name), null, 2);
      show("predict-msg", `Из файла «${file.name}» прочитано строк: ${JSON.parse(document.getElementById("rows").value).length}. Проверьте данные и отправьте запрос.`, "info");
    } catch (e) {
      show("predict-msg", `Файл не разобран: ${e.message}. Нужен CSV с заголовком или JSON-массив строк.`, "err");
    }
  };

  document.getElementById("predict-form").onsubmit = submitPredict;
}

/** CSV или JSON -> массив объектов признаков. */
function parseRows(text, filename = "") {
  const trimmed = text.trim();
  if (trimmed.startsWith("[") || filename.toLowerCase().endsWith(".json")) {
    const data = JSON.parse(trimmed);
    if (!Array.isArray(data)) throw new Error("ожидался JSON-массив строк");
    return data;
  }
  const lines = trimmed.split(/\r?\n/).filter((l) => l.trim());
  if (lines.length < 2) throw new Error("в CSV нужны заголовок и хотя бы одна строка");
  const head = lines[0].split(",").map((h) => h.trim());
  return lines.slice(1).map((line) => {
    const cells = line.split(",");
    const row = {};
    head.forEach((h, i) => {
      const raw = (cells[i] ?? "").trim();
      const num = Number(raw);
      row[h] = raw !== "" && !Number.isNaN(num) ? num : raw; // нечисловое оставляем как есть
    });
    return row;
  });
}

async function submitPredict(ev) {
  ev.preventDefault();
  const resultBox = document.getElementById("predict-result");
  resultBox.innerHTML = "";

  let rows;
  try {
    rows = parseRows(document.getElementById("rows").value);
  } catch (e) {
    show("predict-msg", `Данные не разобраны: ${e.message}. Ожидается JSON-массив объектов.`, "err");
    return;
  }

  const model = document.getElementById("model-select").value;
  const submit = document.getElementById("predict-submit");
  submit.disabled = true;
  let task;
  try {
    setPipeline("request", { request: `строк: ${rows.length}`, queue: "—", worker: "—", result: "—" });
    show("predict-msg", "Отправляем запрос…", "info");
    task = await api("/predict", { method: "POST", body: { model, rows } });
  } catch (e) {
    // ошибки backend показываются пользователю как есть
    setPipeline("request", { request: "запрос не принят" });
    if (e.status === 402) {
      show("predict-msg",
        `Не хватает кредитов на этот запрос. ${e.message}. Пополните баланс выше и отправьте снова.`,
        "warn");
    } else {
      show("predict-msg", e.message, "err");
    }
    await refreshBalance();
    submit.disabled = false;
    return;
  }

  // задача обрабатывается воркером асинхронно — опрашиваем результат
  setPipeline("queue", { request: `строк: ${rows.length}`, queue: `задача ${task.task_id.slice(0, 8)}` });
  show("predict-msg", "Задача в очереди, ждём свободный воркер…", "info");
  await refreshBalance();

  for (let i = 0; i < 40 && (task.status === "new" || task.status === "running"); i++) {
    await new Promise((r) => setTimeout(r, 600));
    task = await api(`/predict/${task.task_id}`);
    if (task.status === "running") setPipeline("worker", { worker: "выполняется предикт" });
  }
  await refreshBalance();
  submit.disabled = false;
  renderTask(task, resultBox);
}

/** Строки, дошедшие до модели: входные минус отклонённые (порядок сохранён). */
function processedRows(task) {
  const pool = (task.invalid_rows || []).map((r) => JSON.stringify(r.row));
  const kept = [];
  (task.input_data || []).forEach((row) => {
    const i = pool.indexOf(JSON.stringify(row));
    if (i >= 0) pool.splice(i, 1);
    else kept.push(row);
  });
  return kept;
}

function renderTask(task, box) {
  const rejected = task.invalid_rows || [];
  const processed = processedRows(task);
  const sent = Array.isArray(task.result) ? task.result.length : 0;

  if (task.status === "done") {
    setPipeline("result", { worker: "готово", result: `списано ${task.charged}` });
    show("predict-msg",
      `Запрос выполнен: обработано строк — ${processed.length || sent}` +
      (rejected.length ? `, отклонено — ${rejected.length}` : "") +
      `, списано ${task.charged} кредитов.`, "ok");
  } else if (task.status === "validation_failed") {
    setPipeline("result", { worker: "данные отклонены", result: "кредиты возвращены" });
    show("predict-msg",
      "Ни одна строка не прошла валидацию. Запрос не выполнен, кредиты вернулись на баланс — " +
      "исправьте данные по причинам ниже и отправьте снова.", "warn");
  } else if (task.status === "failed") {
    setPipeline("result", { worker: "ошибка", result: "кредиты возвращены" });
    show("predict-msg", "Задача завершилась с ошибкой, кредиты вернулись на баланс.", "err");
  } else {
    setPipeline("worker", { worker: "всё ещё обрабатывается" });
    show("predict-msg",
      "Задача ещё обрабатывается. Загляните в историю через минуту — результат появится там.", "info");
  }

  let html = `<div class="result-block">
      <h4>Задача ${esc(task.task_id)} · ${badge(task.status)}</h4>`;
  if (Array.isArray(task.result) && processed.length) {
    html += `<h4>Обработано строк: ${processed.length}</h4>
      <div class="table-wrap"><table><thead><tr>
        <th>№</th><th>Отправленные данные</th><th>Предсказание</th>
      </tr></thead><tbody>` +
      processed
        .map(
          (row, i) => `<tr>
            <td class="num">${i + 1}</td>
            <td class="mono">${esc(JSON.stringify(row))}</td>
            <td class="num">${esc(JSON.stringify(task.result[i]))}</td>
          </tr>`
        )
        .join("") +
      `</tbody></table></div>`;
  } else if (task.result) {
    html += `<h4>Предсказание</h4><pre>${esc(JSON.stringify(task.result))}</pre>`;
  }
  if (rejected.length) {
    html += `<h4>Отклонено строк: ${rejected.length}</h4>
      <div class="table-wrap"><table><thead><tr><th>Строка</th><th>Причина</th></tr></thead><tbody>` +
      rejected
        .map((r) => `<tr><td class="mono">${esc(JSON.stringify(r.row))}</td><td>${esc(r.reason)}</td></tr>`)
        .join("") +
      `</tbody></table></div>`;
  }
  box.innerHTML = html + "</div>";
}

/** Конвейер задачи: подсвечивает текущий шаг обработки. */
function setPipeline(stage, notes = {}) {
  const box = document.getElementById("task-pipeline");
  if (!box) return;
  const order = ["request", "queue", "worker", "result"];
  const idx = order.indexOf(stage);
  box.classList.toggle("live", stage === "queue" || stage === "worker");
  order.forEach((name, i) => {
    const el = box.querySelector(`[data-stage="${name}"]`);
    el.classList.toggle("on", i === idx);
    el.classList.toggle("done", i < idx);
    if (notes[name] !== undefined) el.querySelector(".note").textContent = notes[name];
  });
}

/* ------------------------------------------------------------------ история */

async function initHistory() {
  const [tasks, txs] = await Promise.all([
    api("/history/predictions"),
    api("/history/transactions"),
  ]);

  const tasksBody = document.querySelector("#tasks tbody");
  tasksBody.innerHTML = tasks.length
    ? tasks
        .map((t) => {
          const rejected = (t.invalid_rows || []).length;
          return `<tr>
            <td>${fmtDate(t.created_at)}</td>
            <td>${esc(t.model)}</td>
            <td>${badge(t.status)}${rejected ? `<div class="hint">отклонено строк: ${rejected}</div>` : ""}</td>
            <td class="num">${t.charged}</td>
            <td class="mono">${esc(JSON.stringify(t.input_data))}</td>
            <td class="mono">${t.result === null ? "—" : esc(JSON.stringify(t.result))}</td>
          </tr>`;
        })
        .join("")
    : `<tr><td colspan="6" class="muted">Запросов пока нет — отправьте первый из кабинета</td></tr>`;

  const txBody = document.querySelector("#txs tbody");
  txBody.innerHTML = txs.length
    ? txs
        .map(
          (t) => `<tr>
            <td>${fmtDate(t.created_at)}</td>
            <td>${badge(t.type)}${t.type === "deposit" && t.task_id ? ' <span class="hint">возврат</span>' : ""}</td>
            <td class="num">${t.amount}</td>
            <td class="mono small">${t.task_id ? esc(t.task_id) : "—"}</td>
          </tr>`
        )
        .join("")
    : `<tr><td colspan="4" class="muted">Операций пока нет — начните с пополнения баланса</td></tr>`;
}

/* ------------------------------------------------------------------- админка */

async function initAdmin(me) {
  if (!me.is_admin) {
    show("admin-guard", "Раздел доступен только администраторам. Войдите под учётной записью администратора.", "err");
    document.querySelectorAll(".dash .card").forEach((c) => (c.hidden = true));
    return;
  }
  bindAdminControls();
  allUsers = await api("/admin/users");
  allTxs = await api("/admin/transactions");
  renderUsers();
  renderTransactions();
}

/* Поиск и постраничный вывод в админке.
 * Фильтр по email общий для обеих таблиц: пользователи ищутся по своему email,
 * транзакции — по email владельца операции. Обработка клиентская, списки
 * приходят целиком; на больших объёмах это следует перенести в API
 * (limit/offset и поиск в запросе). */
const PAGE_SIZE = 10;
let allUsers = [];
let allTxs = [];
let usersPage = 1;
let txsPage = 1;

function bindAdminControls() {
  document.getElementById("admin-search").oninput = () => {
    usersPage = 1;
    txsPage = 1;
    renderUsers();
    renderTransactions();
  };
  document.getElementById("users-prev").onclick = () => {
    usersPage -= 1;
    renderUsers();
  };
  document.getElementById("users-next").onclick = () => {
    usersPage += 1;
    renderUsers();
  };
  document.getElementById("txs-prev").onclick = () => {
    txsPage -= 1;
    renderTransactions();
  };
  document.getElementById("txs-next").onclick = () => {
    txsPage += 1;
    renderTransactions();
  };
}

function adminQuery() {
  return document.getElementById("admin-search").value.trim().toLowerCase();
}

/** Отрезает страницу и поправляет номер, если он вышел за границы. */
function paginate(items, page) {
  const pages = Math.max(1, Math.ceil(items.length / PAGE_SIZE));
  const current = Math.min(Math.max(1, page), pages);
  return {
    pages,
    current,
    slice: items.slice((current - 1) * PAGE_SIZE, current * PAGE_SIZE),
  };
}

/** Переключатель страниц показываем, только когда список не помещается целиком. */
function paintPager(name, current, pages, total) {
  document.getElementById(`${name}-pager`).hidden = total <= PAGE_SIZE;
  document.getElementById(`${name}-page`).textContent = `${current} / ${pages}`;
  document.getElementById(`${name}-prev`).disabled = current === 1;
  document.getElementById(`${name}-next`).disabled = current === pages;
}

function paintCount(id, found, total, query) {
  document.getElementById(id).textContent = query
    ? `найдено ${found} из ${total}`
    : `всего ${total}`;
}

async function loadUsers() {
  allUsers = await api("/admin/users");
  renderUsers();
}

function renderUsers() {
  const query = adminQuery();
  const found = query
    ? allUsers.filter((u) => u.email.toLowerCase().includes(query))
    : allUsers;
  const { pages, current, slice } = paginate(found, usersPage);
  usersPage = current;

  const body = document.querySelector("#users tbody");
  body.innerHTML = slice.length
    ? slice
        .map(
          (u) => `<tr>
        <td>${esc(u.email)}</td>
        <td>${u.is_admin ? "администратор" : "пользователь"}</td>
        <td class="num">${u.balance}</td>
        <td>${fmtDate(u.created_at)}</td>
        <td><button class="btn-sm topup-user" data-id="${esc(u.id)}">Начислить 50</button></td>
      </tr>`
        )
        .join("")
    : `<tr><td colspan="5" class="muted">По запросу никого не нашлось</td></tr>`;

  paintCount("users-count", found.length, allUsers.length, query);
  paintPager("users", current, pages, found.length);

  body.querySelectorAll(".topup-user").forEach((btn) => {
    btn.onclick = async () => {
      try {
        const { balance } = await api(`/admin/users/${btn.dataset.id}/top-up`, {
          method: "POST",
          body: { amount: 50 },
        });
        show("admin-msg", `Баланс пользователя пополнен — теперь ${balance} кредитов`, "ok");
        await loadUsers();
        allTxs = await api("/admin/transactions");
        renderTransactions();
      } catch (e) {
        show("admin-msg", e.message, "err");
      }
    };
  });
}

function renderTransactions() {
  const query = adminQuery();
  const found = query
    ? allTxs.filter((t) => (t.user_email || "").toLowerCase().includes(query))
    : allTxs;
  const { pages, current, slice } = paginate(found, txsPage);
  txsPage = current;

  const empty = allTxs.length
    ? "По запросу транзакций не нашлось"
    : "В системе ещё нет транзакций";
  document.querySelector("#all-txs tbody").innerHTML = slice.length
    ? slice
        .map(
          (t) => `<tr>
            <td>${fmtDate(t.created_at)}</td>
            <td>${badge(t.type)}</td>
            <td class="num">${t.amount}</td>
            <td>${esc(t.user_email)}</td>
            <td class="mono small">${t.task_id ? esc(t.task_id) : "—"}</td>
          </tr>`
        )
        .join("")
    : `<tr><td colspan="5" class="muted">${empty}</td></tr>`;

  paintCount("txs-count", found.length, allTxs.length, query);
  paintPager("txs", current, pages, found.length);
}

/* --------------------------------------------------------------------- старт */

document.addEventListener("DOMContentLoaded", async () => {
  const page = document.body.dataset.page;
  let me = null;
  try {
    me = await initNav();
  } catch (_) { /* API недоступен — страница всё равно отрисуется */ }

  try {
    if (page === "index") await initIndex();
    if (page === "login") initLogin();
    if (page === "register") initRegister();
    if (page === "dashboard" && requireAuth(me)) await initDashboard(me);
    if (page === "history" && requireAuth(me)) await initHistory();
    if (page === "admin" && requireAuth(me)) await initAdmin(me);
  } catch (e) {
    console.error(e);
    const box = document.getElementById("msg") || document.getElementById("predict-msg");
    if (box) show(box.id, e.message, "err");
  }
});
