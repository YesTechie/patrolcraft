/* YesTechie Labs Style — PatrolCraft pages. Grid, mono, sidebar // Nav. Not Analyzer. */
const APP_URL = "/pages/app.html#/patrols";
const LOGIN_URL = "/pages/login.html";

const state = {
  cameras: [],
  patrolsByCamera: {},
  openPatrols: new Set(),
  error: "",
  protectConfigured: false,
  apiKeySet: false,
  version: "",
  deviceName: "YesTechie Labs",
  thumbBusy: "",
};

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function tip(text) {
  const escaped = escapeHtml(text);
  return ` title="${escaped}" data-tip="${escaped}"`;
}

function infoIcon(text) {
  const escaped = escapeHtml(text);
  // Light tip is CSS ::after on data-tip. No title: a native title stacks a second dark tooltip.
  return `<button type="button" class="info-i" data-tip="${escaped}" aria-label="${escaped}">(i)</button>`;
}

function labeled(name, text) {
  return `${escapeHtml(name)} ${infoIcon(text)}`;
}

const DWELL_MIN = 0;
const DWELL_MAX = 127;
const SPEED_MIN = 0;
const SPEED_MAX = 1000;
const DWELL_RANGE_MSG = "Dwell must be 0–127 seconds.";
const SPEED_RANGE_MSG = "Speed must be 0–1000.";
const DWELL_TIP = "Seconds to wait at this preset (0–127).";
const SPEED_TIP = "How fast the camera moves to this preset.";

function parseHash() {
  const raw = (location.hash || "#/patrols").replace(/^#/, "");
  const parts = raw.split("/").filter(Boolean);
  const page = parts[0] || "patrols";
  const cameraId = page === "patrols" ? parts[1] || "" : "";
  return { page, cameraId };
}

function showToast(message, isError) {
  const el = document.getElementById("toast");
  if (!el) return;
  el.textContent = message;
  el.className = "toast show " + (isError ? "error" : "success");
  clearTimeout(showToast._timer);
  showToast._timer = setTimeout(() => el.classList.remove("show"), isError ? 4200 : 2200);
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  let body = null;
  const text = await response.text();
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = { error: text };
    }
  }
  if (response.status === 401 && !path.startsWith("/api/login")) {
    location.replace(LOGIN_URL);
    throw new Error("Not signed in");
  }
  if (!response.ok) {
    const message = body?.error || body?.detail || `Request failed (${response.status})`;
    throw new Error(typeof message === "string" ? message : JSON.stringify(message));
  }
  return body;
}

function decodeTitle(element, then) {
  const letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789@#$%&*";
  const targetText = element.dataset.value;
  let iterations = 0;
  const interval = setInterval(() => {
    element.innerText = targetText.split("").map((letter, index) => {
      if (index < iterations) return targetText[index];
      return letters[Math.floor(Math.random() * 42)];
    }).join("");
    if (iterations >= targetText.length) {
      clearInterval(interval);
      then();
    }
    iterations += 1 / 3;
  }, 40);
}

function typeSubtitle(element, text) {
  let i = 0;
  const typeInterval = setInterval(() => {
    element.innerText = text.substring(0, i) + "_";
    i += 1;
    if (i > text.length) {
      clearInterval(typeInterval);
      element.innerText = text;
    }
  }, 50);
}

function initLogin() {
  const title = document.getElementById("title");
  const subtitle = document.getElementById("subtitle");
  const submitBtn = document.getElementById("submit-btn");
  const btnText = submitBtn?.querySelector(".btn-text");
  const errorBox = document.getElementById("error");
  if (!submitBtn) return;

  fetch("/api/health").then((r) => r.json()).then((d) => {
    const el = document.getElementById("appVersion");
    if (el && d.version) el.textContent = "v" + d.version;
  }).catch(() => {});

  if (title) {
    decodeTitle(title, () => typeSubtitle(subtitle, "PTZ Patrol Ops"));
  }

  async function doLogin() {
    errorBox.style.display = "none";
    errorBox.classList.remove("show");
    submitBtn.classList.remove("failed");
    submitBtn.classList.add("processing");
    btnText.innerText = "AUTHENTICATING...";
    try {
      const r = await fetch("/api/login", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          username: document.getElementById("username").value,
          password: document.getElementById("password").value,
        }),
      });
      const d = await r.json().catch(() => ({}));
      if (r.ok && d.ok) {
        submitBtn.classList.remove("processing");
        submitBtn.classList.add("success");
        btnText.innerText = "ACCESS GRANTED";
        setTimeout(() => { window.location.href = APP_URL; }, 800);
      } else {
        submitBtn.classList.remove("processing");
        submitBtn.classList.add("failed");
        btnText.innerText = "ACCESS DENIED";
        errorBox.textContent = d.error || d.detail || "Authentication failed";
        errorBox.style.display = "block";
        errorBox.classList.add("show");
        setTimeout(() => {
          submitBtn.classList.remove("failed");
          btnText.innerText = "SIGN IN";
        }, 1500);
      }
    } catch (e) {
      submitBtn.classList.remove("processing");
      submitBtn.classList.add("failed");
      btnText.innerText = "CONNECTION ERROR";
      errorBox.textContent = "Unable to reach server";
      errorBox.style.display = "block";
      setTimeout(() => {
        submitBtn.classList.remove("failed");
        btnText.innerText = "SIGN IN";
      }, 1500);
    }
  }

  submitBtn.addEventListener("click", (e) => {
    e.preventDefault();
    doLogin();
  });
  document.getElementById("password")?.addEventListener("keydown", (e) => {
    if (e.key === "Enter") doLogin();
  });
  document.getElementById("username")?.addEventListener("keydown", (e) => {
    if (e.key === "Enter") document.getElementById("password").focus();
  });
}

