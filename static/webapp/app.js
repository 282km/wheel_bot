/* global Telegram */

const $ = (sel) => document.querySelector(sel);

let token = null;
let role = "user";
let participants = [];
let allParticipants = [];
let selectedIds = [];
let editingParticipantId = null;
let historyItemsCache = [];
let wiredDndZones = new WeakSet();
let silentSpinRunning = false;
let silentCurrentSessionId = null;
let silentCurrentSegments = [];
let silentWheelRotationDeg = 0;
let silentColorById = new Map();
let silentCenterOverlayTimer = null;

function getTg() {
  return window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;
}

function onClick(sel, handler) {
  const el = typeof sel === "string" ? $(sel) : sel;
  if (el) el.addEventListener("click", handler);
}

function onSubmit(sel, handler) {
  const el = typeof sel === "string" ? $(sel) : sel;
  if (el) el.addEventListener("submit", handler);
}

function resolveTelegramInitData() {
  const tg = getTg();
  if (!tg) {
    return { data: "", tg: null, reason: "no_telegram" };
  }
  if (tg.initData) {
    return { data: tg.initData, tg, reason: "ok" };
  }
  const params = new URLSearchParams(window.location.search);
  if (params.has("hash") && params.has("auth_date")) {
    return { data: params.toString(), tg, reason: "url_query" };
  }
  return { data: "", tg, reason: "empty" };
}

async function waitForInitData(maxMs = 3000) {
  const started = Date.now();
  while (Date.now() - started < maxMs) {
    const resolved = resolveTelegramInitData();
    if (resolved.data) return resolved;
    await sleep(100);
  }
  return resolveTelegramInitData();
}

function tgAlert(msg) {
  const tg = getTg();
  if (tg && tg.showAlert) {
    tg.showAlert(String(msg));
    return;
  }
  alert(String(msg));
}

function tgConfirm(msg) {
  const tg = getTg();
  return new Promise((resolve) => {
    if (tg && tg.showConfirm) {
      tg.showConfirm(String(msg), (ok) => resolve(Boolean(ok)));
      return;
    }
    resolve(confirm(String(msg)));
  });
}

function ensureAddFormReady() {
  const form = $("#add-form");
  if (!form) return;
  const nick = form.querySelector('input[name="nick"]');
  const desc = form.querySelector('input[name="desc"]');
  const submit = form.querySelector('button[type="submit"]');
  for (const el of [nick, desc, submit]) {
    if (!el) continue;
    el.disabled = false;
    if ("readOnly" in el) el.readOnly = false;
    el.style.pointerEvents = "auto";
  }
  // Helps Telegram WebView recover focus after confirmations.
  setTimeout(() => {
    if (nick) nick.focus();
  }, 30);
}

function resetParticipantForm() {
  editingParticipantId = null;
  const form = $("#add-form");
  if (!form) return;
  form.reset();
  const submit = $("#add-submit");
  if (submit) submit.textContent = "Добавить";
  const cancel = $("#add-cancel-edit");
  if (cancel) cancel.classList.add("hidden");
}

function enterParticipantEditMode(p) {
  editingParticipantId = Number(p.id);
  const form = $("#add-form");
  if (!form) return;
  const nick = form.querySelector('input[name="nick"]');
  const desc = form.querySelector('input[name="desc"]');
  if (nick) nick.value = p.poker_nick || "";
  if (desc) desc.value = p.description || "";
  const submit = $("#add-submit");
  if (submit) submit.textContent = "Сохранить";
  const cancel = $("#add-cancel-edit");
  if (cancel) cancel.classList.remove("hidden");
  if (nick) nick.focus();
}

function api(path, opts = {}) {
  const headers = Object.assign({}, opts.headers || {});
  if (token) headers.Authorization = `Bearer ${token}`;
  headers["Content-Type"] = "application/json";
  return fetch(path, Object.assign({}, opts, { headers })).then(async (r) => {
    const txt = await r.text();
    let data = null;
    try {
      data = txt ? JSON.parse(txt) : null;
    } catch {
      data = { raw: txt };
    }
    if (!r.ok) {
      const msg = (data && data.error) || `HTTP ${r.status}`;
      throw new Error(msg);
    }
    return data;
  });
}

function participantLabel(p) {
  return `${p.poker_nick} (${p.description || "—"})`;
}

function wheelLineForParticipant(p) {
  const d = String(p.description || "").trim();
  return d ? `${p.poker_nick} (${d})` : String(p.poker_nick);
}

