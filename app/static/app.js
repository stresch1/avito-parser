let ALL_COLUMNS = [];

const linksList = document.getElementById("linksList");
const addLinkBtn = document.getElementById("addLinkBtn");
const linksCount = document.getElementById("linksCount");
const columnsList = document.getElementById("columnsList");
const colsSelectedCount = document.getElementById("colsSelectedCount");
const colsTotalCount = document.getElementById("colsTotalCount");
const proxiesText = document.getElementById("proxiesText");
const proxiesStatus = document.getElementById("proxiesStatus");
const captchaKeyInput = document.getElementById("captchaKeyInput");
const captchaKeyStatus = document.getElementById("captchaKeyStatus");
const startBtn = document.getElementById("startBtn");
const formError = document.getElementById("formError");
const tasksListEl = document.getElementById("tasksList");
const taskStatsEl = document.getElementById("taskStats");

function addLinkRow(value = "") {
  const row = document.createElement("div");
  row.className = "link-row";
  row.innerHTML = `
    <input type="text" placeholder="https://www.avito.ru/.../gruzoviki_i_spetstehnika/..." value="${value}">
    <button type="button" title="Удалить">✕</button>
  `;
  row.querySelector("button").addEventListener("click", () => {
    row.remove();
    updateLinksCount();
  });
  row.querySelector("input").addEventListener("input", updateLinksCount);
  linksList.appendChild(row);
  updateLinksCount();
}

function updateLinksCount() {
  const filled = [...linksList.querySelectorAll("input")].filter(i => i.value.trim()).length;
  linksCount.textContent = filled ? `(${filled})` : "";
}

addLinkBtn.addEventListener("click", () => addLinkRow());

function getLinks() {
  return [...linksList.querySelectorAll("input")].map(i => i.value.trim()).filter(Boolean);
}

async function loadColumns() {
  const res = await fetch("/api/columns");
  const data = await res.json();
  ALL_COLUMNS = data.columns;
  colsTotalCount.textContent = ALL_COLUMNS.length;
  columnsList.innerHTML = "";
  ALL_COLUMNS.forEach(col => {
    const label = document.createElement("label");
    label.innerHTML = `<input type="checkbox" checked value="${col}"> ${col}`;
    label.querySelector("input").addEventListener("change", updateColsCount);
    columnsList.appendChild(label);
  });
  updateColsCount();
}

function updateColsCount() {
  const checked = columnsList.querySelectorAll("input:checked").length;
  colsSelectedCount.textContent = checked;
}

function getSelectedColumns() {
  const boxes = [...columnsList.querySelectorAll("input")];
  const checked = boxes.filter(b => b.checked).map(b => b.value);
  return checked.length === ALL_COLUMNS.length ? null : checked;
}

async function loadProxies() {
  const res = await fetch("/api/proxies");
  const data = await res.json();
  proxiesText.value = data.raw || "";
  proxiesStatus.textContent = data.count ? `Загружено: ${data.count}` : "Прокси не заданы";
}

document.getElementById("saveProxiesBtn").addEventListener("click", async () => {
  const res = await fetch("/api/proxies", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ raw: proxiesText.value }),
  });
  const data = await res.json();
  proxiesStatus.textContent = `Сохранено: ${data.count}`;
});

async function loadCaptchaKey() {
  const res = await fetch("/api/captcha-key");
  const data = await res.json();
  captchaKeyStatus.textContent = data.configured ? `Сохранён: ${data.masked}` : "Ключ не задан";
}

document.getElementById("saveCaptchaKeyBtn").addEventListener("click", async () => {
  if (!captchaKeyInput.value.trim()) return;
  const res = await fetch("/api/captcha-key", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ api_key: captchaKeyInput.value.trim() }),
  });
  await res.json();
  captchaKeyInput.value = "";
  await loadCaptchaKey();
});

const cookieServiceKeyInput = document.getElementById("cookieServiceKeyInput");
const cookieServiceKeyStatus = document.getElementById("cookieServiceKeyStatus");