function toggleMenu(force) {
  const sb = document.querySelector(".sidebar");
  const bd = document.getElementById("sidebarBackdrop");
  if (!sb) return;
  const open = force === undefined ? !sb.classList.contains("open") : !!force;
  sb.classList.toggle("open", open);
  if (bd) bd.classList.toggle("show", open);
  document.body.classList.toggle("nav-open", open);
}

function setProtectStatus() {
  const el = document.getElementById("protectStatus");
  if (!el) return;
  if (state.protectConfigured) {
    el.innerHTML = `<span class="dot dot-green"></span>Live`;
  } else {
    el.innerHTML = `<span class="dot dot-yellow"></span>Mock`;
  }
}

function setChrome() {
  const ver = document.getElementById("appVersion");
  if (ver && state.version) ver.textContent = "v" + state.version;
  const site = state.deviceName || "YesTechie Labs";
  const name = document.getElementById("siteName");
  const mobile = document.getElementById("siteNameMobile");
  if (name) name.textContent = site;
  if (mobile) mobile.textContent = site;
  setProtectStatus();
}

async function initApp() {
  try {
    await api("/api/me");
  } catch {
    return;
  }

  const health = await fetch("/api/health").then((r) => r.json()).catch(() => ({}));
  state.protectConfigured = Boolean(health?.protect?.configured);
  state.apiKeySet = Boolean(health?.protect?.apiKeySet);
  state.version = health?.version || "";
  setChrome();

  document.getElementById("menuBtn")?.addEventListener("click", () => toggleMenu());
  document.getElementById("sidebarBackdrop")?.addEventListener("click", () => toggleMenu(false));
  document.getElementById("sec-patrols")?.addEventListener("click", onViewClick);
  document.getElementById("sec-patrols")?.addEventListener("focusout", onPresetFieldBlur);
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") toggleMenu(false);
  });
  window.addEventListener("hashchange", () => {
    render();
    loadRoute();
  });
  render();
  await loadRoute();
}

async function loadRoute() {
  const route = parseHash();
  if (route.page === "config") {
    await loadConfig();
    return;
  }
  if (route.page === "logs") {
    await loadLogs();
    return;
  }
  await loadCameras();
  if (route.cameraId) await loadPatrols(route.cameraId);
}

function showSection(id) {
  document.querySelectorAll(".section").forEach((s) => s.classList.remove("active"));
  document.querySelectorAll(".nav-item").forEach((n) => n.classList.remove("active"));
  const sec = document.getElementById("sec-" + id);
  if (sec) sec.classList.add("active");
  document.querySelector(`[data-nav="${id}"]`)?.classList.add("active");
  toggleMenu(false);
}

function render() {
  const route = parseHash();
  const page = ["patrols", "config", "logs"].includes(route.page) ? route.page : "patrols";
  showSection(page);
  if (page === "config") {
    document.getElementById("sec-config").innerHTML = renderConfigSkeleton();
    return;
  }
  if (page === "logs") {
    document.getElementById("sec-logs").innerHTML = renderLogsSkeleton();
    return;
  }
  document.getElementById("sec-patrols").innerHTML = renderPatrols(route.cameraId);
}

async function loadCameras() {
  state.error = "";
  try {
    const data = await api("/api/cameras");
    state.cameras = data.cameras || [];
    state.protectConfigured = data.mode === "live";
    if (typeof data.apiKeySet === "boolean") state.apiKeySet = data.apiKeySet;
    setProtectStatus();
  } catch (err) {
    state.cameras = [];
    state.error = err.message;
  }
  const route = parseHash();
  if (route.page === "patrols") {
    document.getElementById("sec-patrols").innerHTML = renderPatrols(route.cameraId);
  }
}

async function loadPatrols(cameraId) {
  try {
    const [data, settings] = await Promise.all([
      api(`/api/cameras/${encodeURIComponent(cameraId)}/patrols`),
      api(`/api/cameras/${encodeURIComponent(cameraId)}/settings`).catch(() => null),
    ]);
    state.patrolsByCamera[cameraId] = data.patrols || [];
    const camera = findCamera(cameraId);
    if (camera) camera.patrolCount = state.patrolsByCamera[cameraId].length;
    if (settings && settings.autoTracking) {
      if (camera) camera.autoTracking = settings.autoTracking;
    }
    state.error = "";
  } catch (err) {
    state.patrolsByCamera[cameraId] = [];
    const camera = findCamera(cameraId);
    if (camera) camera.patrolCount = 0;
    state.error = err.message;
  }
  const route = parseHash();
  if (route.page === "patrols" && route.cameraId === cameraId) {
    document.getElementById("sec-patrols").innerHTML = renderPatrols(cameraId);
  }
}

function findCamera(id) {
  return state.cameras.find((camera) => camera.id === id) || null;
}

/* Product photo: marketName, then type, then platform. Protect has no color. */
const CAMERA_IMAGE_DIR = "/img/cameras";
const CAMERA_IMAGE_DEFAULT = "uvc-ai-ptz-precision-white.png";
const CAMERA_IMAGE_OFFLINE = "camera-offline-silhouette.png";
const CAMERA_IMAGE_RULES = [
  ["ai-ptz-precision", ["ai ptz precision", "uvc ai ptz precision", "cv25z"]],
  ["ai-ptz-industrial", ["ai ptz industrial", "uvc ai ptz industrial", "ai ptz", "uvc ai ptz"]],
  ["g6-ptz", ["g6 ptz", "uvc g6 ptz"]],
  ["g5-ptz", ["g5 ptz", "uvc g5 ptz"]],
];