function buildWheelPlainText() {
  const lines = [];
  let n = 0;
  for (const id of selectedIds) {
    const p = participants.find((x) => x.id === id);
    if (!p) continue;
    n += 1;
    lines.push(`${n}. ${wheelLineForParticipant(p)}`);
  }
  return lines.join("\n");
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function wheelPaletteByHue(h) {
  return `hsl(${Number(h || 0)}, 70%, 48%)`;
}

function hueForSilentId(id, fallbackIdx, total) {
  const key = String(Number(id));
  if (silentColorById.has(key)) return silentColorById.get(key);
  const idx = Number.isFinite(fallbackIdx) ? fallbackIdx : silentColorById.size;
  const base = (idx * 360) / Math.max(1, total || 1);
  silentColorById.set(key, base);
  return base;
}

function resetSilentColorMap(ids) {
  silentColorById = new Map();
  const src = Array.isArray(ids) ? ids : [];
  const total = Math.max(1, src.length);
  src.forEach((id, idx) => {
    silentColorById.set(String(Number(id)), (idx * 360) / total);
  });
}

function silentColorForParticipantId(id) {
  const key = String(Number(id));
  if (!silentColorById.has(key)) {
    hueForSilentId(id, silentColorById.size, Math.max(1, silentColorById.size + 1));
  }
  return wheelPaletteByHue(silentColorById.get(key) ?? 0);
}

function buildSilentSegments(roster) {
  const src = Array.isArray(roster) ? roster : [];
  return src.map((p, idx) => {
    const hue = hueForSilentId(p.id, idx, src.length);
    return {
      ...p,
      num: idx + 1,
      hue,
      color: wheelPaletteByHue(hue),
    };
  });
}

function hideSilentCenterOverlay() {
  if (silentCenterOverlayTimer) {
    clearTimeout(silentCenterOverlayTimer);
    silentCenterOverlayTimer = null;
  }
  const el = $("#silent-wheel-center");
  if (!el) return;
  el.classList.add("hidden");
  el.innerHTML = "";
}

function showSilentCenterOverlay(roundNo, winnerNick, prize, holdMs = 2600) {
  const el = $("#silent-wheel-center");
  if (!el) return;
  hideSilentCenterOverlay();
  el.innerHTML = `<div class="swc-round">Раунд ${escapeHtml(String(roundNo))}</div>
    <div class="swc-nick">${escapeHtml(String(winnerNick || ""))}</div>
    <div class="swc-prize">${escapeHtml(fmtMoney(prize))}</div>`;
  el.classList.remove("hidden");
  silentCenterOverlayTimer = setTimeout(() => {
    el.classList.add("hidden");
    el.innerHTML = "";
    silentCenterOverlayTimer = null;
  }, holdMs);
}

const SILENT_WHEEL_MAX_PX = 560;
const SILENT_WHEEL_VW = 0.92;

function silentCanvasCssSize() {
  const canvas = $("#silent-wheel-canvas");
  if (canvas) {
    const w = canvas.getBoundingClientRect().width;
    if (w > 20) return Math.round(w);
  }
  const vw = window.visualViewport?.width || window.innerWidth || SILENT_WHEEL_MAX_PX;
  return Math.round(Math.min(SILENT_WHEEL_MAX_PX, Math.max(280, vw * SILENT_WHEEL_VW)));
}

function ensureSilentCanvas() {
  const canvas = $("#silent-wheel-canvas");
  if (!canvas) return null;
  const css = silentCanvasCssSize();
  const dpr = Math.min(2, window.devicePixelRatio || 1);
  const px = Math.round(css * dpr);
  if (canvas.width !== px || canvas.height !== px) {
    canvas.width = px;
    canvas.height = px;
  }
  return canvas;
}

function hslFromHue(h) {
  return `hsl(${Number(h || 0)}, 62%, 48%)`;
}

function setSilentWheelRotation(deg, animate) {
  const canvas = $("#silent-wheel-canvas");
  if (!canvas) return;
  canvas.style.transition = animate ? "transform 5s cubic-bezier(0.11, 0.72, 0.2, 1)" : "none";
  canvas.style.transform = `rotate(${deg}deg)`;
}

function fitSingleLineNick(ctx, nick, maxWidth, maxFont, minFont) {
  const raw = String(nick || "").trim();
  if (!raw) return { text: "", size: minFont };
  for (let size = maxFont; size >= minFont; size -= 1) {
    ctx.font = `600 ${size}px system-ui, sans-serif`;
    if (ctx.measureText(raw).width <= maxWidth) {
      return { text: raw, size };
    }
  }
  ctx.font = `600 ${minFont}px system-ui, sans-serif`;
  let text = raw;
  if (ctx.measureText(text).width <= maxWidth) {
    return { text, size: minFont };
  }
  while (text.length > 1 && ctx.measureText(`${text}…`).width > maxWidth) {
    text = text.slice(0, -1);
  }
  return { text: `${text}…`, size: minFont };
}

function drawSilentSectorNick(ctx, nick, maxWidth, maxFont, minFont) {
  const { text, size } = fitSingleLineNick(ctx, nick, maxWidth, maxFont, minFont);
  ctx.font = `600 ${size}px system-ui, sans-serif`;
  ctx.strokeText(text, 0, 0);
  ctx.fillText(text, 0, 0);
}

function drawSilentWheelCanvas(roster) {
  const canvas = ensureSilentCanvas();
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  const size = canvas.width;
  const css = silentCanvasCssSize();
  const scale = size / css;
  ctx.setTransform(scale, 0, 0, scale, 0, 0);
  ctx.clearRect(0, 0, css, css);

  if (!roster || !roster.length) {
    ctx.fillStyle = "#1a1e2a";
    ctx.fillRect(0, 0, css, css);
    ctx.fillStyle = "#d6def0";
    ctx.font = `600 ${Math.max(13, Math.round(css / 28))}px system-ui, sans-serif`;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText("Добавьте участников", css / 2, css / 2 - 10);
    ctx.fillText("и нажмите «Крутить колесо»", css / 2, css / 2 + 12);
    return;
  }

  const n = roster.length;
  const cx = css / 2;
  const cy = css / 2;
  const pad = Math.max(8, css * 0.03);
  const outerR = css / 2 - pad;
  const hubRatio = n > 18 ? 0.12 : n > 12 ? 0.15 : n > 8 ? 0.18 : 0.24;
  const hubR = Math.max(outerR * hubRatio, css * 0.08);
  const step = (Math.PI * 2) / n;
  const labelR = (outerR + hubR) / 2;
  const bandH = outerR - hubR;
  const maxFont = Math.max(8, Math.min(13, Math.round(bandH * 0.3)));
  const minFont = n > 18 ? 6 : n > 12 ? 7 : 8;
  ctx.textBaseline = "middle";
  ctx.textAlign = "center";

  for (let i = 0; i < n; i += 1) {
    const start = -Math.PI / 2 + i * step;
    const end = start + step;
    const mid = start + step / 2;
    ctx.beginPath();
    ctx.moveTo(cx, cy);
    ctx.arc(cx, cy, outerR, start, end);
    ctx.closePath();
    ctx.fillStyle = roster[i].color || hslFromHue(roster[i].hue);
    ctx.fill();
    ctx.strokeStyle = "rgba(20, 20, 20, 0.55)";
    ctx.lineWidth = 1.2;
    ctx.stroke();
  }

  ctx.beginPath();
  ctx.arc(cx, cy, hubR, 0, Math.PI * 2);
  ctx.fillStyle = "#121622";
  ctx.fill();
  ctx.strokeStyle = "rgba(255, 255, 255, 0.12)";
  ctx.lineWidth = 2;
  ctx.stroke();

  for (let i = 0; i < n; i += 1) {
    const start = -Math.PI / 2 + i * step;
    const end = start + step;
    const mid = start + step / 2;
    const lx = cx + labelR * Math.cos(mid);
    const ly = cy + labelR * Math.sin(mid);
    const nick = String(roster[i].nick || "").trim();
    const maxW = 2 * labelR * Math.sin(step / 2) * 0.96;

    ctx.save();
    ctx.beginPath();
    ctx.moveTo(cx, cy);
    ctx.arc(cx, cy, outerR, start, end);
    ctx.closePath();
    ctx.clip();
    ctx.translate(lx, ly);
    let rot = mid;
    if (Math.cos(rot) < 0) rot += Math.PI;
    ctx.rotate(rot);
    ctx.fillStyle = "#ffffff";
    ctx.strokeStyle = "rgba(0, 0, 0, 0.85)";
    ctx.lineWidth = 2.5;
    ctx.lineJoin = "round";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    drawSilentSectorNick(ctx, nick, maxW, maxFont, minFont);
    ctx.restore();
  }
}

function renderParticipants() {
  const root = $("#plist");
  if (!root) return;
  root.innerHTML = "";
  const visible = allParticipants.filter((x) => !x.is_hidden);
  if (!visible.length) {
    root.innerHTML = '<div class="card"><small>Список пуст. Добавьте участника формой выше.</small></div>';
    return;
  }
  for (const p of visible) {
    const div = document.createElement("div");
    div.className = "card";
    const hiddenBadge = p.is_hidden ? ' <small>(скрыт)</small>' : "";
    const hideLabel = p.is_hidden ? "Показать" : "Скрыть";
    div.innerHTML = `
      <div><strong>${escapeHtml(p.poker_nick)}</strong> <small>#${p.id}</small>${hiddenBadge}</div>
      <div><small>${escapeHtml(p.description || "")}</small></div>
      <div class="row" style="margin-top:8px">
        <button data-act="edit" data-id="${p.id}" type="button">Изменить</button>
        <button data-act="hide" data-id="${p.id}" type="button">${hideLabel}</button>
        <button data-act="del" data-id="${p.id}" type="button">Удалить</button>
      </div>
    `;
    root.appendChild(div);
  }
  root.onclick = async (e) => {
    const btn = e.target.closest("button");
    if (!btn) return;
    const id = Number(btn.dataset.id);
    const act = btn.dataset.act;
    const p = allParticipants.find((x) => x.id === id);
    if (!p) return;
    if (act === "edit") {
      enterParticipantEditMode(p);
    }
    if (act === "del") {
      if (!(await tgConfirm("Удалить участника?"))) return;
      try {
        await api(`/api/participants/${id}`, { method: "DELETE" });
      } catch (err) {
        tgAlert(err.message || String(err));
        return;
      }
      resetParticipantForm();
      await reloadParticipants();
      await reloadDraftUi();
    }
    if (act === "hide") {
      await api(`/api/participants/${id}`, {
        method: "PATCH",
        body: JSON.stringify({ is_hidden: !Boolean(p.is_hidden) }),
      });
      await reloadParticipants();
      await reloadDraftUi();
    }
  };
}

function renderHiddenParticipants() {
  const root = $("#hidden-plist");
  if (!root) return;
  root.innerHTML = "";
  const hidden = allParticipants.filter((x) => x.is_hidden);
  if (!hidden.length) {
    root.innerHTML = '<div class="card"><small>Скрытых участников нет.</small></div>';
    return;
  }
  for (const p of hidden) {
    const div = document.createElement("div");
    div.className = "card";
    div.innerHTML = `
      <div><strong>${escapeHtml(p.poker_nick)}</strong> <small>#${p.id}</small></div>
      <div><small>${escapeHtml(p.description || "")}</small></div>
      <div class="row" style="margin-top:8px">
        <button data-act="unhide" data-id="${p.id}" type="button">Вернуть в общий список</button>
      </div>
    `;
    root.appendChild(div);
  }
  root.onclick = async (e) => {
    const btn = e.target.closest("button");
    if (!btn) return;
    const id = Number(btn.dataset.id);
    if (!id) return;
    await api(`/api/participants/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ is_hidden: false }),
    });
    await reloadParticipants();
    await reloadDraftUi();
  };
}

function escapeHtml(s) {
  return String(s)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function renderPoolAndPicked() {
  renderWheelRoster("#pool", "#picked", "#depositor", { numbered: false });
  renderWheelRoster("#pool-silent", "#picked-silent", "#depositor-silent", { numbered: true });
  resetSilentColorMap(selectedIds);
  const rosterPreview = selectedIds
    .map((id, idx) => {
      const p = participants.find((x) => x.id === id);
      if (!p) return null;
      return {
        id: p.id,
        nick: p.poker_nick,
        description: p.description || "",
      };
    })
    .filter(Boolean);
  silentCurrentSegments = buildSilentSegments(rosterPreview);
  paintSilentWheel(silentCurrentSegments);
}

function renderWheelRoster(poolSel, pickedSel, depositorSel, opts = {}) {
  const numbered = Boolean(opts.numbered);
  const pool = $(poolSel);
  const picked = $(pickedSel);
  if (!pool || !picked) return;
  pool.innerHTML = "";
  picked.innerHTML = "";

  const sel = new Set(selectedIds);
  for (const p of participants) {
    if (sel.has(p.id)) continue;
    pool.appendChild(renderCard(p, "pool"));
  }
  for (let i = 0; i < selectedIds.length; i += 1) {
    const id = selectedIds[i];
    const p = participants.find((x) => x.id === id);
    if (!p) continue;
    picked.appendChild(renderCard(p, "picked", numbered ? i + 1 : null));
  }

  wireDnD(pool, picked);
  refreshDepositorSelect(depositorSel);
}

function renderCard(p, side, wheelNumber = null) {
  const div = document.createElement("div");
  div.className = "card card-wheel";
  div.draggable = true;
  div.dataset.pid = String(p.id);
  const numHtml = wheelNumber != null ? `<span class="wheel-num">${wheelNumber}.</span>` : "";
  const actionHtml =
    side === "pool"
      ? `<div class="row card-wheel-actions"><button type="button" class="btn-wheel-add">Добавить</button></div>`
      : `<div class="row card-wheel-actions"><button type="button" class="btn-wheel-remove">Убрать</button></div>`;
  div.innerHTML = `<div><strong>${numHtml}${escapeHtml(p.poker_nick)}</strong></div>
    <div><small>${escapeHtml(p.description || "")}</small></div>${actionHtml}`;
  const btn = div.querySelector(".card-wheel-actions button");
  if (btn) {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      e.preventDefault();
      if (side === "pool") {
        if (!selectedIds.includes(p.id)) selectedIds = [...selectedIds, p.id];
      } else {
        selectedIds = selectedIds.filter((id) => id !== p.id);
      }
      renderPoolAndPicked();
    });
  }
  div.addEventListener("dragstart", (ev) => {
    ev.dataTransfer.setData("text/plain", String(p.id));
    ev.dataTransfer.effectAllowed = "move";
  });
  return div;
}

function wireDnD(pool, picked) {
  if (wiredDndZones.has(pool) || wiredDndZones.has(picked)) return;
  wiredDndZones.add(pool);
  wiredDndZones.add(picked);
  const zones = [pool, picked];
  for (const z of zones) {
    z.addEventListener("dragover", (e) => {
      e.preventDefault();
    });
    z.addEventListener("drop", (e) => {
      e.preventDefault();
      const id = Number(e.dataTransfer.getData("text/plain"));
      if (!id) return;
      const isPicked = z === picked;
      const set = new Set(selectedIds);
      if (isPicked) set.add(id);
      else set.delete(id);
      selectedIds = Array.from(set);
      renderPoolAndPicked();
    });
  }
}

function refreshDepositorSelect(selQuery = "#depositor") {
  const sel = $(selQuery);
  if (!sel) return;
  const prev = Number(sel.value || "0");
  sel.innerHTML = "";
  const opt0 = document.createElement("option");
  opt0.value = "";
  opt0.textContent = "— выберите —";
  sel.appendChild(opt0);
  for (const id of selectedIds) {
    const p = participants.find((x) => x.id === id);
    if (!p) continue;
    const o = document.createElement("option");
    o.value = String(p.id);
    o.textContent = participantLabel(p);
    sel.appendChild(o);
  }
  if (prev && selectedIds.includes(prev)) sel.value = String(prev);
}

async function reloadParticipants() {
  const data = await api("/api/participants");
  allParticipants = data.participants || [];
  renderParticipants();
  renderHiddenParticipants();
  ensureAddFormReady();
}

async function reloadDraftUi() {
  const data = await api("/api/wheel/draft");
  participants = data.participants || [];
  selectedIds = data.selected_ids || [];
  renderPoolAndPicked();
  ensureAddFormReady();
}

async function saveDraft() {
  $("#draft-status").textContent = "Сохранение…";
  await api("/api/wheel/draft", {
    method: "PUT",
    body: JSON.stringify({ selected_ids: selectedIds }),
  });
  $("#draft-status").textContent = "Сохранено";
  setTimeout(() => ($("#draft-status").textContent = ""), 1200);
}

function setTab(name) {
  for (const b of document.querySelectorAll(".tabs button")) b.classList.toggle("active", b.dataset.tab === name);
  $("#tab-home").classList.toggle("hidden", name !== "home");
  $("#tab-participants").classList.toggle("hidden", name !== "participants");
  $("#tab-hidden-participants").classList.toggle("hidden", name !== "hidden_participants");
  $("#tab-wheel").classList.toggle("hidden", name !== "wheel");
  $("#tab-wheel-silent").classList.toggle("hidden", name !== "wheel_silent");
  $("#tab-history").classList.toggle("hidden", name !== "history");
  $("#tab-templates").classList.toggle("hidden", name !== "templates");
  $("#tab-admins").classList.toggle("hidden", name !== "admins");
  if (name === "history") {
    reloadHistory().catch((e) => {
      const tg = window.Telegram && window.Telegram.WebApp;
      if (tg && tg.showAlert) tg.showAlert(String(e && e.message ? e.message : e));
    });
  }
  if (name === "templates") {
    reloadTemplates().catch((e) => tgAlert(String(e && e.message ? e.message : e)));
  }
  if (name === "wheel_silent") {
    requestAnimationFrame(() => paintSilentWheel(silentCurrentSegments));
  }
  if (name === "participants") {
    reloadParticipants().catch((e) => tgAlert(String(e && e.message ? e.message : e)));
  }
}

function bindAdminTestChatButton(btn) {
  if (!btn || btn.dataset.bound === "1") return;
  btn.dataset.bound = "1";
  btn.addEventListener("click", async () => {
    btn.disabled = true;
    try {
      const res = await api("/api/admin/test-chat", { method: "POST" });
      tgAlert(`Тестовое сообщение отправлено в чат ${res.chat_id}.`);
    } catch (err) {
      tgAlert(String(err && err.message ? err.message : err));
    } finally {
      btn.disabled = false;
    }
  });
}

function renderHome(roleName) {
  const root = $("#home-content");
  if (!root) return;
  if (roleName === "admin" || roleName === "superadmin") {
    root.innerHTML = `
      <div><strong>Добро пожаловать в управление колесом 🎡</strong></div>
      <div class="muted" style="margin-top:8px">
        Используйте вкладки для управления участниками, запуском колеса, историей и шаблонами сообщений.
      </div>
      <div class="card" style="margin-top:12px">
        <div><strong>Проверка чата</strong></div>
        <p class="muted" style="margin:8px 0">
          Быстрый тест: отправить сообщение в рабочий чат бота.
        </p>
        <button id="admin-test-chat-home" type="button">Тестовое сообщение в чат</button>
      </div>
    `;
    bindAdminTestChatButton($("#admin-test-chat-home"));
    return;
  }
  root.innerHTML = `
    <div><strong>Доступ ограничен ⛔</strong></div>
    <div class="muted" style="margin-top:8px">
      У вас нет прав для работы с приложением. Обратитесь к суперадмину.
    </div>
  `;
}

function fmtMoney(x) {
  return `$${Number(x || 0).toLocaleString("ru-RU")}`;
}

async function reloadHistory() {
  const data = await api("/api/wheel/history");
  historyItemsCache = data.items || [];
  renderHistory();
}

async function reloadTemplates() {
  const data = await api("/api/message-templates");
  const t = data.templates || {};
  $("#tpl-announce").value = t.announce || "";
  $("#tpl-round-caption").value = t.round_caption || "";
  $("#tpl-finish").value = t.finish || "";
}

function periodBounds(key) {
  const now = new Date();
  const y = now.getUTCFullYear();
  const m = now.getUTCMonth();
  const d = now.getUTCDate();
  const startToday = new Date(Date.UTC(y, m, d, 0, 0, 0));
  if (key === "today") return [startToday, new Date(Date.UTC(y, m, d + 1, 0, 0, 0))];
  if (key === "cur_month") return [new Date(Date.UTC(y, m, 1, 0, 0, 0)), new Date(Date.UTC(y, m + 1, 1, 0, 0, 0))];
  if (key === "prev_month") return [new Date(Date.UTC(y, m - 1, 1, 0, 0, 0)), new Date(Date.UTC(y, m, 1, 0, 0, 0))];
  if (key === "cur_year") return [new Date(Date.UTC(y, 0, 1, 0, 0, 0)), new Date(Date.UTC(y + 1, 0, 1, 0, 0, 0))];
  if (key === "prev_year") return [new Date(Date.UTC(y - 1, 0, 1, 0, 0, 0)), new Date(Date.UTC(y, 0, 1, 0, 0, 0))];
  return [null, null];
}

function filterHistoryItems(items) {
  const key = String($("#history-period")?.value || "today");
  const [start, end] = periodBounds(key);
  if (!start || !end) return items;
  return items.filter((it) => {
    const dt = new Date(it.created_at);
    return dt >= start && dt < end;
  });
}

function renderHistory() {
  const root = $("#history-list");
  root.innerHTML = "";
  const items = filterHistoryItems(historyItemsCache);
  if (!items.length) {
    root.innerHTML = '<div class="card"><small>История пока пустая.</small></div>';
    return;
  }
  for (const it of items) {
    const winners = (it.winners || [])
      .map((w, i) => `${i + 1}. ${escapeHtml(w.nick)} — ${fmtMoney(w.prize)}`)
      .join("<br/>");
    const div = document.createElement("div");
    div.className = "card";
    div.innerHTML = `
      <div><strong>Колесо #${it.id}</strong></div>
      <div><small>Дата: ${escapeHtml(it.created_at)}</small></div>
      <div><small>Кто занёс: ${escapeHtml(it.depositor_nick || "—")}</small></div>
      <div><small>Сумма заноса: ${fmtMoney(it.deposit_amount)}</small></div>
      <div><small>Призовой фонд: ${fmtMoney(it.prizes_sum)}</small></div>
      <div><small>Победителей: ${Number(it.winners_count || 0)}</small></div>
      <div style="margin-top:6px"><small>Победители:</small><br/><small>${winners || "—"}</small></div>
    `;
    root.appendChild(div);
  }
}

async function reloadAdmins() {
  const data = await api("/api/admins");
  const root = $("#alist");
  root.innerHTML = "";
  for (const a of data.admins || []) {
    const div = document.createElement("div");
    div.className = "card";
    div.innerHTML = `<div><strong>${a.telegram_id}</strong> — ${escapeHtml(a.role)}</div>
      <div class="row" style="margin-top:8px">
        <button data-del="${a.telegram_id}" type="button" ${a.role === "superadmin" ? "disabled" : ""}>Убрать права</button>
      </div>`;
    root.appendChild(div);
  }
  root.onclick = async (e) => {
    const btn = e.target.closest("button");
    if (!btn) return;
    const tid = Number(btn.dataset.del);
    if (!tid) return;
    if (!(await tgConfirm(`Убрать права администратора у ${tid}?`))) return;
    await api(`/api/admins/${tid}`, { method: "DELETE" });
    await reloadAdmins();
  };
}

function renderSilentResults(items) {
  const root = $("#silent-wheel-results");
  if (!root) return;
  root.innerHTML = "";
  for (const it of items) {
    const div = document.createElement("div");
    div.className = "silent-winner-card";
    const color = silentColorForParticipantId(it.winner_id);
    div.innerHTML = `<strong>${it.round}. <span class="silent-dot" style="background:${color}"></span> ${escapeHtml(
      it.winner_nick
    )}</strong> <small>— ${fmtMoney(it.prize)}</small>`;
    root.appendChild(div);
  }
}

function paintSilentWheel(roster) {
  hideSilentCenterOverlay();
  silentWheelRotationDeg = 0;
  drawSilentWheelCanvas(roster);
  const canvas = $("#silent-wheel-canvas");
  if (canvas) {
    setSilentWheelRotation(0, false);
    void canvas.offsetWidth;
  }
}

async function animateSilentRound(round) {
  const canvas = ensureSilentCanvas();
  const winnerLine = $("#silent-wheel-winner");
  if (!canvas || !winnerLine) return;
  const baseRoster = round.roster || [];
  const roster = buildSilentSegments(baseRoster);
  silentCurrentSegments = roster;
  const winnerIdx = roster.findIndex((x) => Number(x.id) === Number(round.winner_id));
  if (!roster.length || winnerIdx < 0) return;

  const seg = 360 / roster.length;
  const stopDeg = -((winnerIdx + 0.5) * seg);
  const total = 360 * 7 + stopDeg;

  hideSilentCenterOverlay();
  drawSilentWheelCanvas(roster);
  setSilentWheelRotation(0, false);
  void canvas.offsetWidth;
  silentWheelRotationDeg = total;
  setSilentWheelRotation(total, true);

  winnerLine.textContent = `Раунд ${round.round}: крутится...`;
  await sleep(5000);

  const winnerColor = silentColorForParticipantId(round.winner_id);
  showSilentCenterOverlay(round.round, round.winner_nick, round.prize, 2600);
  winnerLine.innerHTML = `Раунд ${round.round}: <strong><span class="silent-dot" style="background:${winnerColor}"></span> ${escapeHtml(
    round.winner_nick
  )}</strong> — ${fmtMoney(round.prize)}`;
  await sleep(2800);
}

function showBootError(detail) {
  const appEl = document.getElementById("app");
  const errEl = document.getElementById("boot-error");
  if (appEl) appEl.classList.add("hidden");
  if (errEl) {
    errEl.classList.remove("hidden");
    const slot = errEl.querySelector("[data-boot-detail]");
    if (slot && detail) slot.textContent = String(detail);
  }
}

async function boot() {
  const init = await waitForInitData();
  const tg = init.tg;
  if (!tg || !init.data) {
    const hint =
      init.reason === "no_telegram"
        ? "Страница открыта не в Telegram. Закройте браузер и откройте WebApp из бота."
        : "В Telegram: личка бота → /start → кнопка «🎡 Управление колесом» (или меню «Колесо» слева внизу).";
    showBootError(hint);
    return;
  }
  tg.ready();
  tg.expand();

  let sess;
  try {
    sess = await api("/api/session", {
      method: "POST",
      body: JSON.stringify({ initData: init.data }),
    });
  } catch (err) {
    const msg = String(err && err.message ? err.message : err);
    if (msg.includes("bad hash")) {
      showBootError("Ошибка авторизации (bad hash). Проверьте BOT_TOKEN в .env — он должен совпадать с токеном бота в BotFather.");
    } else {
      showBootError(`Ошибка API /api/session: ${msg}`);
    }
    throw err;
  }
  token = sess.token;
  role = sess.role;

  const me = await api("/api/me");
  $("#whoami").textContent = `Вы: ${me.telegram_id}, роль: ${me.role}`;

  const tabs = $("#tabs");
  tabs.innerHTML = "";
  const mk = (id, label) => {
    const b = document.createElement("button");
    b.type = "button";
    b.dataset.tab = id;
    b.classList.add("tab-nav");
    b.textContent = label;
    b.addEventListener("click", () => setTab(id));
    tabs.appendChild(b);
  };

  mk("home", "Главная");

  if (me.role === "admin" || me.role === "superadmin") {
    mk("participants", "Участники");
    mk("wheel", "Колесо");
    mk("wheel_silent", "Колесо (тишина)");
    mk("history", "История колес");
    mk("templates", "Шаблоны сообщений");
    mk("admins", "Админ");
    mk("hidden_participants", "Скрытые участники");
  }
  const adminMgmt = $("#admin-mgmt-block");
  if (adminMgmt) adminMgmt.classList.toggle("hidden", me.role !== "superadmin");

  renderHome(me.role);
  const firstTabBtn = document.querySelector('.tabs button[data-tab="home"]');
  if (firstTabBtn) firstTabBtn.classList.add("active");
  setTab("home");

  if (me.role === "user") {
    return;
  }

  $("#add-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    const nick = String(fd.get("nick") || "").trim();
    const desc = String(fd.get("desc") || "").trim();
    try {
      if (editingParticipantId) {
        await api(`/api/participants/${editingParticipantId}`, {
          method: "PATCH",
          body: JSON.stringify({ poker_nick: nick, description: desc }),
        });
      } else {
        await api("/api/participants", {
          method: "POST",
          body: JSON.stringify({ poker_nick: nick, description: desc }),
        });
      }
      resetParticipantForm();
      await reloadParticipants();
      await reloadDraftUi();
    } catch (err) {
      const msg = String(err && err.message ? err.message : err);
      if (msg.includes("nick already exists")) {
        tgAlert("Участник с таким ником уже существует.");
      } else {
        tgAlert(`Ошибка при добавлении участника: ${msg}`);
      }
      ensureAddFormReady();
    }
  });
  $("#add-cancel-edit").addEventListener("click", () => {
    resetParticipantForm();
    ensureAddFormReady();
  });

  $("#save-draft").addEventListener("click", saveDraft);
  const saveDraftSilent = $("#save-draft-silent");
  if (saveDraftSilent) saveDraftSilent.addEventListener("click", saveDraft);
  $("#add-all").addEventListener("click", () => {
    selectedIds = participants.map((p) => p.id);
    renderPoolAndPicked();
  });
  const addAllSilent = $("#add-all-silent");
  if (addAllSilent) {
    addAllSilent.addEventListener("click", () => {
      selectedIds = participants.map((p) => p.id);
      renderPoolAndPicked();
    });
  }
  $("#clear-all").addEventListener("click", () => {
    selectedIds = [];
    renderPoolAndPicked();
  });
  const clearAllSilent = $("#clear-all-silent");
  if (clearAllSilent) {
    clearAllSilent.addEventListener("click", () => {
      selectedIds = [];
      renderPoolAndPicked();
    });
  }

  const btnCopyWheel = $("#wheel-copy-list");
  if (btnCopyWheel) {
    btnCopyWheel.addEventListener("click", async () => {
      const text = buildWheelPlainText().trim();
      if (!text) {
        tgAlert("Добавьте участников в текущее колесо.");
        return;
      }
      try {
        await navigator.clipboard.writeText(text);
        tgAlert("Список скопирован в буфер.");
      } catch {
        tgAlert("Не удалось скопировать в буфер. Попробуйте ещё раз или скопируйте состав из списка справа вручную.");
      }
    });
  }

  const btnSendPreview = $("#wheel-send-preview");
  if (btnSendPreview) {
    btnSendPreview.addEventListener("click", async () => {
      if (!selectedIds.length) {
        tgAlert("Добавьте участников в текущее колесо.");
        return;
      }
      try {
        await api("/api/wheel/preview-send", {
          method: "POST",
          body: JSON.stringify({ selected_ids: selectedIds }),
        });
        tgAlert("Список отправлен в чат.");
      } catch (err) {
        tgAlert(String(err.message || err));
      }
    });
  }

  const syncSilentFields = () => {
    if ($("#deposit_amount-silent") && $("#deposit_amount")) {
      $("#deposit_amount-silent").value = $("#deposit_amount").value;
    }
    if ($("#prizes-silent") && $("#prizes")) {
      $("#prizes-silent").value = $("#prizes").value;
    }
  };
  syncSilentFields();

  $("#history-period").addEventListener("change", () => {
    renderHistory();
  });

  $("#spin").addEventListener("click", async () => {
    const depositor_id = Number($("#depositor").value || "0");
    const deposit_amount = Number($("#deposit_amount").value || "0");
    const prizesRaw = String($("#prizes").value || "")
      .split(/\r?\n/)
      .map((x) => x.trim())
      .filter(Boolean)
      .map((x) => Number(x));
    const announceDelaySec = Number($("#announce_delay_sec").value || "30");
    $("#spin-log").textContent = "Крутим…";
    try {
      const res = await api("/api/wheel/spin", {
        method: "POST",
        body: JSON.stringify({
          depositor_id,
          deposit_amount,
          prizes: prizesRaw,
          selected_ids: selectedIds,
          announce_delay_sec: announceDelaySec,
        }),
      });
      $("#spin-log").textContent = JSON.stringify(res, null, 2);
      tgAlert("Готово: результаты отправлены в чат.");
    } catch (err) {
      $("#spin-log").textContent = String(err.message || err);
      tgAlert(String(err.message || err));
    }
  });

  const spinSilentBtn = $("#spin-silent");
  if (spinSilentBtn) {
    spinSilentBtn.addEventListener("click", async () => {
      if (silentSpinRunning) return;
      const depositor_id = Number($("#depositor-silent")?.value || "0");
      const deposit_amount = Number($("#deposit_amount-silent")?.value || "0");
      const prizesRaw = String($("#prizes-silent")?.value || "")
        .split(/\r?\n/)
        .map((x) => x.trim())
        .filter(Boolean)
        .map((x) => Number(x));
      const log = $("#spin-silent-log");
      const winnerLine = $("#silent-wheel-winner");
      const sendBtn = $("#silent-send-results");
      log.textContent = "Готовим локальное колесо...";
      if (sendBtn) sendBtn.disabled = true;
      resetSilentColorMap(selectedIds);
      silentSpinRunning = true;
      hideSilentCenterOverlay();
      renderSilentResults([]);
      try {
        const res = await api("/api/wheel/silent-spin", {
          method: "POST",
          body: JSON.stringify({
            depositor_id,
            deposit_amount,
            prizes: prizesRaw,
            selected_ids: selectedIds,
            session_id: silentCurrentSessionId,
          }),
        });
        const rounds = Array.isArray(res.rounds) ? res.rounds : [];
        for (const round of rounds) {
          await animateSilentRound(round);
        }
        renderSilentResults(rounds);
        silentCurrentSessionId = Number(res.session_id || 0) || null;
        if (winnerLine) winnerLine.textContent = "Кручение завершено. Проверьте победителей и отправьте результаты в чат.";
        if (sendBtn) sendBtn.disabled = !silentCurrentSessionId;
        log.textContent = JSON.stringify({ session_id: res.session_id, rounds: rounds.length }, null, 2);
      } catch (err) {
        log.textContent = String(err.message || err);
        tgAlert(String(err.message || err));
      } finally {
        silentSpinRunning = false;
      }
    });
  }

  const sendSilentResultsBtn = $("#silent-send-results");
  if (sendSilentResultsBtn) {
    sendSilentResultsBtn.addEventListener("click", async () => {
      if (!silentCurrentSessionId) {
        tgAlert("Сначала выполните кручение в режиме тишины.");
        return;
      }
      try {
        await api("/api/wheel/silent-send-results", {
          method: "POST",
          body: JSON.stringify({ session_id: silentCurrentSessionId }),
        });
        tgAlert("Результаты отправлены в чат.");
        sendSilentResultsBtn.disabled = true;
      } catch (err) {
        tgAlert(String(err.message || err));
      }
    });
  }

  const sendSilentAnnounceBtn = $("#silent-send-announce");
  if (sendSilentAnnounceBtn) {
    sendSilentAnnounceBtn.addEventListener("click", async () => {
      const depositor_id = Number($("#depositor-silent")?.value || "0");
      const deposit_amount = Number($("#deposit_amount-silent")?.value || "0");
      const prizesRaw = String($("#prizes-silent")?.value || "")
        .split(/\r?\n/)
        .map((x) => x.trim())
        .filter(Boolean)
        .map((x) => Number(x));
      try {
        const res = await api("/api/wheel/silent-announce", {
          method: "POST",
          body: JSON.stringify({
            depositor_id,
            deposit_amount,
            prizes: prizesRaw,
            selected_ids: selectedIds,
          }),
        });
        silentCurrentSessionId = Number(res.session_id || 0) || null;
        tgAlert("Анонс отправлен в чат.");
      } catch (err) {
        tgAlert(String(err.message || err));
      }
    });
  }

  bindAdminTestChatButton($("#admin-test-chat"));

  const adminForm = $("#admin-form");
  if (adminForm) {
    adminForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      const fd = new FormData(e.target);
      const tid = Number(fd.get("tid") || "0");
      await api("/api/admins", { method: "POST", body: JSON.stringify({ telegram_id: tid }) });
      e.target.reset();
      await reloadAdmins();
    });
  }
  onSubmit("#templates-form", async (e) => {
    e.preventDefault();
    await api("/api/message-templates", {
      method: "PUT",
      body: JSON.stringify({
        templates: {
          announce: String($("#tpl-announce").value || ""),
          round_caption: String($("#tpl-round-caption").value || ""),
          finish: String($("#tpl-finish").value || ""),
        },
      }),
    });
    tgAlert("Шаблоны сохранены.");
  });
  onClick("#tpl-reset-defaults", async () => {
    if (!(await tgConfirm("Вернуть стандартные шаблоны сообщений?"))) return;
    await api("/api/message-templates/reset", { method: "POST" });
    await reloadTemplates();
    tgAlert("Стандартные шаблоны восстановлены.");
  });

  let silentResizeTimer = null;
  window.addEventListener("resize", () => {
    clearTimeout(silentResizeTimer);
    silentResizeTimer = setTimeout(() => {
      paintSilentWheel(silentCurrentSegments);
    }, 120);
  });

  await reloadParticipants();
  await reloadDraftUi();
  ensureAddFormReady();
  if (me.role === "superadmin") {
    try {
      await reloadAdmins();
    } catch {
      /* ignore */
    }
  }
}

boot().catch((e) => {
  console.error(e);
  const msg = String(e && e.message ? e.message : e);
  if (!document.getElementById("boot-error")?.classList.contains("hidden")) return;
  showBootError(`Ошибка запуска: ${msg}`);
  tgAlert(msg);
});