async function loadCookieServiceKey() {
  const res = await fetch("/api/cookie-service-key");
  const data = await res.json();
  if (!data.configured) {
    cookieServiceKeyStatus.textContent = "Ключ не задан";
  } else {
    const balance = data.balance !== null && data.balance !== undefined ? `, баланс: ${data.balance}₽` : "";
    cookieServiceKeyStatus.textContent = `Сохранён: ${data.masked}${balance}`;
  }
}

document.getElementById("saveCookieServiceKeyBtn").addEventListener("click", async () => {
  if (!cookieServiceKeyInput.value.trim()) return;
  const res = await fetch("/api/cookie-service-key", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ api_key: cookieServiceKeyInput.value.trim() }),
  });
  await res.json();
  cookieServiceKeyInput.value = "";
  await loadCookieServiceKey();
});

startBtn.addEventListener("click", async () => {
  formError.textContent = "";
  const links = getLinks();
  if (!links.length) {
    formError.textContent = "Добавьте хотя бы одну ссылку на выдачу Avito.";
    return;
  }
  startBtn.disabled = true;
  startBtn.textContent = "Запускаю...";
  try {
    const res = await fetch("/api/tasks", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        source: "avito",
        mode: "full",
        links,
        limit_per_link: Number(document.getElementById("limitPerLink").value) || 0,
        only_region: document.getElementById("onlyRegion").checked,
        merge_file: document.getElementById("mergeFile").checked,
        columns: getSelectedColumns(),
      }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Ошибка запуска");
    await refreshTasks();
  } catch (e) {
    formError.textContent = e.message;
  } finally {
    startBtn.disabled = false;
    startBtn.textContent = "▶ Запустить парсинг";
  }
});

const STATUS_LABELS = {
  queued: "В очереди",
  running: "Идёт парсинг",
  done: "Готово",
  error: "Ошибка",
};
const STATUS_ICONS = { queued: "⏳", running: "🔄", done: "✓", error: "✕" };

function fmtDate(ts) {
  if (!ts) return "—";
  const d = new Date(ts * 1000);
  return d.toLocaleString("ru-RU");
}

function renderTaskStats(tasks) {
  const completedItems = tasks
    .filter(task => task.status === "done")
    .reduce((sum, task) => sum + (Number(task.items_count) || 0), 0);
  const attentionCount = tasks.filter(task => task.status === "error" && task.pending_cards).length;
  const stats = [
    { label: "В РАБОТЕ", value: tasks.filter(task => task.status === "running").length, detail: "активных задач", state: "running" },
    { label: "СОБРАНО", value: completedItems.toLocaleString("ru-RU"), detail: "объявлений в готовых файлах", state: "done" },
    { label: "ТРЕБУЮТ ВНИМАНИЯ", value: attentionCount, detail: "задач можно продолжить", state: "attention" },
  ];

  taskStatsEl.replaceChildren();
  stats.forEach(stat => {
    const tile = document.createElement("div");
    tile.className = `stat-tile ${stat.state}`;
    const label = document.createElement("span");
    label.className = "stat-label";
    label.textContent = stat.label;
    const value = document.createElement("strong");
    value.className = "stat-value";
    value.textContent = stat.value;
    const detail = document.createElement("span");
    detail.className = "stat-detail";
    detail.textContent = stat.detail;
    tile.append(label, value, detail);
    taskStatsEl.appendChild(tile);
  });
}