function cameraProductImage(camera) {
  if (!camera) return CAMERA_IMAGE_DIR + "/" + CAMERA_IMAGE_DEFAULT;
  if (camera.productImage) return camera.productImage;
  const override = camera.imageOverride || camera.productImageOverride;
  if (override) {
    const name = String(override).split("/").pop();
    return CAMERA_IMAGE_DIR + "/" + name;
  }
  let slug = "ai-ptz-precision";
  const fields = [camera.marketName, camera.type, camera.platform];
  for (let i = 0; i < fields.length; i += 1) {
    const text = String(fields[i] || "").trim().toLowerCase().replace(/\s+/g, " ");
    if (!text) continue;
    const hit = CAMERA_IMAGE_RULES.find((rule) => rule[1].some((key) => text === key || text.indexOf(key) !== -1));
    if (hit) {
      slug = hit[0];
      break;
    }
  }
  const color = String(camera.housingColor || camera.color || "white").trim().toLowerCase().replace(/\s+/g, "");
  const known = color === "white" ? color : "white";
  return CAMERA_IMAGE_DIR + "/uvc-" + slug + "-" + known + ".png";
}

function displayMeta(value) {
  const text = String(value ?? "").trim();
  return text || "—";
}

function cameraIp(camera) {
  return displayMeta(camera?.ip || camera?.host || camera?.connectionHost);
}

function cameraMac(camera) {
  const raw = String(camera?.mac ?? "").trim();
  if (!raw) return "—";
  const compact = raw.replace(/[:.\-]/g, "").toUpperCase();
  if (/^[0-9A-F]{12}$/.test(compact)) {
    return compact.replace(/(.{2})(?=.)/g, "$1:");
  }
  return raw;
}

function cameraPatrolCountLabel(camera) {
  const loaded = state.patrolsByCamera[camera.id];
  if (Array.isArray(loaded)) return String(loaded.length);
  const count = camera?.patrolCount;
  if (typeof count === "number" && Number.isFinite(count)) return String(Math.max(0, count));
  return "—";
}

function renderCameraTile(camera, selectedId, options = {}) {
  const inGroup = Boolean(options.inGroup);
  const selected = camera.id === selectedId;
  const selectedClass = selected && !inGroup ? " selected" : "";
  const online = String(camera.state || "").toUpperCase() === "CONNECTED";
  const offlineClass = online ? "" : " is-offline";
  const model = camera.marketName || camera.type || "Camera";
  const img = online
    ? cameraProductImage(camera)
    : CAMERA_IMAGE_DIR + "/" + CAMERA_IMAGE_OFFLINE;
  const patrolCount = cameraPatrolCountLabel(camera);
  const wrapClass = inGroup
    ? `camera-tile camera-group-header selectable${offlineClass}`
    : `panel selectable camera-tile${selectedClass}${offlineClass}`;
  const selectTip = selected
    ? "This camera is selected."
    : "Select this camera to view its patrols.";
  return `
      <div class="${wrapClass}" data-select-camera="${escapeHtml(camera.id)}"${tip(selectTip)}>
        <div class="camera-tile-inner">
          <div class="camera-photo"${tip("Product photo for this camera model.")}>
            <img src="${escapeHtml(img)}" alt="${escapeHtml(model)}" onerror="this.classList.add('missing'); this.parentElement.classList.add('missing');">
          </div>
          <div class="camera-meta">
            <div class="panel-header"${tip("Camera name from Protect.")}>${escapeHtml(camera.name)}</div>
            <div class="data-row"><span>Type</span><span class="data-value">${escapeHtml(displayMeta(camera.type))}</span></div>
            <div class="data-row"><span>State</span><span class="data-value"><span class="dot ${online ? "dot-green" : "dot-yellow"}"></span>${escapeHtml(displayMeta(camera.state))}</span></div>
            <div class="data-row"><span>Patrols</span><span class="data-value">${escapeHtml(patrolCount)}</span></div>
            <div class="data-row"><span>Ip</span><span class="data-value">${escapeHtml(cameraIp(camera))}</span></div>
            <div class="data-row"><span>Mac</span><span class="data-value">${escapeHtml(cameraMac(camera))}</span></div>
          </div>
        </div>
      </div>`;
}

function renderCameraAccordion(camera) {
  const patrols = state.patrolsByCamera[camera.id] || [];
  const error = state.error
    ? `<div class="err-banner">Protect error — ${escapeHtml(state.error)}</div>`
    : "";
  const panels = patrols.map((patrol) => renderPatrolPanel(camera, patrol)).join("")
    || `<div class="empty-state">// No patrols on this camera.</div>`;
  return `
    <div class="camera-accordion" data-camera-accordion="${escapeHtml(camera.id)}">
      <div class="panel-header dashed"${tip("Patrols on this camera.")}>Patrols</div>
      ${error}
      ${panels}
    </div>`;
}

function renderCameraGroup(camera, selectedId) {
  const selected = camera.id === selectedId;
  if (!selected) return renderCameraTile(camera, selectedId);
  const online = String(camera.state || "").toUpperCase() === "CONNECTED";
  const offlineClass = online ? "" : " is-offline";
  return `
    <div class="panel camera-group is-open selected${offlineClass}">
      ${renderCameraTile(camera, selectedId, { inGroup: true })}
      ${renderCameraAccordion(camera)}
    </div>`;
}

