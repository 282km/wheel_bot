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
let wiredPointerZones = new WeakSet();
let dragParticipantId = null;
let pointerReorder = null;
let silentSpinRunning = false;
let silentAnnounceSessionId = null;
let silentSpunSessionId = null;
let silentCurrentSegments = [];
let silentWheelRotationDeg = 0;
let silentColorById = new Map();
let silentCenterOverlayTimer = null;
let wheelPostTarget = "channel";

function getTg() {
  return window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;
}

let fullscreenEventsWired = false;

function isFullscreenApiSupported() {
  const tg = getTg();
  if (!tg || typeof tg.requestFullscreen !== "function") return false;
  if (typeof tg.isVersionAtLeast === "function") return tg.isVersionAtLeast("8.0");
  return true;
}

function isTgFullscreen() {
  const tg = getTg();
  return Boolean(tg && tg.isFullscreen);
}

function updateFullscreenHomeButton() {
  const btn = $("#home-fullscreen-btn");
  const hint = $("#home-fullscreen-hint");
  if (!btn) return;
  if (!isFullscreenApiSupported()) {
    btn.disabled = true;
    btn.textContent = "На весь экран";
    if (hint) {
      hint.textContent =
        "Недоступно: обновите Telegram (Desktop или телефон) до версии с Mini Apps 2.0.";
    }
    return;
  }
  btn.disabled = false;
  if (hint) hint.textContent = "Удобно на ПК: разворачивает Mini App внутри Telegram.";
  btn.textContent = isTgFullscreen() ? "Выйти из полноэкранного режима" : "На весь экран";
}

function toggleTgFullscreen() {
  const tg = getTg();
  if (!tg) {
    tgAlert("Откройте приложение через Telegram, не в браузере.");
    return;
  }
  if (!isFullscreenApiSupported()) {
    tgAlert("Полный экран не поддерживается этой версией Telegram. Обновите клиент.");
    return;
  }
  try {
    if (isTgFullscreen()) {
      tg.exitFullscreen();
    } else {
      tg.expand();
      tg.requestFullscreen();
    }
  } catch (err) {
    tgAlert(String(err && err.message ? err.message : err));
  }
  updateFullscreenHomeButton();
}

function wireFullscreenHomeEvents() {
  if (fullscreenEventsWired) return;
  const tg = getTg();
  if (!tg || typeof tg.onEvent !== "function") return;
  fullscreenEventsWired = true;
  tg.onEvent("fullscreenChanged", () => updateFullscreenHomeButton());
  tg.onEvent("fullscreenFailed", (payload) => {
    const code = payload && payload.error ? payload.error : "UNKNOWN";
    if (code !== "ALREADY_FULLSCREEN") {
      tgAlert(`Не удалось включить полный экран (${code}).`);
    }
    updateFullscreenHomeButton();
  });
}

function bindFullscreenHomeButton() {
  wireFullscreenHomeEvents();
  const btn = $("#home-fullscreen-btn");
  if (!btn || btn.dataset.bound === "1") return;
  btn.dataset.bound = "1";
  btn.addEventListener("click", () => toggleTgFullscreen());
  updateFullscreenHomeButton();
}

function onClick(sel, handler) {
  const el = typeof sel === "string" ? $(sel) : sel;
  if (el) el.addEventListener("click", handler);
}

function onSubmit(sel, handler) {
  const el = typeof sel === "string" ? $(sel) : sel;
  if (el) el.addEventListener("submit", handler);
}

function flashButton(el, kind = "ok") {
  if (!el) return;
  el.classList.remove("btn-flash-ok", "btn-flash-err");
  void el.offsetWidth;
  el.classList.add(kind === "err" ? "btn-flash-err" : "btn-flash-ok");
  window.setTimeout(() => el.classList.remove("btn-flash-ok", "btn-flash-err"), 560);
}

function tapButton(el, kind = "ok") {
  flashButton(el, kind);
}

async function withButtonFeedback(btn, fn) {
  if (!btn) return fn();
  if (btn.disabled) return;
  btn.classList.add("is-busy");
  btn.disabled = true;
  try {
    const result = await fn();
    flashButton(btn, "ok");
    return result;
  } catch (err) {
    flashButton(btn, "err");
    throw err;
  } finally {
    btn.classList.remove("is-busy");
    btn.disabled = false;
  }
}