function renderTasks(tasks) {
  renderTaskStats(tasks);
  tasksListEl.innerHTML = "";
  if (!tasks.length) {
    tasksListEl.innerHTML = '<p class="muted">Пока нет задач</p>';
    return;
  }
  tasks.forEach(t => {
    const div = document.createElement("div");
    div.className = "task-item";
    const isBlocked = t.error_code === "AVITO_IP_BLOCKED";
    const isBrowserClosed = t.error_code === "BROWSER_CLOSED";
    const hasPending = Boolean(t.pending_cards);
    const canResume = t.status !== "running" && hasPending;
    const isCaptchaWait = t.status === "running" && /капч|блок/i.test(t.progress_text || "");
    const linksHtml = t.links.map(l => `<a class="task-link" href="${l}" target="_blank">${l}</a>`).join("");
    // В desktop-версии (окно pywebview) обычная ссылка на скачивание ничего не делает —
    // там нет браузерного менеджера загрузок. В этом случае используем нативный диалог
    // "Сохранить как" через window.pywebview.api (см. desktop.py). В обычном браузере
    // (веб-версия) — как и раньше, простая ссылка.
    const downloadsHtml = t.result_files.length
      ? t.result_files.map((f, idx) => window.pywebview
          ? `<a href="#" class="download-link" data-id="${t.id}" data-idx="${idx}">⬇ Скачать${t.result_files.length > 1 ? " " + (idx + 1) : ""}</a>`
          : `<a href="/api/tasks/${t.id}/download?index=${idx}">⬇ Скачать${t.result_files.length > 1 ? " " + (idx + 1) : ""}</a>`
        ).join("")
      : "";
    div.innerHTML = `
      <div class="task-status ${t.status} ${(isBlocked || isBrowserClosed) ? "blocked" : ""}">
        ${STATUS_ICONS[t.status] || ""} ${isBlocked ? "IP заблокирован" : (isBrowserClosed ? "Браузер закрыт" : (STATUS_LABELS[t.status] || t.status))}
      </div>
      ${linksHtml}
      <div class="task-meta">
        Старт: <b>${fmtDate(t.started_at)}</b> &nbsp; Финиш: <b>${fmtDate(t.finished_at)}</b><br>
        Объявлений: <b>${t.items_count}</b>
        ${t.error ? `<br><span style="color:#dc2626">${t.error}</span>` : ""}
      </div>
        ${t.status === "running" && t.progress_text ? `<div class="progress-text">${t.progress_text}</div>` : ""}
      <div class="task-actions">
        ${downloadsHtml}
        <button class="repeat-btn" data-id="${t.id}">↻ Повторить</button>
        ${canResume ? `<button class="repeat-unfinished-btn" data-id="${t.id}">Добрать недособранное</button>` : ""}
        ${isCaptchaWait ? `<button class="continue-captcha-btn" data-id="${t.id}">Продолжить после капчи</button>` : ""}
        ${t.status === "running" ? `<button class="danger cancel-btn" data-id="${t.id}">✕ Отменить</button>` : ""}
      </div>
    `;
    tasksListEl.appendChild(div);
  });

  tasksListEl.querySelectorAll(".download-link").forEach(a => {
    a.addEventListener("click", async (e) => {
      e.preventDefault();
      if (!window.pywebview || !window.pywebview.api) return;
      const res = await window.pywebview.api.save_file(a.dataset.id, Number(a.dataset.idx));
      if (!res.ok && res.error !== "Отменено") {
        alert("Не удалось сохранить файл: " + res.error);
      }
    });
  });
  tasksListEl.querySelectorAll(".repeat-btn").forEach(btn => {
    btn.addEventListener("click", async () => {
      await fetch(`/api/tasks/${btn.dataset.id}/repeat`, { method: "POST" });
      refreshTasks();
    });
  });
  tasksListEl.querySelectorAll(".repeat-unfinished-btn").forEach(btn => {
    btn.addEventListener("click", async () => {
      await fetch(`/api/tasks/${btn.dataset.id}/repeat-unfinished`, { method: "POST" });
      refreshTasks();
    });
  });
  tasksListEl.querySelectorAll(".continue-captcha-btn").forEach(btn => {
    btn.addEventListener("click", async () => {
      await fetch(`/api/tasks/${btn.dataset.id}/continue-captcha`, { method: "POST" });
      refreshTasks();
    });
  });
  tasksListEl.querySelectorAll(".cancel-btn").forEach(btn => {
    btn.addEventListener("click", async () => {
      await fetch(`/api/tasks/${btn.dataset.id}/cancel`, { method: "POST" });
      refreshTasks();
    });
  });
}

async function refreshTasks() {
  const res = await fetch("/api/tasks");
  const data = await res.json();
  renderTasks(data.tasks);
}

async function init() {
  addLinkRow();
  await loadColumns();
  await loadProxies();
  await loadCaptchaKey();
  await loadCookieServiceKey();
  await refreshTasks();
  setInterval(refreshTasks, 3000);
  // Баланс spfa.ru меняется не так часто, как задачи — обновляем раз в минуту,
  // а не при каждом refreshTasks, чтобы зря не дёргать их API
  setInterval(loadCookieServiceKey, 60000);
}

init();