function renderPatrols(cameraId) {
  const selected = findCamera(cameraId);
  const cameras = state.cameras.map((camera) => renderCameraGroup(camera, cameraId)).join("");

  let hint = "";
  if (!selected) {
    if (state.error) {
      hint = `<div class="err-banner">Protect error — ${escapeHtml(state.error)}</div>`;
    } else {
      hint = `<div class="empty-state">${state.cameras.length ? "// Select a PTZ camera to list its patrols." : "// No PTZ cameras yet. Configure the console or wait for Protect."}</div>`;
    }
  }

  return `
    <h2>Ptz Cameras</h2>
    ${cameras || `<div class="empty-state">// Waiting for cameras.</div>`}
    ${hint}`;
}

function snapshotHash(preset) {
  const snap = String(preset?.snapshot || "");
  const match = snap.match(/[?&]hash=([^&]+)/);
  return match ? decodeURIComponent(match[1]) : "";
}

function presetThumbUrl(cameraId, preset) {
  const slot = encodeURIComponent(preset.slot);
  const hash = snapshotHash(preset);
  const v = preset.thumbMtime || hash || "stock";
  const query = new URLSearchParams({ v: String(v) });
  if (hash && !preset.thumbMtime) query.set("hash", hash);
  return `/api/cameras/${encodeURIComponent(cameraId)}/presets/${slot}/thumb?${query.toString()}`;
}

function renderPresetThumb(cameraId, preset) {
  return `<span class="preset-thumb"${tip("View from this preset.")}><img data-thumb-camera="${escapeHtml(cameraId)}" data-thumb-slot="${escapeHtml(preset.slot)}" src="${escapeHtml(presetThumbUrl(cameraId, preset))}" alt="" onerror="this.classList.add('missing'); this.parentElement.classList.add('is-empty'); this.nextElementSibling.hidden=false;"><span class="preset-thumb-empty" hidden>—</span></span>`;
}

function autoTrackingOf(camera) {
  const tracking = camera?.autoTracking || {};
  return {
    person: Boolean(tracking.person),
    vehicle: Boolean(tracking.vehicle),
    zoomWhileTrack: Boolean(tracking.zoomWhileTrack),
  };
}

function renderTrackToggle(label, key, on, cameraId, tipText) {
  return `
    <div class="form-group">
      <label>${labeled(label, tipText)}</label>
      <button type="button" class="toggle${on ? " is-on" : ""}" data-autotrack="${escapeHtml(key)}" data-camera="${escapeHtml(cameraId)}" aria-pressed="${on ? "true" : "false"}"${tip(tipText)}>${on ? "On" : "Off"}</button>
    </div>`;
}

function fieldVal(value) {
  return value == null || value === "" ? "" : String(value);
}

function renderPresetRow(camera, patrol, preset) {
  const dwell = fieldVal(preset.presetDurationSeconds ?? patrol.presetDurationSeconds ?? 0);
  const speed = fieldVal(preset.presetMovementSpeed ?? patrol.presetMovementSpeed ?? 400);
  const pan = fieldVal(preset.pan ?? "—");
  const tilt = fieldVal(preset.tilt ?? "—");
  const zoom = fieldVal(preset.zoom ?? "—");
  const focus = fieldVal(preset.focus ?? "—");
  return `
    <div class="preset-row" data-preset-row="${escapeHtml(camera.id)}:${escapeHtml(preset.slot)}" data-preset-slot="${escapeHtml(preset.slot)}">
      ${renderPresetThumb(camera.id, preset)}
      <div class="preset-row-body">
        <div class="preset-row-head">
          <span class="preset-label">${escapeHtml(preset.slot)} ${escapeHtml(preset.name)}</span>
        </div>
        <div class="preset-fields">
          <div class="form-group">
            <label>${labeled("Dwell", DWELL_TIP)}</label>
            <input data-field="dwell" type="number" min="${DWELL_MIN}" max="${DWELL_MAX}" step="1" inputmode="numeric" value="${escapeHtml(dwell)}"${tip(DWELL_TIP)}>
            <p class="field-error" data-field-error hidden></p>
          </div>
          <div class="form-group">
            <label>${labeled("Speed", SPEED_TIP)}</label>
            <input data-field="speed" type="number" min="${SPEED_MIN}" max="${SPEED_MAX}" step="1" inputmode="numeric" value="${escapeHtml(speed)}"${tip(SPEED_TIP)}>
            <p class="field-error" data-field-error hidden></p>
          </div>
        </div>
        <div class="preset-ptz">
          <span>P <span class="data-value">${escapeHtml(pan)}</span></span>
          <span>T <span class="data-value">${escapeHtml(tilt)}</span></span>
          <span>Z <span class="data-value">${escapeHtml(zoom)}</span></span>
          <span>Focus <span class="data-value">${escapeHtml(focus)}</span></span>
        </div>
      </div>
    </div>`;
}

function paintAutoTracking(cameraId, tracking) {
  document.querySelectorAll(`[data-autotrack][data-camera="${cameraId}"]`).forEach((btn) => {
    const on = Boolean(tracking[btn.dataset.autotrack]);
    btn.classList.toggle("is-on", on);
    btn.setAttribute("aria-pressed", on ? "true" : "false");
    btn.textContent = on ? "On" : "Off";
  });
}

function rememberSavedPoints(cameraId, id, slot, points) {
  if (!Array.isArray(points)) return;
  const patrols = state.patrolsByCamera[cameraId] || [];
  const patrol = patrols.find((item) => (id && item.id === id) || String(item.slot) === String(slot));
  if (!patrol) return;
  points.forEach((saved) => {
    const preset = (patrol.presets || []).find((item) => String(item.slot) === String(saved.slot));
    if (!preset) return;
    preset.presetDurationSeconds = saved.presetDurationSeconds;
    preset.presetMovementSpeed = saved.presetMovementSpeed;
    preset.dwellSource = "sidecar";
  });
  patrol.pointsSidecar = true;
}

function collectPresetPoints(cameraId, patrolSlot) {
  const panel = document.querySelector(`[data-patrol-edit="${cameraId}:${patrolSlot}"]`);
  const points = [];
  if (!panel) return points;
  panel.querySelectorAll("[data-preset-slot]").forEach((row) => {
    const num = (field) => {
      const raw = row.querySelector(`[data-field="${field}"]`)?.value;
      if (raw === "" || raw == null) return null;
      const value = Number(raw);
      return Number.isFinite(value) ? value : null;
    };
    points.push({
      slot: Number(row.dataset.presetSlot),
      presetDurationSeconds: num("dwell"),
      presetMovementSpeed: num("speed"),
    });
  });
  return points;
}

function isWholeNumberInRange(raw, min, max) {
  const text = String(raw ?? "").trim();
  if (!/^-?\d+$/.test(text)) return false;
  const value = Number(text);
  return Number.isInteger(value) && value >= min && value <= max;
}

function presetFieldError(field, raw) {
  if (field === "dwell") {
    return isWholeNumberInRange(raw, DWELL_MIN, DWELL_MAX) ? "" : DWELL_RANGE_MSG;
  }
  if (field === "speed") {
    return isWholeNumberInRange(raw, SPEED_MIN, SPEED_MAX) ? "" : SPEED_RANGE_MSG;
  }
  return "";
}

function setFieldError(input, message) {
  const group = input.closest(".form-group");
  const err = group?.querySelector("[data-field-error]");
  if (group) group.classList.toggle("is-invalid", Boolean(message));
  if (err) {
    err.textContent = message || "";
    err.hidden = !message;
  }
  input.setAttribute("aria-invalid", message ? "true" : "false");
}

function validatePresetInput(input) {
  const message = presetFieldError(input.dataset.field, input.value);
  setFieldError(input, message);
  return !message;
}

function validatePresetPanel(panel) {
  if (!panel) return false;
  let first = null;
  let firstMsg = "";
  panel.querySelectorAll('[data-field="dwell"], [data-field="speed"]').forEach((input) => {
    const message = presetFieldError(input.dataset.field, input.value);
    setFieldError(input, message);
    if (message && !first) {
      first = input;
      firstMsg = message;
    }
  });
  if (first) {
    first.focus();
    showToast(firstMsg, true);
    return false;
  }
  return true;
}

function onPresetFieldBlur(event) {
  const input = event.target.closest?.("[data-field]");
  if (!input) return;
  if (input.dataset.field !== "dwell" && input.dataset.field !== "speed") return;
  validatePresetInput(input);
}

function renderPatrolPanel(camera, patrol) {
  const open = state.openPatrols.has(patrol.id);
  const presetCount = patrol.presets?.length || 0;
  const busyKey = `${camera.id}:${patrol.slot}`;
  const busy = state.thumbBusy === camera.id || state.thumbBusy === busyKey;
  const tracking = autoTrackingOf(camera);
  const presets = (patrol.presets || []).map((preset) => renderPresetRow(camera, patrol, preset)).join("")
    || `<div class="empty-state">// No presets on this patrol.</div>`;

  return `
    <div class="panel${open ? " open" : ""}" data-patrol="${escapeHtml(patrol.id)}">
      <div class="panel-header selectable" data-toggle-patrol="${escapeHtml(patrol.id)}" aria-expanded="${open}"${tip(open ? "Hide preset editor." : "Open presets and auto-tracking.")}>
        <span${tip("Patrol name.")}>${escapeHtml(patrol.name)}</span>
        <span class="muted"${tip("How many poses are in this patrol.")}>${presetCount} presets</span>
      </div>
      <div class="data-row"><span>Slot</span><span class="data-value">${escapeHtml(patrol.slot)}</span></div>
      <div class="btn-row">
        <button class="btn btn-primary" type="button" data-start-patrol="${escapeHtml(patrol.slot)}" data-camera="${escapeHtml(camera.id)}">Start</button>
        <button class="btn btn-alert" type="button" data-stop-patrol="${escapeHtml(patrol.slot)}" data-camera="${escapeHtml(camera.id)}">Stop</button>
        <button class="btn" type="button" data-toggle-patrol="${escapeHtml(patrol.id)}">${open ? "Hide Presets" : "Edit"}</button>
      </div>
      <div class="panel-body" data-patrol-edit="${escapeHtml(camera.id)}:${escapeHtml(patrol.slot)}">
        <div class="panel-header dashed"${tip("Name for this patrol.")}>Patrol Settings</div>
        <div class="form-group">
          <label>Name</label>
          <input value="${escapeHtml(patrol.name)}" disabled>
        </div>
        <div class="panel-header dashed"${tip("Follow a person or vehicle with this camera.")}>Auto-tracking</div>
        <div class="form-grid-3">
          ${renderTrackToggle("Person", "person", tracking.person, camera.id, "Follow people when they move.")}
          ${renderTrackToggle("Vehicle", "vehicle", tracking.vehicle, camera.id, "Follow vehicles when they move.")}
          ${renderTrackToggle("Zoom-while-track", "zoomWhileTrack", tracking.zoomWhileTrack, camera.id, "Zoom in while following a target.")}
        </div>
        <div class="panel-header dashed"${tip("Poses in this patrol.")}>Presets</div>
        <div class="btn-row preset-refresh-row">
          <button class="btn" type="button" data-refresh-thumbs-patrol="${escapeHtml(patrol.slot)}" data-camera="${escapeHtml(camera.id)}"${busy ? " disabled" : ""}>Refresh snapshot</button>
        </div>
        ${presets}
        <div class="btn-row">
          <button class="btn btn-primary" type="button" data-apply-points="${escapeHtml(patrol.slot)}" data-camera="${escapeHtml(camera.id)}">Apply</button>
        </div>
      </div>
    </div>`;
}