function setDraftStatus(text, kind = "") {
  for (const el of [$("#draft-status"), $("#draft-status-silent")]) {
    if (!el) continue;
    el.textContent = text;
    el.classList.remove("draft-status-ok", "draft-status-err");
    if (kind === "ok") el.classList.add("draft-status-ok");
    if (kind === "err") el.classList.add("draft-status-err");
  }
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

function silentFlowState() {
  if (silentSpunSessionId) return "spun";
  if (silentAnnounceSessionId) return "announced";
  return "idle";
}

function updateSilentSessionStatus() {
  const el = $("#silent-session-status");
  if (el) {
    const parts = [];
    const state = silentFlowState();
    if (silentAnnounceSessionId) {
      parts.push(`анонс: колесо #${silentAnnounceSessionId}`);
    }
    if (silentSpunSessionId) {
      parts.push(`кручение: колесо #${silentSpunSessionId}`);
    }
    if (state === "announced") {
      parts.push("ожидает кручения");
    } else if (state === "spun") {
      parts.push("можно отправить результаты");
    }
    el.textContent = parts.length ? parts.join(" · ") : "";
  }
  updateSilentWheelControls();
}

function updateSilentWheelControls() {
  const announceBtn = $("#silent-send-announce");
  const spinBtn = $("#spin-silent");
  const cancelBtn = $("#silent-cancel-wheel");
  const resultsBtn = $("#silent-send-results");
  const state = silentFlowState();

  if (announceBtn) {
    announceBtn.disabled = state !== "idle" || silentSpinRunning;
  }
  if (spinBtn) {
    spinBtn.disabled = state !== "announced" || silentSpinRunning;
    spinBtn.title =
      state === "idle"
        ? "Сначала отправьте анонс в чат"
        : state === "spun"
          ? "Колесо уже прокручено"
          : "";
  }
  if (cancelBtn) {
    cancelBtn.classList.toggle("hidden", state !== "announced");
    cancelBtn.disabled = silentSpinRunning;
  }
  if (resultsBtn) {
    resultsBtn.disabled = state !== "spun" || silentSpinRunning;
    resultsBtn.title = state === "spun" ? "" : "Сначала анонс и кручение колеса";
  }
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

function participantDescHtml(description) {
  const desc = String(description || "").trim();
  if (!desc) return "";
  return `<span class="card-participant-desc" title="${escapeHtml(desc)}">${escapeHtml(desc)}</span>`;
}

function buildParticipantCardHtml(p, rowNum, actionsHtml) {
  return `
    <div class="card-participant-row">
      <span class="wheel-num" aria-hidden="true">${rowNum}</span>
      <div class="card-participant-main">
        <span class="card-participant-nick">${escapeHtml(p.poker_nick)}</span>
        ${participantDescHtml(p.description)}
        <span class="card-participant-id">#${p.id}</span>
      </div>
      <div class="card-participant-actions">${actionsHtml}</div>
    </div>`;
}

function renderParticipants() {
  const root = $("#plist");
  if (!root) return;
  root.innerHTML = "";
  const visible = allParticipants
    .filter((x) => !x.is_hidden)
    .sort((a, b) =>
      String(a.poker_nick || "").localeCompare(String(b.poker_nick || ""), "ru", { sensitivity: "base" })
    );
  if (!visible.length) {
    root.innerHTML = '<div class="card card-participant-compact"><small>Список пуст. Добавьте участника формой выше.</small></div>';
    return;
  }
  visible.forEach((p, index) => {
    const div = document.createElement("div");
    div.className = "card card-participant-compact";
    const actionsHtml = `
        <button class="btn-participant-mini" data-act="edit" data-id="${p.id}" type="button" title="Изменить">Изм.</button>
        <button class="btn-participant-mini" data-act="hide" data-id="${p.id}" type="button" title="Скрыть из списка">Скрыть</button>
        <button class="btn-participant-mini" data-act="del" data-id="${p.id}" type="button" title="Удалить">Удал.</button>`;
    div.innerHTML = buildParticipantCardHtml(p, index + 1, actionsHtml);
    root.appendChild(div);
  });
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
  const hidden = allParticipants
    .filter((x) => x.is_hidden)
    .sort((a, b) =>
      String(a.poker_nick || "").localeCompare(String(b.poker_nick || ""), "ru", { sensitivity: "base" })
    );
  if (!hidden.length) {
    root.innerHTML = '<div class="card card-participant-compact"><small>Скрытых участников нет.</small></div>';
    return;
  }
  hidden.forEach((p, index) => {
    const div = document.createElement("div");
    div.className = "card card-participant-compact";
    const actionsHtml = `
        <button class="btn-participant-mini" data-act="unhide" data-id="${p.id}" type="button" title="Вернуть в общий список">Вернуть</button>`;
    div.innerHTML = buildParticipantCardHtml(p, index + 1, actionsHtml);
    root.appendChild(div);
  });
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

const PRIZES_EDITOR_MIN_ROWS = 3;

function renumberPrizeRows(rowsEl) {
  const rows = [...rowsEl.querySelectorAll(".prize-row")];
  rows.forEach((row, i) => {
    const badge = row.querySelector(".prize-round-badge");
    if (badge) badge.textContent = String(i + 1);
    const rm = row.querySelector(".prize-row-remove");
    if (rm) rm.disabled = rows.length <= 1;
  });
}

function createPrizeRow(value = "") {
  const row = document.createElement("div");
  row.className = "prize-row";

  const badge = document.createElement("span");
  badge.className = "prize-round-badge";
  badge.textContent = "1";

  const input = document.createElement("input");
  input.type = "text";
  input.inputMode = "decimal";
  input.className = "prize-row-input";
  input.placeholder = "Сумма в $";
  input.value = value == null ? "" : String(value);

  const removeBtn = document.createElement("button");
  removeBtn.type = "button";
  removeBtn.className = "prize-row-remove";
  removeBtn.textContent = "×";
  removeBtn.title = "Убрать раунд";
  removeBtn.addEventListener("click", () => {
    const rowsEl = row.parentElement;
    if (!rowsEl || rowsEl.querySelectorAll(".prize-row").length <= 1) return;
    row.remove();
    renumberPrizeRows(rowsEl);
  });

  row.appendChild(badge);
  row.appendChild(input);
  row.appendChild(removeBtn);
  return row;
}

function renderPrizesRows(rowsEl, values = [], minRows = PRIZES_EDITOR_MIN_ROWS) {
  rowsEl.innerHTML = "";
  const vals = Array.isArray(values) ? values : [];
  const count = Math.max(vals.length, minRows);
  for (let i = 0; i < count; i += 1) {
    rowsEl.appendChild(createPrizeRow(vals[i] ?? ""));
  }
  renumberPrizeRows(rowsEl);
}

function getPrizesEditorValues(editorSel) {
  const root = $(editorSel);
  if (!root) return [];
  return [...root.querySelectorAll(".prize-row-input")].map((inp) => String(inp.value || "").trim());
}

function getPrizesForApi(editorSel) {
  return getPrizesEditorValues(editorSel)
    .filter(Boolean)
    .map((x) => Number(x));
}

function setPrizesEditorValues(editorSel, values) {
  const root = $(editorSel);
  if (!root) return;
  const rowsEl = root.querySelector(".prizes-rows");
  if (!rowsEl) return;
  renderPrizesRows(rowsEl, values);
}

function bindPrizesEditor(editorSel) {
  const root = $(editorSel);
  if (!root || root.dataset.bound === "1") return;
  root.dataset.bound = "1";
  const rowsEl = root.querySelector(".prizes-rows");
  const addBtn = root.querySelector(".prizes-add-row");
  if (!rowsEl) return;

  renderPrizesRows(rowsEl);

  if (addBtn) {
    addBtn.addEventListener("click", () => {
      rowsEl.appendChild(createPrizeRow(""));
      renumberPrizeRows(rowsEl);
      const inputs = rowsEl.querySelectorAll(".prize-row-input");
      const last = inputs[inputs.length - 1];
      if (last) last.focus();
    });
  }
}

function initPrizesEditors() {
  bindPrizesEditor("#prizes-editor");
  bindPrizesEditor("#prizes-silent-editor");
}

function renderPoolAndPicked() {
  renderWheelRoster("#pool", "#picked", "#depositor", { numbered: true });
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
  div.className = "card card-wheel card-wheel-compact";
  div.draggable = true;
  div.dataset.pid = String(p.id);
  const desc = String(p.description || "").trim();
  const numHtml =
    wheelNumber != null ? `<span class="wheel-num" aria-hidden="true">${wheelNumber}</span>` : "";
  const gripHtml =
    side === "picked" ? '<span class="card-wheel-grip" aria-hidden="true" title="Удерживайте для смены номера">⠿</span>' : "";
  const descHtml = desc
    ? `<span class="card-wheel-desc" title="${escapeHtml(desc)}">${escapeHtml(desc)}</span>`
    : "";
  const btnClass = side === "pool" ? "btn-wheel-add btn-wheel-mini" : "btn-wheel-remove btn-wheel-mini";
  const btnLabel = side === "pool" ? "+" : "×";
  const btnTitle = side === "pool" ? "Добавить в колесо" : "Убрать из колеса";
  div.innerHTML = `
    <div class="card-wheel-row">
      ${gripHtml}
      ${numHtml}
      <div class="card-wheel-main">
        <span class="card-wheel-nick">${escapeHtml(p.poker_nick)}</span>
        ${descHtml}
      </div>
      <button type="button" class="${btnClass}" title="${btnTitle}" aria-label="${btnTitle}">${btnLabel}</button>
    </div>`;
  const btn = div.querySelector("button");
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
    dragParticipantId = p.id;
    ev.dataTransfer.setData("text/plain", String(p.id));
    ev.dataTransfer.effectAllowed = "move";
    div.classList.add("card-wheel-dragging");
  });
  div.addEventListener("dragend", () => {
    dragParticipantId = null;
    div.classList.remove("card-wheel-dragging");
    clearPickedDragMarkers();
  });
  if (btn) btn.draggable = false;
  return div;
}

function clearPickedDragMarkers() {
  for (const el of document.querySelectorAll(
    ".card-wheel-drag-before, .card-wheel-drag-after, .card-wheel-drag-empty"
  )) {
    el.classList.remove("card-wheel-drag-before", "card-wheel-drag-after", "card-wheel-drag-empty");
  }
}

function pickedCardsForDrop(pickedEl, draggedId = null) {
  return [...pickedEl.querySelectorAll(":scope > .card-wheel[data-pid]")].filter((el) => {
    if (!draggedId) return true;
    return Number(el.dataset.pid) !== draggedId;
  });
}

/** Индекс вставки в список без перетаскиваемой карточки (0 … cards.length). */
function getDropIndexInPicked(pickedEl, clientY, draggedId = null) {
  const cards = pickedCardsForDrop(pickedEl, draggedId);
  if (!cards.length) return 0;
  for (let i = 0; i < cards.length; i += 1) {
    const rect = cards[i].getBoundingClientRect();
    if (clientY < rect.top + rect.height / 2) return i;
  }
  return cards.length;
}

function markPickedInsertIndex(pickedEl, insertIndex, draggedId = null) {
  clearPickedDragMarkers();
  const cards = pickedCardsForDrop(pickedEl, draggedId);
  if (!cards.length) {
    pickedEl.classList.add("card-wheel-drag-empty");
    return;
  }
  if (insertIndex < cards.length) {
    cards[insertIndex].classList.add("card-wheel-drag-before");
  } else {
    cards[cards.length - 1].classList.add("card-wheel-drag-after");
  }
}

/**
 * @param {number} insertIndex — позиция в массиве после удаления id (как getDropIndexInPicked с draggedId).
 */
function reorderSelectedIds(id, insertIndex) {
  const fromIdx = selectedIds.indexOf(id);
  const next = selectedIds.filter((x) => x !== id);
  const idx = Math.max(0, Math.min(insertIndex, next.length));
  if (fromIdx === idx) return;
  next.splice(idx, 0, id);
  selectedIds = next;
}

function finishPointerReorder(commit) {
  if (!pointerReorder) return;
  const { id, picked, card } = pointerReorder;
  picked.classList.remove("is-reordering");
  card.classList.remove("card-wheel-dragging");
  try {
    card.releasePointerCapture(pointerReorder.pointerId);
  } catch {
    /* already released */
  }
  if (commit) {
    const insertAt = getDropIndexInPicked(picked, pointerReorder.lastY, id);
    reorderSelectedIds(id, insertAt);
    renderPoolAndPicked();
  } else {
    clearPickedDragMarkers();
  }
  pointerReorder = null;
}

function wirePointerReorder(picked) {
  if (wiredPointerZones.has(picked)) return;
  wiredPointerZones.add(picked);

  picked.addEventListener(
    "pointerdown",
    (e) => {
      if (pointerReorder || e.button !== 0) return;
      const card = e.target.closest(".card-wheel[data-pid]");
      if (!card || !picked.contains(card) || e.target.closest("button")) return;
      const id = Number(card.dataset.pid);
      if (!selectedIds.includes(id)) return;

      pointerReorder = {
        id,
        picked,
        card,
        pointerId: e.pointerId,
        lastY: e.clientY,
      };
      picked.classList.add("is-reordering");
      card.classList.add("card-wheel-dragging");
      card.setPointerCapture(e.pointerId);
      const insertAt = getDropIndexInPicked(picked, e.clientY, id);
      markPickedInsertIndex(picked, insertAt, id);
      e.preventDefault();
    },
    { passive: false }
  );

  picked.addEventListener(
    "pointermove",
    (e) => {
      if (!pointerReorder || e.pointerId !== pointerReorder.pointerId) return;
      pointerReorder.lastY = e.clientY;
      e.preventDefault();
      const insertAt = getDropIndexInPicked(picked, e.clientY, pointerReorder.id);
      markPickedInsertIndex(picked, insertAt, pointerReorder.id);
    },
    { passive: false }
  );

  picked.addEventListener("pointerup", (e) => {
    if (!pointerReorder || e.pointerId !== pointerReorder.pointerId) return;
    e.preventDefault();
    finishPointerReorder(true);
  });

  picked.addEventListener("pointercancel", (e) => {
    if (!pointerReorder || e.pointerId !== pointerReorder.pointerId) return;
    finishPointerReorder(false);
  });
}

function wireDnD(pool, picked) {
  if (!wiredDndZones.has(pool)) {
    wiredDndZones.add(pool);
    pool.addEventListener("dragover", (e) => {
      e.preventDefault();
      e.dataTransfer.dropEffect = "move";
      clearPickedDragMarkers();
    });
    pool.addEventListener("drop", (e) => {
      e.preventDefault();
      clearPickedDragMarkers();
      const id = Number(e.dataTransfer.getData("text/plain"));
      if (!id) return;
      selectedIds = selectedIds.filter((x) => x !== id);
      renderPoolAndPicked();
    });
  }

  if (!wiredDndZones.has(picked)) {
    wiredDndZones.add(picked);
    const onPickedDragOver = (e) => {
      e.preventDefault();
      e.stopPropagation();
      e.dataTransfer.dropEffect = "move";
      const draggedId = dragParticipantId;
      const insertAt = getDropIndexInPicked(picked, e.clientY, draggedId);
      markPickedInsertIndex(picked, insertAt, draggedId);
    };

    picked.addEventListener("dragover", onPickedDragOver, true);

    picked.addEventListener("dragleave", (e) => {
      if (!picked.contains(e.relatedTarget)) clearPickedDragMarkers();
    });

    picked.addEventListener("drop", (e) => {
      e.preventDefault();
      e.stopPropagation();
      clearPickedDragMarkers();
      const id = Number(e.dataTransfer.getData("text/plain"));
      if (!id) return;
      const draggedId = dragParticipantId === id ? id : null;
      const insertAt = getDropIndexInPicked(picked, e.clientY, draggedId);
      if (selectedIds.includes(id)) {
        reorderSelectedIds(id, insertAt);
      } else {
        const next = selectedIds.filter((x) => x !== id);
        const idx = Math.max(0, Math.min(insertAt, next.length));
        next.splice(idx, 0, id);
        selectedIds = next;
      }
      renderPoolAndPicked();
    });
  }

  wirePointerReorder(picked);
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
  const list = [...participants].sort((a, b) =>
    String(a.poker_nick || "").localeCompare(String(b.poker_nick || ""), "ru")
  );
  for (const p of list) {
    const o = document.createElement("option");
    o.value = String(p.id);
    o.textContent = participantLabel(p);
    sel.appendChild(o);
  }
  if (prev && list.some((x) => Number(x.id) === prev)) sel.value = String(prev);
}

async function reloadParticipants() {
  const data = await api("/api/participants");
  allParticipants = data.participants || [];
  renderParticipants();
  renderHiddenParticipants();
  ensureAddFormReady();
}

function applySilentFlowFromServer(silentFlow) {
  if (silentFlow && silentFlow.session_id) {
    const sid = Number(silentFlow.session_id);
    silentAnnounceSessionId = sid || null;
    silentSpunSessionId = silentFlow.phase === "spun" ? sid : null;
  } else {
    silentAnnounceSessionId = null;
    silentSpunSessionId = null;
  }
  updateSilentSessionStatus();
}

async function reloadDraftUi() {
  const data = await api("/api/wheel/draft");
  participants = data.participants || [];
  selectedIds = data.selected_ids || [];
  applySilentFlowFromServer(data.silent_flow);
  renderPoolAndPicked();
  ensureAddFormReady();
}

async function saveDraft(triggerBtn) {
  const saveBtns = [$("#save-draft"), $("#save-draft-silent")].filter(Boolean);
  setDraftStatus("Сохранение…");
  for (const b of saveBtns) b.classList.add("is-busy");
  try {
    await api("/api/wheel/draft", {
      method: "PUT",
      body: JSON.stringify({ selected_ids: selectedIds }),
    });
    setDraftStatus("Сохранено ✓", "ok");
    for (const b of saveBtns) flashButton(b, "ok");
    if (triggerBtn && !saveBtns.includes(triggerBtn)) flashButton(triggerBtn, "ok");
    window.setTimeout(() => setDraftStatus(""), 2200);
  } catch (err) {
    setDraftStatus("Ошибка", "err");
    if (triggerBtn) flashButton(triggerBtn, "err");
    throw err;
  } finally {
    for (const b of saveBtns) b.classList.remove("is-busy");
  }
}

function bindSaveDraftClick(btn) {
  if (!btn || btn.dataset.bound === "1") return;
  btn.dataset.bound = "1";
  btn.addEventListener("click", async (e) => {
    try {
      await saveDraft(e.currentTarget);
    } catch (err) {
      tgAlert(String(err && err.message ? err.message : err));
    }
  });
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
  if (name === "home") updateFullscreenHomeButton();
  if (name === "history") {
    reloadHistory().catch((e) => {
      const tg = window.Telegram && window.Telegram.WebApp;
      if (tg && tg.showAlert) tg.showAlert(String(e && e.message ? e.message : e));
    });
  }
  if (name === "templates") {
    reloadTemplates().catch((e) => tgAlert(String(e && e.message ? e.message : e)));
  }
  if (name === "admins") {
    loadMorningDigestSettings().catch((e) => tgAlert(String(e && e.message ? e.message : e)));
  }
  if (name === "wheel_silent") {
    requestAnimationFrame(() => paintSilentWheel(silentCurrentSegments));
    updateSilentWheelControls();
  }
  if (name === "participants") {
    reloadParticipants().catch((e) => tgAlert(String(e && e.message ? e.message : e)));
  }
}

function updateWheelPostStatusUi(data) {
  const target = data?.target === "chat" ? "chat" : "channel";
  wheelPostTarget = target;
  const statusEl = $("#wheel-post-status");
  const hintEl = $("#wheel-post-hint");
  if (statusEl) {
    const dest =
      target === "channel"
        ? `канал (${data.channel_chat_id ?? "не задан"})`
        : `чат (${data.stats_chat_id})`;
    statusEl.textContent = `Сейчас постим в: ${dest}`;
    if (target === "channel" && !data.channel_configured) {
      statusEl.textContent +=
        ". Задайте WHEEL_CHANNEL_ID в .env на сервере или снимите галку (постинг в чат).";
    }
  }
  if (hintEl) {
    hintEl.textContent =
      target === "channel" ? "Сообщения колеса уйдут в канал." : "Сообщения колеса уйдут в чат.";
  }
}

async function loadWheelPostSettings() {
  const data = await api("/api/wheel/post-settings");
  const cb = $("#wheel-post-to-channel");
  if (cb) cb.checked = data.target !== "chat";
  updateWheelPostStatusUi(data);
}

function bindAdminTestButton(btn, path, label) {
  if (!btn || btn.dataset.bound === "1") return;
  btn.dataset.bound = "1";
  btn.addEventListener("click", async () => {
    try {
      await withButtonFeedback(btn, async () => {
        const res = await api(path, { method: "POST" });
        tgAlert(`Тест отправлен в ${label} (${res.chat_id}).`);
      });
    } catch (err) {
      tgAlert(String(err && err.message ? err.message : err));
    }
  });
}

function bindWheelPostSettings() {
  const cb = $("#wheel-post-to-channel");
  if (cb && cb.dataset.bound !== "1") {
    cb.dataset.bound = "1";
    cb.addEventListener("change", async () => {
      const target = cb.checked ? "channel" : "chat";
      try {
        const data = await api("/api/wheel/post-settings", {
          method: "PUT",
          body: JSON.stringify({ target }),
        });
        updateWheelPostStatusUi(data);
      } catch (err) {
        cb.checked = !cb.checked;
        tgAlert(String(err && err.message ? err.message : err));
      }
    });
  }
  bindAdminTestButton($("#admin-test-chat"), "/api/admin/test-chat", "чат");
  bindAdminTestButton($("#admin-test-channel"), "/api/admin/test-channel", "канал");
}

function updateMorningDigestUi(data) {
  const enabled = $("#morning-digest-enabled");
  const hour = $("#morning-digest-hour");
  const status = $("#morning-digest-status");
  if (enabled) enabled.checked = !!data.enabled;
  if (hour) hour.value = String(data.hour ?? 8);
  if (status) {
    status.textContent = data.enabled
      ? `Включён, отправка в ${data.hour ?? 8}:00 (${data.timezone || "Europe/Moscow"}).`
      : "Выключен — утренние посты не отправляются.";
  }
}

async function loadMorningDigestSettings() {
  const data = await api("/api/admin/morning-digest");
  updateMorningDigestUi(data);
  return data;
}

function bindMorningDigestSettings() {
  const form = $("#morning-digest-form");
  const testBtn = $("#morning-digest-test");
  if (form && form.dataset.bound !== "1") {
    form.dataset.bound = "1";
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const submitBtn = form.querySelector('button[type="submit"]');
      const body = {
        enabled: !!$("#morning-digest-enabled")?.checked,
        hour: Number($("#morning-digest-hour")?.value || 8),
      };
      try {
        await withButtonFeedback(submitBtn, async () => {
          const data = await api("/api/admin/morning-digest", {
            method: "PUT",
            body: JSON.stringify(body),
          });
          updateMorningDigestUi(data);
          tgAlert("Настройки утреннего дайджеста сохранены.");
        });
      } catch (err) {
        tgAlert(String(err && err.message ? err.message : err));
      }
    });
  }
  if (testBtn && testBtn.dataset.bound !== "1") {
    testBtn.dataset.bound = "1";
    testBtn.addEventListener("click", async () => {
      const preview = $("#morning-digest-preview");
      if (preview) preview.textContent = "Запускаю тест…";
      try {
        await withButtonFeedback(testBtn, async () => {
          const res = await api("/api/admin/morning-digest/test", { method: "POST" });
          if (preview) {
            preview.textContent =
              res.message ||
              "Тест запущен — пост придёт вам в личку через ~5–20 сек.";
          }
        });
      } catch (err) {
        if (preview) preview.textContent = "";
        tgAlert(String(err && err.message ? err.message : err));
      }
    });
  }
}