async function onViewClick(event) {
  if (event.target.closest(".info-i")) {
    event.preventDefault();
    event.stopPropagation();
    return;
  }
  if (!event.target.closest("[data-camera-accordion]")) {
    const cameraBtn = event.target.closest("[data-select-camera]");
    if (cameraBtn) {
      const id = cameraBtn.dataset.selectCamera;
      const selectedId = parseHash().cameraId;
      location.hash = selectedId === id ? "#/patrols" : `#/patrols/${id}`;
      return;
    }
  }
  const toggle = event.target.closest("[data-toggle-patrol]");
  if (toggle) {
    const id = toggle.dataset.togglePatrol;
    if (state.openPatrols.has(id)) state.openPatrols.delete(id);
    else state.openPatrols.add(id);
    const route = parseHash();
    document.getElementById("sec-patrols").innerHTML = renderPatrols(route.cameraId);
    return;
  }
  const start = event.target.closest("[data-start-patrol]");
  if (start) {
    try {
      const result = await api(`/api/cameras/${start.dataset.camera}/patrols/${start.dataset.startPatrol}/start`, { method: "POST" });
      if (result && result.pointsError) showToast(`Patrol started, but saved dwell and speed were not written: ${result.pointsError}`, true);
      else if (result && result.pointsWritten) showToast("Patrol started with saved dwell and speed.");
      else showToast("Patrol started");
    } catch (err) {
      showToast(err.message, true);
    }
    return;
  }
  const stop = event.target.closest("[data-stop-patrol]");
  if (stop) {
    try {
      await api(`/api/cameras/${stop.dataset.camera}/patrols/${stop.dataset.stopPatrol}/stop`, { method: "POST" });
      showToast("Patrol stopped");
    } catch (err) {
      showToast(err.message, true);
    }
    return;
  }
  const applyPoints = event.target.closest("[data-apply-points]");
  if (applyPoints) {
    const cameraId = applyPoints.dataset.camera;
    const slot = applyPoints.dataset.applyPoints;
    const panel = document.querySelector(`[data-patrol-edit="${cameraId}:${slot}"]`);
    if (!validatePresetPanel(panel)) return;
    const points = collectPresetPoints(cameraId, slot);
    applyPoints.disabled = true;
    try {
      const result = await api(`/api/cameras/${cameraId}/patrols/${slot}/apply-points`, {
        method: "POST",
        body: JSON.stringify({ points }),
      });
      if (result && result.ok === false) {
        showToast(result.error || "Live apply failed.", true);
        return;
      }
      rememberSavedPoints(cameraId, null, slot, result?.points);
      showToast(result?.mode === "mock" ? "Demo mode: nothing sent to a camera." : "Patrol settings applied.");
      document.getElementById("sec-patrols").innerHTML = renderPatrols(cameraId);
    } catch (err) {
      showToast(err.message, true);
    } finally {
      applyPoints.disabled = false;
    }
    return;
  }
  const trackBtn = event.target.closest("[data-autotrack]");
  if (trackBtn) {
    const cameraId = trackBtn.dataset.camera;
    const camera = findCamera(cameraId);
    if (!camera) return;
    const next = autoTrackingOf(camera);
    const key = trackBtn.dataset.autotrack;
    next[key] = !next[key];
    paintAutoTracking(cameraId, next);
    try {
      const result = await api(`/api/cameras/${cameraId}/settings`, {
        method: "PATCH",
        body: JSON.stringify({ autoTracking: next }),
      });
      camera.autoTracking = result.autoTracking || next;
      paintAutoTracking(cameraId, camera.autoTracking);
      showToast("Auto-tracking saved");
    } catch (err) {
      paintAutoTracking(cameraId, autoTrackingOf(camera));
      showToast(err.message, true);
    }
    return;
  }
  const refreshPatrol = event.target.closest("[data-refresh-thumbs-patrol]");
  if (refreshPatrol) {
    await refreshPovIcons(refreshPatrol.dataset.camera, refreshPatrol.dataset.refreshThumbsPatrol);
  }
}

function povRefreshWarning(cameraId) {
  const live = state.protectConfigured;
  const keyNote = live && !state.apiKeySet
    ? " Live capture needs a Protect Integration API key on the Config page."
    : "";
  return `Refresh snapshot moves this camera to each preset (~6 seconds each) and captures a live snapshot.${keyNote} Continue?`;
}

function thumbRefreshSummary(data) {
  const slots = data?.slots || [];
  const okCount = slots.filter((item) => item.ok).length;
  const skipped = slots.filter((item) => item.skipped).length;
  const failed = slots.filter((item) => !item.ok && !item.skipped).length;
  let message = `Snapshots: ${okCount} saved`;
  if (skipped) message += `, ${skipped} skipped`;
  if (failed) message += `, ${failed} failed`;
  return message;
}

function markCapturingThumb(cameraId, slot) {
  document.querySelectorAll(".preset-row.is-capturing").forEach((row) => row.classList.remove("is-capturing"));
  const key = `${cameraId}:${slot}`;
  document.querySelectorAll(`[data-preset-row="${key}"]`).forEach((row) => row.classList.add("is-capturing"));
}

function paintPresetThumb(cameraId, slot, thumbMtime) {
  const mtime = thumbMtime || Math.floor(Date.now() / 1000);
  (state.patrolsByCamera[cameraId] || []).forEach((patrol) => {
    (patrol.presets || []).forEach((preset) => {
      if (String(preset.slot) !== String(slot)) return;
      preset.hasCache = true;
      preset.hasThumb = true;
      preset.thumbSource = "cache";
      preset.thumbMtime = mtime;
    });
  });
  const url = presetThumbUrl(cameraId, { slot, thumbMtime: mtime });
  document.querySelectorAll(`img[data-thumb-camera="${cameraId}"][data-thumb-slot="${slot}"]`).forEach((img) => {
    img.classList.remove("missing");
    if (img.parentElement) img.parentElement.classList.remove("is-empty");
    const empty = img.nextElementSibling;
    if (empty) empty.hidden = true;
    img.src = url;
  });
}