function homeFullscreenBlockHtml() {
  return `
      <div class="home-fullscreen-block">
        <button id="home-fullscreen-btn" type="button" class="primary home-fullscreen-btn">На весь экран</button>
        <p id="home-fullscreen-hint" class="muted home-fullscreen-hint"></p>
      </div>`;
}

function renderHome(roleName) {
  const root = $("#home-content");
  if (!root) return;
  if (roleName === "admin" || roleName === "superadmin") {
    root.innerHTML = `
      <div><strong>Добро пожаловать в управление колесом 🎡</strong></div>
      <div class="muted" style="margin-top:8px">
        Используйте вкладки для управления участниками, запуском колеса, историей и шаблонами сообщений.
        Настройка канала и тесты — во вкладке «Админ».
      </div>
      ${homeFullscreenBlockHtml()}
    `;
    bindFullscreenHomeButton();
    return;
  }
  root.innerHTML = `
    <div><strong>Доступ ограничен ⛔</strong></div>
    <div class="muted" style="margin-top:8px">
      У вас нет прав для работы с приложением. Обратитесь к суперадмину.
    </div>
    ${homeFullscreenBlockHtml()}
  `;
  bindFullscreenHomeButton();
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
    b.addEventListener("click", (e) => {
      tapButton(e.currentTarget);
      setTab(id);
    });
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
    const submitBtn = e.submitter || $("#add-submit");
    const fd = new FormData(e.target);
    const nick = String(fd.get("nick") || "").trim();
    const desc = String(fd.get("desc") || "").trim();
    try {
      await withButtonFeedback(submitBtn, async () => {
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
      });
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
  $("#add-cancel-edit").addEventListener("click", (e) => {
    tapButton(e.currentTarget);
    resetParticipantForm();
    ensureAddFormReady();
  });

  bindSaveDraftClick($("#save-draft"));
  bindSaveDraftClick($("#save-draft-silent"));
  $("#add-all").addEventListener("click", (e) => {
    tapButton(e.currentTarget);
    selectedIds = participants.map((p) => p.id);
    renderPoolAndPicked();
  });
  const addAllSilent = $("#add-all-silent");
  if (addAllSilent) {
    addAllSilent.addEventListener("click", (e) => {
      tapButton(e.currentTarget);
      selectedIds = participants.map((p) => p.id);
      renderPoolAndPicked();
    });
  }
  $("#clear-all").addEventListener("click", (e) => {
    tapButton(e.currentTarget);
    selectedIds = [];
    renderPoolAndPicked();
  });
  const clearAllSilent = $("#clear-all-silent");
  if (clearAllSilent) {
    clearAllSilent.addEventListener("click", (e) => {
      tapButton(e.currentTarget);
      selectedIds = [];
      renderPoolAndPicked();
    });
  }

  const btnCopyWheel = $("#wheel-copy-list");
  if (btnCopyWheel) {
    btnCopyWheel.addEventListener("click", async (e) => {
      const btn = e.currentTarget;
      const text = buildWheelPlainText().trim();
      if (!text) {
        tapButton(btn, "err");
        tgAlert("Добавьте участников в текущее колесо.");
        return;
      }
      try {
        await navigator.clipboard.writeText(text);
        tapButton(btn, "ok");
        tgAlert("Список скопирован в буфер.");
      } catch {
        tapButton(btn, "err");
        tgAlert("Не удалось скопировать в буфер. Попробуйте ещё раз или скопируйте состав из списка справа вручную.");
      }
    });
  }

  const btnSendPreview = $("#wheel-send-preview");
  if (btnSendPreview) {
    btnSendPreview.addEventListener("click", async (e) => {
      const btn = e.currentTarget;
      if (!selectedIds.length) {
        tapButton(btn, "err");
        tgAlert("Добавьте участников в текущее колесо.");
        return;
      }
      try {
        await withButtonFeedback(btn, async () => {
          await api("/api/wheel/preview-send", {
            method: "POST",
            body: JSON.stringify({ selected_ids: selectedIds }),
          });
          tgAlert("Список отправлен в чат.");
        });
      } catch (err) {
        tgAlert(String(err.message || err));
      }
    });
  }

  const syncSilentFields = () => {
    if ($("#prizes-silent-editor") && $("#prizes-editor")) {
      setPrizesEditorValues("#prizes-silent-editor", getPrizesEditorValues("#prizes-editor"));
    }
  };
  initPrizesEditors();
  syncSilentFields();

  $("#history-period").addEventListener("change", () => {
    renderHistory();
  });

  $("#spin").addEventListener("click", async (e) => {
    const btn = e.currentTarget;
    const depositor_id = Number($("#depositor").value || "0");
    const deposit_amount = 0;
    const prizesRaw = getPrizesForApi("#prizes-editor");
    const announceDelaySec = Number($("#announce_delay_sec").value || "30");
    $("#spin-log").textContent = "Крутим…";
    try {
      await withButtonFeedback(btn, async () => {
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
      });
    } catch (err) {
      $("#spin-log").textContent = String(err.message || err);
      tgAlert(String(err.message || err));
    }
  });

  const spinSilentBtn = $("#spin-silent");
  if (spinSilentBtn) {
    spinSilentBtn.addEventListener("click", async (e) => {
      if (silentSpinRunning) return;
      if (!silentAnnounceSessionId) {
        tapButton(e.currentTarget, "err");
        tgAlert("Сначала отправьте анонс в чат. Без анонса крутить нельзя.");
        return;
      }
      if (silentSpunSessionId) {
        tapButton(e.currentTarget, "err");
        tgAlert("Колесо уже прокручено. Отправьте результаты или отмените текущее колесо до кручения.");
        return;
      }
      const spinBtn = e.currentTarget;
      spinBtn.classList.add("is-busy");
      spinBtn.disabled = true;
      updateSilentWheelControls();
      const depositor_id = Number($("#depositor-silent")?.value || "0");
      const deposit_amount = 0;
      const prizesRaw = getPrizesForApi("#prizes-silent-editor");
      const log = $("#spin-silent-log");
      const winnerLine = $("#silent-wheel-winner");
      const sendBtn = $("#silent-send-results");
      log.textContent = "Готовим локальное колесо...";
      updateSilentWheelControls();
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
            session_id: silentAnnounceSessionId,
          }),
        });
        const rounds = Array.isArray(res.rounds) ? res.rounds : [];
        for (const round of rounds) {
          await animateSilentRound(round);
        }
        renderSilentResults(rounds);
        silentSpunSessionId = Number(res.session_id || 0) || null;
        silentAnnounceSessionId = silentSpunSessionId;
        updateSilentSessionStatus();
        if (winnerLine) {
          winnerLine.textContent = `Кручение завершено (колесо #${silentSpunSessionId}). Можно отправить результаты в чат.`;
        }
        log.textContent = JSON.stringify({ session_id: res.session_id, rounds: rounds.length }, null, 2);
        flashButton(spinBtn, "ok");
      } catch (err) {
        log.textContent = String(err.message || err);
        flashButton(spinBtn, "err");
        tgAlert(String(err.message || err));
      } finally {
        silentSpinRunning = false;
        spinBtn.classList.remove("is-busy");
        updateSilentSessionStatus();
      }
    });
  }

  const sendSilentResultsBtn = $("#silent-send-results");
  if (sendSilentResultsBtn) {
    sendSilentResultsBtn.addEventListener("click", async (e) => {
      const btn = e.currentTarget;
      if (!silentSpunSessionId) {
        tapButton(btn, "err");
        tgAlert("Сначала нажмите «Крутить колесо». Анонс сам по себе не создаёт победителей.");
        return;
      }
      btn.classList.add("is-busy");
      btn.disabled = true;
      try {
        await api("/api/wheel/silent-send-results", {
          method: "POST",
          body: JSON.stringify({ session_id: silentSpunSessionId }),
        });
        flashButton(btn, "ok");
        tgAlert(`Результаты колеса #${silentSpunSessionId} отправлены в чат.`);
        silentSpunSessionId = null;
        silentAnnounceSessionId = null;
        updateSilentSessionStatus();
      } catch (err) {
        flashButton(btn, "err");
        btn.disabled = false;
        tgAlert(String(err.message || err));
      } finally {
        btn.classList.remove("is-busy");
      }
    });
  }

  const sendSilentCancelBtn = $("#silent-cancel-wheel");
  if (sendSilentCancelBtn) {
    sendSilentCancelBtn.addEventListener("click", async (e) => {
      const btn = e.currentTarget;
      if (!silentAnnounceSessionId || silentSpunSessionId) {
        tapButton(btn, "err");
        return;
      }
      const sid = silentAnnounceSessionId;
      if (!(await tgConfirm(`Отменить колесо #${sid}? Запись удалится из базы, в чат ничего не отправится.`))) {
        return;
      }
      try {
        await withButtonFeedback(btn, async () => {
          await api("/api/wheel/silent-cancel", {
            method: "POST",
            body: JSON.stringify({ session_id: sid }),
          });
          silentAnnounceSessionId = null;
          silentSpunSessionId = null;
          hideSilentCenterOverlay();
          renderSilentResults([]);
          const winnerLine = $("#silent-wheel-winner");
          if (winnerLine) winnerLine.textContent = "";
          const log = $("#spin-silent-log");
          if (log) log.textContent = "";
          updateSilentSessionStatus();
          tgAlert(`Колесо #${sid} отменено. Можно начать заново с анонса.`);
        });
      } catch (err) {
        tgAlert(String(err && err.message ? err.message : err));
      }
    });
  }

  const sendSilentAnnounceBtn = $("#silent-send-announce");
  if (sendSilentAnnounceBtn) {
    sendSilentAnnounceBtn.addEventListener("click", async (e) => {
      const btn = e.currentTarget;
      if (silentFlowState() !== "idle") {
        tapButton(btn, "err");
        tgAlert("Сначала завершите текущее колесо: прокрутите и отправьте результаты или отмените колесо после анонса.");
        return;
      }
      const depositor_id = Number($("#depositor-silent")?.value || "0");
      const deposit_amount = 0;
      const prizesRaw = getPrizesForApi("#prizes-silent-editor");
      try {
        await withButtonFeedback(btn, async () => {
          const res = await api("/api/wheel/silent-announce", {
            method: "POST",
            body: JSON.stringify({
              depositor_id,
              deposit_amount,
              prizes: prizesRaw,
              selected_ids: selectedIds,
            }),
          });
          silentAnnounceSessionId = Number(res.session_id || 0) || null;
          silentSpunSessionId = null;
          updateSilentSessionStatus();
          tgAlert(
            `Анонс отправлен (колесо #${silentAnnounceSessionId}). Нажмите «Крутить колесо» или «Отменить колесо», если передумали.`
          );
        });
      } catch (err) {
        tgAlert(String(err.message || err));
      }
    });
  }

  bindWheelPostSettings();
  bindMorningDigestSettings();
  try {
    await loadWheelPostSettings();
  } catch (err) {
    tgAlert(String(err && err.message ? err.message : err));
  }

  const adminForm = $("#admin-form");
  if (adminForm) {
    adminForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      const submitBtn = e.submitter || adminForm.querySelector('button[type="submit"]');
      try {
        await withButtonFeedback(submitBtn, async () => {
          const fd = new FormData(e.target);
          const tid = Number(fd.get("tid") || "0");
          await api("/api/admins", { method: "POST", body: JSON.stringify({ telegram_id: tid }) });
          e.target.reset();
          await reloadAdmins();
        });
      } catch (err) {
        tgAlert(String(err && err.message ? err.message : err));
      }
    });
  }
  onSubmit("#templates-form", async (e) => {
    e.preventDefault();
    const submitBtn = e.submitter || e.target.querySelector('button[type="submit"]');
    try {
      await withButtonFeedback(submitBtn, async () => {
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
    } catch (err) {
      tgAlert(String(err && err.message ? err.message : err));
    }
  });
  onClick("#tpl-reset-defaults", async (e) => {
    const btn = e.currentTarget;
    if (!(await tgConfirm("Вернуть стандартные шаблоны сообщений?"))) return;
    try {
      await withButtonFeedback(btn, async () => {
        await api("/api/message-templates/reset", { method: "POST" });
        await reloadTemplates();
        tgAlert("Стандартные шаблоны восстановлены.");
      });
    } catch (err) {
      tgAlert(String(err && err.message ? err.message : err));
    }
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
  updateSilentSessionStatus();
  ensureAddFormReady();
  if (me.role === "superadmin") {
    try {
      await loadMorningDigestSettings();
    } catch {
      /* ignore */
    }
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