async function readNdjson(response, onEvent) {
  if (!response.body || !response.body.getReader) {
    const text = await response.text();
    text.split("\n").forEach((line) => {
      const trimmed = line.trim();
      if (trimmed) onEvent(JSON.parse(trimmed));
    });
    return;
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let nl = buf.indexOf("\n");
    while (nl >= 0) {
      const line = buf.slice(0, nl).trim();
      buf = buf.slice(nl + 1);
      if (line) onEvent(JSON.parse(line));
      nl = buf.indexOf("\n");
    }
  }
  const tail = buf.trim();
  if (tail) onEvent(JSON.parse(tail));
}

async function refreshPovIcons(cameraId, patrolSlot) {
  if (state.protectConfigured && !state.apiKeySet) {
    showToast("Protect Integration API key is required to refresh snapshots. Save it on the Config page.", true);
    return;
  }
  if (!window.confirm(povRefreshWarning(cameraId))) return;
  const path = patrolSlot != null && patrolSlot !== ""
    ? `/api/cameras/${encodeURIComponent(cameraId)}/patrols/${encodeURIComponent(patrolSlot)}/thumbs/refresh`
    : `/api/cameras/${encodeURIComponent(cameraId)}/thumbs/refresh`;
  state.thumbBusy = patrolSlot != null && patrolSlot !== "" ? `${cameraId}:${patrolSlot}` : cameraId;
  document.getElementById("sec-patrols").innerHTML = renderPatrols(cameraId);
  const slotEvents = [];
  let doneEvent = null;
  try {
    const response = await fetch(path, {
      method: "POST",
      credentials: "same-origin",
      headers: { Accept: "application/x-ndjson" },
    });
    if (response.status === 401) {
      location.replace(LOGIN_URL);
      throw new Error("Not signed in");
    }
    const ctype = response.headers.get("content-type") || "";
    if (!response.ok) {
      let message = `Request failed (${response.status})`;
      if (ctype.includes("application/json")) {
        const body = await response.json().catch(() => ({}));
        message = body.error || body.detail || message;
      }
      throw new Error(typeof message === "string" ? message : JSON.stringify(message));
    }
    await readNdjson(response, (event) => {
      if (event.event === "error") {
        throw new Error(event.error || "Refresh snapshot failed");
      }
      if (event.event === "capturing") {
        markCapturingThumb(cameraId, event.slot);
        return;
      }
      if (event.event === "slot") {
        slotEvents.push(event);
        if (event.ok) paintPresetThumb(cameraId, event.slot, event.thumbMtime);
        return;
      }
      if (event.event === "done") doneEvent = event;
    });
    showToast(thumbRefreshSummary(doneEvent || { slots: slotEvents }), doneEvent && doneEvent.ok === false);
  } catch (err) {
    showToast(err.message, true);
  } finally {
    document.querySelectorAll(".preset-row.is-capturing").forEach((row) => row.classList.remove("is-capturing"));
    state.thumbBusy = "";
    const route = parseHash();
    if (route.page === "patrols" && route.cameraId === cameraId) {
      document.getElementById("sec-patrols").innerHTML = renderPatrols(cameraId);
    }
  }
}

function renderConfigSkeleton() {
  return `
    <h2>Site Configuration</h2>
    <p class="note">Loading…</p>`;
}

async function loadConfig() {
  let config;
  try {
    config = await api("/api/config");
  } catch (err) {
    document.getElementById("sec-config").innerHTML = `<h2>Site Configuration</h2><div class="err-banner">${escapeHtml(err.message)}</div>`;
    return;
  }
  state.deviceName = config.deviceName || "YesTechie Labs";
  setChrome();
  document.getElementById("sec-config").innerHTML = `
    <h2>Site Configuration</h2>
    <p class="note">Saved to config.local.yaml on this appliance. Env vars override the file on restart. Live mode needs host + UniFi username/password.</p>
    <div class="panel">
      <div class="panel-header">Console</div>
      <div class="form-group"><label for="host">Host / Ip</label><input id="host" class="case-sensitive" value="${escapeHtml(config.host)}" placeholder="10.0.0.1"></div>
      <div class="form-row">
        <div class="form-group"><label for="port">Port</label><input id="port" value="${escapeHtml(config.port)}"></div>
        <div class="form-group"><label for="site">Site</label><input id="site" class="case-sensitive" value="${escapeHtml(config.site)}"></div>
      </div>
    </div>
    <div class="panel">
      <div class="panel-header">Unifi Os Login</div>
      <div class="form-group"><label for="unifi-user">Username</label><input id="unifi-user" class="case-sensitive" value="${escapeHtml(config.username)}" autocomplete="off"></div>
      <div class="form-group"><label for="unifi-pass">Password</label>
        <div class="input-wrap">
          <input id="unifi-pass" class="case-sensitive" type="password" placeholder="${config.passwordSet ? "••••••••" : "UNIFI_PASS"}" autocomplete="off">
          <button class="reveal" type="button" id="reveal-pass">Show</button>
        </div>
      </div>
      <div class="form-group"><label for="verify-tls">Tls</label>
        <select id="verify-tls">
          <option value="1"${config.verifyTls ? " selected" : ""}>Verify Certificate</option>
          <option value="0"${config.verifyTls ? "" : " selected"}>Allow Self-Signed (Lab)</option>
        </select>
      </div>
      <div class="form-group"><label for="api-key">Integration Api Key</label><input id="api-key" class="case-sensitive" type="password" placeholder="${escapeHtml(config.apiKeyHint || "required to refresh snapshots")}" autocomplete="off">
        <p class="note">Required to Refresh snapshot (Integration HQ after goto). Stock POV icons use the private preset snapshot URL and do not need this key. Also used as start/stop fallback.</p>
      </div>
    </div>
    <div class="panel">
      <div class="panel-header">Appliance</div>
      <div class="form-group"><label for="device-name">Device Name</label><input id="device-name" value="${escapeHtml(config.deviceName)}"></div>
      <div class="form-group"><label for="timezone">Timezone</label><input id="timezone" class="case-sensitive" value="${escapeHtml(config.timezone)}"></div>
    </div>
    <div class="btn-row">
      <button class="btn btn-primary" type="button" id="save-config">Save Configuration</button>
    </div>`;

  document.getElementById("reveal-pass")?.addEventListener("click", (event) => {
    const inp = document.getElementById("unifi-pass");
    inp.type = inp.type === "password" ? "text" : "password";
    event.currentTarget.textContent = inp.type === "password" ? "Show" : "Hide";
  });
  document.getElementById("save-config")?.addEventListener("click", async () => {
    try {
      const next = await api("/api/config", {
        method: "PUT",
        body: JSON.stringify({
          host: document.getElementById("host").value,
          port: Number(document.getElementById("port").value || 443),
          site: document.getElementById("site").value,
          apiKey: document.getElementById("api-key").value,
          username: document.getElementById("unifi-user").value,
          password: document.getElementById("unifi-pass").value,
          verifyTls: document.getElementById("verify-tls").value === "1",
          deviceName: document.getElementById("device-name").value,
          timezone: document.getElementById("timezone").value,
        }),
      });
      state.protectConfigured = Boolean(next.configured);
      state.apiKeySet = Boolean(next.apiKeySet);
      state.deviceName = next.deviceName || "YesTechie Labs";
      setChrome();
      showToast("Configuration saved");
    } catch (err) {
      showToast(err.message, true);
    }
  });
}

function renderLogsSkeleton() {
  return `
    <h2>System Logs</h2>
    <p class="note">Loading…</p>`;
}

async function loadLogs() {
  let lines = [];
  try {
    const data = await api("/api/logs");
    lines = data.lines || [];
  } catch (err) {
    document.getElementById("sec-logs").innerHTML = `<h2>System Logs</h2><div class="err-banner">${escapeHtml(err.message)}</div>`;
    return;
  }
  const levelClass = { OK: "terminal-ok", INF: "", WRN: "terminal-warn", ERR: "terminal-err" };
  document.getElementById("sec-logs").innerHTML = `
    <h2>System Logs</h2>
    <p class="note">Backend and action stream. Cleared on process restart.</p>
    <div class="toolbar">
      <button class="btn btn-primary btn-sm" type="button" id="refresh-logs">Refresh Now</button>
      <button class="btn btn-sm" type="button" id="clear-logs">Clear View</button>
    </div>
    <div class="panel">
      <div class="panel-header">Console <span class="muted">${lines.length}</span></div>
      <div class="terminal-window" id="logBox">
        ${lines.map((line) => `
          <div class="terminal-line ${levelClass[line.level] || ""}"><span class="terminal-ts">[${escapeHtml(line.time)}]</span>${escapeHtml(line.level)} ${escapeHtml(line.message)}</div>
        `).join("") || `<div class="terminal-line">No lines yet.</div>`}
      </div>
    </div>`;
  document.getElementById("refresh-logs")?.addEventListener("click", () => loadLogs());
  document.getElementById("clear-logs")?.addEventListener("click", async () => {
    await api("/api/logs/clear", { method: "POST" });
    await loadLogs();
  });
  const box = document.getElementById("logBox");
  if (box) box.scrollTop = box.scrollHeight;
}

document.addEventListener("DOMContentLoaded", () => {
  const page = document.body.dataset.page;
  if (page === "login") initLogin();
  if (page === "app") initApp();
});
