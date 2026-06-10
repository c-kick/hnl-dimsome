const DEFAULT_CONFIG = {
  global: {
    dim_schedule: { type: "civil_sun", event: "civil_dusk" },
    brighten_schedule: { type: "civil_sun", event: "civil_dawn" },
    ramp_duration: "01:00:00",
    override_resume_mode: "manual_only",
    override_grace_period: "00:15:00",
    split_turn_on_calls: false,
    apply_on_recovered_on: true,
    native_user_ids: [],
  },
  lights: [],
};

const SCHEDULE_TYPES = [
  ["fixed_time", "Fixed Time"],
  ["civil_sun", "Civil Sun"],
];

const SUN_EVENTS = [
  ["civil_dawn", "Civil Dawn"],
  ["civil_dusk", "Civil Dusk"],
];

const RESUME_MODES = [
  ["manual_only", "Manual Only"],
  ["after_grace_period", "After Grace Period"],
];

const COLOR_MODE = "color_temp_kelvin";

// MDI icon SVG paths
const MDI_REFRESH = "M17.65,6.35C16.2,4.9 14.21,4 12,4A8,8 0 0,0 4,12A8,8 0 0,0 12,20C15.73,20 18.84,17.45 19.73,14H17.65C16.83,16.33 14.61,18 12,18A6,6 0 0,1 6,12A6,6 0 0,1 12,6C13.66,6 15.14,6.69 16.22,7.78L13,11H20V4L17.65,6.35Z";
const MDI_CONTENT_SAVE = "M15,9H5V5H15M12,19A3,3 0 0,1 9,16A3,3 0 0,1 12,13A3,3 0 0,1 15,16A3,3 0 0,1 12,19M17,3H5C3.89,3 3,3.9 3,5V19A2,2 0 0,0 5,21H19A2,2 0 0,0 21,19V7L17,3Z";
const MDI_PLAY_CIRCLE = "M12,2A10,10 0 0,0 2,12A10,10 0 0,0 12,22A10,10 0 0,0 22,12A10,10 0 0,0 12,2M10,16.5V7.5L16,12L10,16.5Z";
const MDI_DELETE = "M19,4H15.5L14.5,3H9.5L8.5,4H5V6H19M6,19A2,2 0 0,0 8,21H16A2,2 0 0,0 18,19V7H6V19Z";
const MDI_PLUS = "M19,13H13V19H11V13H5V11H11V5H13V11H19V13Z";

const ADD_DRAFT_DEFAULT = Object.freeze({
  entity_id: "",
  min_brightness_pct: 10,
  max_brightness_pct: 80,
});

const STATUS_LABEL = {
  ramping: "Ramping",
  tracking: "Tracking",
  manual_override: "Manual override",
  stood_down: "Standing down",
  disabled: "Disabled",
};

const clone = (value) => JSON.parse(JSON.stringify(value));

const escapeHtml = (value) => String(value ?? "")
  .replaceAll("&", "&amp;")
  .replaceAll("<", "&lt;")
  .replaceAll(">", "&gt;")
  .replaceAll('"', "&quot;");

const durationToMinutes = (value, fallback = 60) => {
  if (!value) return fallback;
  const parts = String(value).split(":").map((part) => Number(part));
  if (parts.length === 3) return Math.max(1, Math.round(parts[0] * 60 + parts[1] + parts[2] / 60));
  if (parts.length === 2) return Math.max(1, Math.round(parts[0] * 60 + parts[1]));
  return fallback;
};

const minutesToDuration = (value) => {
  const minutes = Math.max(1, Number(value) || 1);
  const hours = Math.floor(minutes / 60);
  const remainder = minutes % 60;
  return `${String(hours).padStart(2, "0")}:${String(remainder).padStart(2, "0")}:00`;
};

const durationToSeconds = (value, fallback = 0.5) => {
  if (value === undefined || value === null || value === "") return fallback;
  if (typeof value === "number") return value;
  const parts = String(value).split(":").map((part) => Number(part));
  if (parts.length === 3) return Math.max(0, parts[0] * 3600 + parts[1] * 60 + parts[2]);
  if (parts.length === 2) return Math.max(0, parts[0] * 3600 + parts[1] * 60);
  return Math.max(0, Number(value) || fallback);
};

const getPath = (object, path) => path.split(".").reduce((value, key) => value?.[key], object);

const hasOwn = (object, key) => Object.prototype.hasOwnProperty.call(object, key);

const hasTimingOverride = (light) => (
  hasOwn(light, "dim_schedule")
  || hasOwn(light, "brighten_schedule")
  || hasOwn(light, "ramp_duration")
  || hasOwn(light, "override_resume_mode")
  || hasOwn(light, "override_grace_period")
);

const enableTimingOverride = (light, global) => {
  light.dim_schedule = clone(global.dim_schedule);
  light.brighten_schedule = clone(global.brighten_schedule);
  light.ramp_duration = global.ramp_duration;
  light.override_resume_mode = global.override_resume_mode;
  light.override_grace_period = global.override_grace_period;
};

const disableTimingOverride = (light) => {
  delete light.dim_schedule;
  delete light.brighten_schedule;
  delete light.ramp_duration;
  delete light.override_resume_mode;
  delete light.override_grace_period;
};

const setPath = (object, path, value) => {
  const parts = path.split(".");
  let current = object;
  for (const part of parts.slice(0, -1)) {
    if (!current[part]) current[part] = {};
    current = current[part];
  }
  current[parts.at(-1)] = value;
};

const selectHtml = ({ path, value, options, renderOnChange }) => {
  const opts = options.map(([v, l]) => (
    `<option value="${escapeHtml(v)}"${v === value ? " selected" : ""}>${escapeHtml(l)}</option>`
  )).join("");
  return `
    <span class="native-select-wrap">
      <select
        class="native-select"
        data-path="${escapeHtml(path)}"
        ${renderOnChange ? 'data-render-on-change="true"' : ""}
      >${opts}</select>
    </span>
  `;
};

const percentBrightness = (value) => {
  if (!value) return "";
  return `${Math.round((Number(value) / 255) * 100)}%`;
};

const formatEntityName = (state, entityId) => (
  state?.attributes?.friendly_name || entityId || "New Light"
);

const clampPct = (value, fallback) => {
  const number = Number(value);
  if (!Number.isFinite(number)) return fallback;
  return Math.min(100, Math.max(1, Math.round(number)));
};

const formatTime = (date) =>
  `${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}`;

const formatScheduleSummary = (schedule) => {
  if (!schedule) return "";
  if (schedule.type === "fixed_time") return schedule.at || "fixed time";
  if (schedule.event === "civil_dawn") return "civil dawn";
  if (schedule.event === "civil_dusk") return "civil dusk";
  return schedule.event || schedule.type || "";
};

const defaultFixedTimeForPath = (path) => (
  path.includes("dim_schedule") ? "20:00:00" : "06:00:00"
);

const formatRelative = (target, now) => {
  let secs = Math.round((target - now) / 1000);
  if (secs < 0) secs = 0;
  if (secs < 60) return "in <1 min";
  const mins = Math.round(secs / 60);
  if (mins < 60) return `in ${mins} min`;
  const h = Math.floor(mins / 60);
  const m = mins % 60;
  return m ? `in ${h}h ${m}m` : `in ${h}h`;
};

// ── Solar elevation (NOAA approximate) ─────────────────────────────────
// Returns degrees above horizon for given lat/lon at JS Date.
const solarElevation = (lat, lon, date) => {
  const rad = Math.PI / 180;
  const startOfYear = new Date(date.getFullYear(), 0, 0);
  const n = Math.floor((date - startOfYear) / 86400000);
  const decl = 23.45 * rad * Math.sin(2 * Math.PI * (284 + n) / 365);
  const B = 2 * Math.PI * (n - 81) / 365;
  const eot = 9.87 * Math.sin(2 * B) - 7.53 * Math.cos(B) - 1.5 * Math.sin(B);
  const utcMinutes = date.getUTCHours() * 60 + date.getUTCMinutes() + date.getUTCSeconds() / 60;
  const solarMinutes = utcMinutes + 4 * lon + eot;
  const hourAngle = (solarMinutes / 60 - 12) * 15 * rad;
  const latRad = lat * rad;
  const sinE = Math.sin(latRad) * Math.sin(decl) +
               Math.cos(latRad) * Math.cos(decl) * Math.cos(hourAngle);
  return Math.asin(Math.max(-1, Math.min(1, sinE))) / rad;
};

// First time in [start, end] where sun elevation crosses `threshold` in given direction.
const findElevationCrossing = (lat, lon, start, end, threshold, direction) => {
  const stepMs = 60 * 1000;
  let prev = solarElevation(lat, lon, start);
  for (let t = start.getTime() + stepMs; t <= end.getTime(); t += stepMs) {
    const cur = solarElevation(lat, lon, new Date(t));
    const descending = prev > threshold && cur <= threshold;
    const ascending = prev < threshold && cur >= threshold;
    if ((direction === "descending" && descending) || (direction === "ascending" && ascending)) {
      return new Date(t);
    }
    prev = cur;
  }
  return null;
};

const timeOfDayToday = (timeStr, baseDate) => {
  const parts = String(timeStr || "00:00").split(":").map(Number);
  const d = new Date(baseDate);
  d.setHours(parts[0] || 0, parts[1] || 0, parts[2] || 0, 0);
  return d;
};

class DimsomePanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._hass = null;
    this._panel = null;
    this._narrow = false;
    this._loaded = false;
    this._saving = false;
    this._configured = false;
    this._config = clone(DEFAULT_CONFIG);
    this._lightStates = {};
    this._runtime = {};
    this._error = "";
    this._message = "";
    this._addDialogOpen = false;
    this._addDraft = { ...ADD_DRAFT_DEFAULT };
    this._addError = "";
    this._pendingScrollToTop = false;
    this._tickHandle = null;
  }

  set hass(hass) {
    const hadHass = Boolean(this._hass);
    this._hass = hass;
    if (!this._loaded) this._loadConfig();
    if (!hadHass && this.shadowRoot?.hasChildNodes()) this._hydrateNativeComponents();
    const menuBtn = this.shadowRoot?.querySelector("ha-menu-button");
    if (menuBtn) menuBtn.hass = hass;
  }

  get hass() {
    return this._hass;
  }

  set panel(panel) {
    this._panel = panel;
  }

  set narrow(value) {
    const narrow = Boolean(value);
    if (narrow === this._narrow) return;
    this._narrow = narrow;
    const menuBtn = this.shadowRoot?.querySelector("ha-menu-button");
    if (menuBtn) menuBtn.narrow = narrow;
  }

  get narrow() {
    return this._narrow;
  }

  set route(value) {
    this._route = value;
  }

  connectedCallback() {
    if (!this._clickBound) {
      this._clickBound = true;
      this.shadowRoot.addEventListener("click", (event) => this._handleClick(event));
    }
    this._render();
    this._tickHandle = window.setInterval(() => this._refreshLiveBits(), 60_000);
  }

  disconnectedCallback() {
    if (this._tickHandle) {
      window.clearInterval(this._tickHandle);
      this._tickHandle = null;
    }
  }

  async _loadConfig() {
    if (!this._hass) return;
    this._loaded = true;
    try {
      const result = await this._hass.callWS({ type: "dimsome/config" });
      this._configured = result.configured;
      this._config = this._normalizeConfig(result.config || DEFAULT_CONFIG);
      this._lightStates = result.light_states || {};
      this._runtime = result.runtime || {};
      this._error = "";
    } catch (error) {
      this._error = error.message || String(error);
    }
    this._render();
  }

  _normalizeConfig(config) {
    return {
      global: { ...clone(DEFAULT_CONFIG.global), ...(config.global || {}) },
      lights: Array.isArray(config.lights) ? config.lights.map((light) => ({ ...light })) : [],
    };
  }

  async _saveConfig() {
    this._saving = true;
    this._error = "";
    this._message = "Saving…";
    this._render();
    try {
      await this._hass.callWS({ type: "dimsome/save_config", config: this._config });
      this._message = "Saved. Dimsome reloaded.";
      this._loaded = false;
      await this._loadConfig();
    } catch (error) {
      this._error = error.message || String(error);
      this._message = "";
    }
    this._saving = false;
    this._render();
  }

  async _resume(entityId = null) {
    if (!this._hass) return;
    const data = entityId ? { entity_id: [entityId] } : {};
    try {
      await this._hass.callService("dimsome", "resume", data);
      this._error = "";
      this._message = entityId ? `Resumed ${entityId}.` : "Resumed all Dimsome lights.";
      this._loaded = false;
      await this._loadConfig();
      return;
    } catch (error) {
      this._error = error.message || String(error);
      this._message = "";
    }
    this._render();
  }

  _refreshLiveBits() {
    if (!this._loaded || !this._configured) return;
    const hero = this.shadowRoot?.querySelector(".hero-card");
    if (!hero) return;
    const wrapper = document.createElement("div");
    wrapper.innerHTML = this._renderHero();
    const fresh = wrapper.firstElementChild;
    if (fresh) {
      hero.replaceWith(fresh);
      // Hydrate only the replaced subtree: re-hydrating the whole shadow root
      // would reset every input to its render-time value, visually undoing
      // unsaved edits that are still pending in this._config.
      this._hydrateNativeComponents(fresh);
    }
  }

  _handleClick(event) {
    if (!(event.target instanceof Element)) return;
    const button = event.target.closest("[data-action]");
    if (!button) return;
    const action = button.dataset.action;
    const index = Number(button.dataset.index);
    if (action === "save") this._saveConfig();
    if (action === "reload") this._loadConfig();
    if (action === "open-add-dialog") this._openAddDialog();
    if (action === "cancel-add") this._closeAddDialog();
    if (action === "confirm-add") this._confirmAdd();
    if (action === "remove-light") this._removeLight(index);
    if (action === "resume") this._resume(button.dataset.entityId || null);
  }

  _handleControlInput(control, event) {
    if (!control?.dataset) return;
    if (control.dataset.draftPath) {
      let value = this._controlValue(control, event);
      if (control.dataset.number === "int") value = Number(value);
      this._addDraft[control.dataset.draftPath] = value;
      this._addError = "";
      return;
    }
    if (control.dataset.path) {
      let value = this._controlValue(control, event);
      if (control.dataset.number === "int") value = Number(value);
      if (control.dataset.number === "float") value = Number(value);
      if (control.dataset.duration === "minutes") value = minutesToDuration(value);
      if (control.dataset.list === "csv") {
        value = String(value).split(",").map((part) => part.trim()).filter(Boolean);
      }
      setPath(this._config, control.dataset.path, value);
      this._normalizeScheduleForPath(control.dataset.path);
      if (control.dataset.renderOnChange || control.dataset.path.endsWith(".type")) this._render();
      return;
    }
    if ("colorToggle" in control.dataset) {
      const index = Number(control.dataset.colorToggle);
      const light = this._config.lights[index];
      if (control.checked) {
        light.min_color = { mode: COLOR_MODE, value: 2200 };
        light.max_color = { mode: COLOR_MODE, value: 4000 };
      } else {
        delete light.min_color;
        delete light.max_color;
      }
      this._render();
      return;
    }
    if ("overrideToggle" in control.dataset) {
      const index = Number(control.dataset.overrideToggle);
      const light = this._config.lights[index];
      if (control.checked) {
        enableTimingOverride(light, this._config.global);
      } else {
        disableTimingOverride(light);
      }
      this._render();
    }
  }

  _controlValue(input, event) {
    if (input.localName === "ha-switch" || input.type === "checkbox") return input.checked;
    if (event.detail?.value !== undefined) return event.detail.value ?? "";
    return input.value ?? "";
  }

  _normalizeScheduleForPath(path) {
    if (!path.endsWith(".type")) return;
    const schedulePath = path.slice(0, -5);
    const schedule = getPath(this._config, schedulePath);
    if (schedule.type === "fixed_time") {
      schedule.at ||= defaultFixedTimeForPath(schedulePath);
      delete schedule.event;
    } else {
      schedule.event ||= "civil_dusk";
      delete schedule.at;
    }
  }

  _openAddDialog() {
    this._addDraft = { ...ADD_DRAFT_DEFAULT };
    this._addError = "";
    this._addDialogOpen = true;
    this._render();
  }

  _closeAddDialog() {
    if (!this._addDialogOpen) return;
    this._addDialogOpen = false;
    this._addError = "";
    this._render();
  }

  _confirmAdd() {
    const entityId = (this._addDraft.entity_id || "").trim();
    if (!entityId) {
      this._addError = "Pick a light entity to continue.";
      this._render();
      return;
    }
    if (this._config.lights.some((l) => l.entity_id === entityId)) {
      this._addError = `${entityId} is already configured.`;
      this._render();
      return;
    }
    const min = clampPct(this._addDraft.min_brightness_pct, 10);
    const max = clampPct(this._addDraft.max_brightness_pct, 80);
    const newLight = {
      entity_id: entityId,
      enabled: true,
      min_brightness_pct: min,
      max_brightness_pct: Math.max(min, max),
      split_turn_on_calls: this._config.global.split_turn_on_calls || false,
      apply_on_recovered_on: this._config.global.apply_on_recovered_on ?? true,
    };
    this._config.lights.unshift(newLight);
    this._addDialogOpen = false;
    this._addError = "";
    this._pendingScrollToTop = true;
    this._render();
  }

  _removeLight(index) {
    const light = this._config.lights[index];
    const name = light?.entity_id || "this light";
    if (!window.confirm(`Remove ${name} from Dimsome?`)) return;
    this._config.lights.splice(index, 1);
    this._render();
  }

  _hydrateNativeComponents(root = this.shadowRoot) {
    if (!root) return;

    // Menu button — native sidebar toggle for narrow screens
    const menuBtn = root.querySelector("ha-menu-button");
    if (menuBtn) {
      menuBtn.hass = this._hass;
      menuBtn.narrow = this._narrow;
    }

    // Icon button SVG paths
    root.querySelectorAll("ha-icon-button[data-action]").forEach((btn) => {
      const action = btn.dataset.action;
      if (action === "reload") btn.path = MDI_REFRESH;
      if (action === "resume") btn.path = MDI_PLAY_CIRCLE;
      if (action === "save") btn.path = MDI_CONTENT_SAVE;
      if (action === "remove-light") btn.path = MDI_DELETE;
      if (action === "open-add-dialog") btn.path = MDI_PLUS;
    });

    // Entity pickers
    root.querySelectorAll("ha-entity-picker").forEach((picker) => {
      picker.hass = this._hass;
      picker.includeDomains = ["light"];
      picker.value = picker.dataset.value || "";
    });

    // State icons
    root.querySelectorAll("ha-state-icon").forEach((icon) => {
      icon.hass = this._hass;
      icon.stateObj = this._hass?.states?.[icon.dataset.entityId];
    });

    // Time selectors
    root.querySelectorAll("ha-selector[data-selector='time']").forEach((selector) => {
      selector.hass = this._hass;
      selector.selector = { time: {} };
      selector.value = selector.dataset.value || "";
    });

    // Native <select> and <input> elements carry their values as plain HTML
    // attributes, so they need no hydration.

    // Switches
    root.querySelectorAll("ha-switch").forEach((control) => {
      control.checked = control.hasAttribute("checked");
    });

    // Dialog lifecycle — handles ESC / scrim / programmatic close.
    const dialog = root.querySelector("ha-dialog");
    if (dialog && !dialog._dimsomeBound) {
      dialog._dimsomeBound = true;
      dialog.addEventListener("closed", () => {
        if (this._addDialogOpen) this._closeAddDialog();
      });
    }

    // Bind all data controls
    root
      .querySelectorAll("[data-path], [data-draft-path], [data-color-toggle], [data-override-toggle]")
      .forEach((control) => this._bindControl(control));

    // After prepending a new light, smooth-scroll it into view.
    if (this._pendingScrollToTop) {
      this._pendingScrollToTop = false;
      requestAnimationFrame(() => {
        const first = this.shadowRoot.querySelector(".lights-list > ha-card");
        if (first?.scrollIntoView) {
          first.scrollIntoView({ behavior: "smooth", block: "start" });
        }
      });
    }
  }

  _bindControl(control) {
    if (control._dimsomeBound) return;
    control._dimsomeBound = true;
    const handler = (event) => this._handleControlInput(control, event);
    if (control.localName === "ha-switch") {
      control.addEventListener("change", handler);
      return;
    }
    if (control.localName === "ha-entity-picker" || control.localName === "ha-selector") {
      control.addEventListener("value-changed", handler);
      return;
    }
    control.addEventListener("input", handler);
    control.addEventListener("change", handler);
  }

  _statusText(light) {
    const state = this._lightStates[light.entity_id];
    const attrs = state?.attributes || {};
    const parts = [state?.state || "Not Found"];
    const brightness = percentBrightness(attrs.brightness);
    if (brightness) parts.push(`Brightness ${brightness}`);
    if (attrs.color_temp_kelvin) parts.push(`${attrs.color_temp_kelvin} K`);
    return parts.join(" · ");
  }

  _renderStatusChip(entityId) {
    const rt = this._runtime?.[entityId];
    const status = rt?.status || "tracking";
    const label = STATUS_LABEL[status] || status;
    return `<span class="status-chip status-${escapeHtml(status)}">
      <span class="chip-dot"></span>${escapeHtml(label)}
    </span>`;
  }

  // Compute today's dim/brighten windows from global schedule + ramp duration.
  _scheduleWindows(now) {
    const lat = this._hass?.config?.latitude ?? 52.0;
    const lon = this._hass?.config?.longitude ?? 5.0;
    const start = new Date(now);
    start.setHours(0, 0, 0, 0);
    const end = new Date(now);
    end.setHours(23, 59, 59, 999);

    const dimSched = this._config.global.dim_schedule || {};
    const briSched = this._config.global.brighten_schedule || {};
    const rampMin = durationToMinutes(this._config.global.ramp_duration || "01:00:00", 60);

    const dimStart = dimSched.type === "fixed_time"
      ? timeOfDayToday(dimSched.at, now)
      : findElevationCrossing(lat, lon, start, end, -6, "descending");
    const briStart = briSched.type === "fixed_time"
      ? timeOfDayToday(briSched.at, now)
      : findElevationCrossing(lat, lon, start, end, -6, "ascending");

    return {
      lat, lon, start, end, rampMin,
      dimStart,
      dimEnd: dimStart ? new Date(dimStart.getTime() + rampMin * 60_000) : null,
      briStart,
      briEnd: briStart ? new Date(briStart.getTime() + rampMin * 60_000) : null,
    };
  }

  _renderSunCurve(now, windows) {
    const W = 1000;
    const H = 220;
    const padX = 32;
    const padTop = 16;
    const padBottom = 36;
    const innerW = W - padX * 2;
    const innerH = H - padTop - padBottom;
    const { lat, lon, start, end } = windows;

    const samples = [];
    let maxElev = 30;
    let minElev = -30;
    for (let t = start.getTime(); t <= end.getTime(); t += 5 * 60 * 1000) {
      const e = solarElevation(lat, lon, new Date(t));
      samples.push({ t, e });
      if (e > maxElev) maxElev = e;
      if (e < minElev) minElev = e;
    }
    const range = Math.max(Math.abs(maxElev), Math.abs(minElev)) + 10;
    const elevToY = (e) => padTop + (1 - (e + range) / (2 * range)) * innerH;
    const tToX = (t) => padX + ((t - start.getTime()) / (end.getTime() - start.getTime())) * innerW;
    const horizonY = elevToY(0);
    const twilightY = elevToY(-6);

    const path = samples
      .map((s, i) => `${i === 0 ? "M" : "L"}${tToX(s.t).toFixed(1)} ${elevToY(s.e).toFixed(1)}`)
      .join(" ");
    const dayPath = `M${padX} ${horizonY} ` +
      samples.map((s) => `L${tToX(s.t).toFixed(1)} ${elevToY(Math.max(s.e, 0)).toFixed(1)}`).join(" ") +
      ` L${padX + innerW} ${horizonY} Z`;

    const rampRect = (a, b, cls) => {
      if (!a || !b) return "";
      const x1 = Math.max(padX, tToX(a.getTime()));
      const x2 = Math.min(padX + innerW, tToX(b.getTime()));
      if (x2 <= x1) return "";
      return `<rect class="${cls}" x="${x1.toFixed(1)}" y="${padTop}" width="${(x2 - x1).toFixed(1)}" height="${innerH}"/>`;
    };

    const nowX = tToX(now.getTime());

    const hourTicks = [0, 6, 12, 18, 24].map((h) => {
      const t = new Date(start);
      t.setHours(h);
      const x = tToX(t.getTime());
      return `
        <line class="grid" x1="${x.toFixed(1)}" y1="${padTop}" x2="${x.toFixed(1)}" y2="${padTop + innerH}"/>
        <text class="tick" x="${x.toFixed(1)}" y="${H - 12}" text-anchor="middle">${String(h).padStart(2, "0")}:00</text>
      `;
    }).join("");

    const eventMarker = (date, label, cls) => {
      if (!date) return "";
      const x = tToX(date.getTime());
      return `
        <line class="event-line ${cls}" x1="${x.toFixed(1)}" y1="${padTop}" x2="${x.toFixed(1)}" y2="${padTop + innerH}"/>
        <text class="event-label ${cls}" x="${x.toFixed(1)}" y="${padTop - 4}" text-anchor="middle">${escapeHtml(label)}</text>
      `;
    };

    return `
      <svg class="sun-curve" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="Sun elevation and Dimsome schedule for today">
        <defs>
          <linearGradient id="day-fill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stop-color="var(--warning-color, #ffb300)" stop-opacity="0.45"/>
            <stop offset="100%" stop-color="var(--warning-color, #ffb300)" stop-opacity="0.05"/>
          </linearGradient>
        </defs>

        ${rampRect(windows.dimStart, windows.dimEnd, "ramp-dim")}
        ${rampRect(windows.briStart, windows.briEnd, "ramp-bri")}

        ${hourTicks}

        <line class="horizon" x1="${padX}" y1="${horizonY}" x2="${padX + innerW}" y2="${horizonY}"/>
        <line class="twilight" x1="${padX}" y1="${twilightY}" x2="${padX + innerW}" y2="${twilightY}"/>
        <text class="axis-label" x="${padX + innerW - 4}" y="${twilightY - 4}" text-anchor="end">civil twilight −6°</text>
        <text class="axis-label" x="${padX + innerW - 4}" y="${horizonY - 4}" text-anchor="end">horizon</text>

        <path d="${dayPath}" fill="url(#day-fill)"/>
        <path class="sun-path" d="${path}" fill="none"/>

        ${eventMarker(windows.dimStart, `dim ${formatTime(windows.dimStart || now)}`, "ev-dim")}
        ${eventMarker(windows.briStart, `bright ${formatTime(windows.briStart || now)}`, "ev-bri")}

        <line class="now-line" x1="${nowX.toFixed(1)}" y1="${padTop}" x2="${nowX.toFixed(1)}" y2="${padTop + innerH}"/>
        <circle class="now-dot" cx="${nowX.toFixed(1)}" cy="${elevToY(solarElevation(lat, lon, now)).toFixed(1)}" r="5"/>
      </svg>
    `;
  }

  _renderHero() {
    const now = new Date();
    const windows = this._scheduleWindows(now);
    const statuses = Object.values(this._runtime || {});
    const activeRamp = statuses.find((s) => s?.active_window);

    let headline;
    if (activeRamp) {
      const seq = activeRamp?.active_window?.sequence;
      headline = seq === "brighten" ? "Brightening" : "Dimming";
    } else {
      const elev = solarElevation(windows.lat, windows.lon, now);
      headline = elev >= -6 ? "Tracking day" : "Tracking night";
    }

    const upcoming = [];
    if (windows.dimStart && windows.dimStart > now) upcoming.push({ kind: "dim", at: windows.dimStart });
    if (windows.briStart && windows.briStart > now) upcoming.push({ kind: "brighten", at: windows.briStart });
    upcoming.sort((a, b) => a.at - b.at);
    const next = upcoming[0];
    const subParts = [];
    const totalLights = this._config.lights.length;
    if (totalLights) subParts.push(`${totalLights} light${totalLights === 1 ? "" : "s"}`);
    const activeRampCount = statuses.filter((s) => s?.active_window).length;
    if (activeRampCount) {
      subParts.push(`${activeRampCount} in active ramp`);
    }
    if (next) subParts.push(`next ${next.kind} at ${formatTime(next.at)} (${formatRelative(next.at, now)})`);

    return `
      <ha-card class="hero-card">
        <div class="hero-content">
          <div class="hero-headline-row">
            <div class="hero-text">
              <div class="hero-eyebrow">Dimsome</div>
              <h1 class="hero-headline">${escapeHtml(headline)}</h1>
              <div class="hero-sub">${escapeHtml(subParts.join(" · "))}</div>
            </div>
            <ha-button data-action="resume" title="Resume all lights">
              <ha-svg-icon slot="icon" path="${MDI_PLAY_CIRCLE}"></ha-svg-icon>
              Resume all
            </ha-button>
          </div>
          ${this._renderSunCurve(now, windows)}
          <div class="legend">
            <span><span class="legend-swatch swatch-dim"></span>Dim ramp</span>
            <span><span class="legend-swatch swatch-bri"></span>Brighten ramp</span>
            <span><span class="legend-swatch swatch-now"></span>Now ${formatTime(now)}</span>
          </div>
        </div>
      </ha-card>
    `;
  }

  _renderSchedule(title, path, fallback) {
    const schedule = getPath(this._config, path) || fallback;
    const type = schedule.type || "fixed_time";
    const id = path.replaceAll(".", "-");
    return `
      <div class="schedule-card" aria-labelledby="${id}-title">
        <div class="schedule-title" id="${id}-title">${escapeHtml(title)}</div>
        <div class="field-grid compact">
          ${this._renderField("Schedule", selectHtml({
            path: `${path}.type`,
            value: type,
            options: SCHEDULE_TYPES,
            renderOnChange: true,
          }))}
          ${type === "fixed_time" ? `
            ${this._renderField("Time", `
              <ha-selector
                data-value="${escapeHtml(schedule.at || defaultFixedTimeForPath(path))}"
                data-path="${path}.at"
                data-selector="time"
              ></ha-selector>
            `)}
          ` : `
            ${this._renderField("Sun Event", selectHtml({
              path: `${path}.event`,
              value: schedule.event || "civil_dusk",
              options: SUN_EVENTS,
            }))}
          `}
        </div>
      </div>
    `;
  }

  _renderField(label, controlHtml) {
    return `
      <div class="field-wrap">
        <span class="field-label">${escapeHtml(label)}</span>
        ${controlHtml}
      </div>
    `;
  }

  _renderSetting(title, description, controlHtml) {
    return `
      <div class="setting-row">
        <div class="setting-copy">
          <span class="setting-heading">${escapeHtml(title)}</span>
          <span class="setting-description">${escapeHtml(description)}</span>
        </div>
        <div class="setting-control">${controlHtml}</div>
      </div>
    `;
  }

  _renderGlobal() {
    const global = this._config.global;
    return `
      <ha-card>
        <ha-expansion-panel
          outlined
          header="Schedule &amp; defaults"
          secondary="Dim and brighten timing, plus fall-backs for every light"
        >
        <div class="card-content">
          <div class="two-col">
            ${this._renderSchedule("Dimming", "global.dim_schedule", DEFAULT_CONFIG.global.dim_schedule)}
            ${this._renderSchedule("Brightening", "global.brighten_schedule", DEFAULT_CONFIG.global.brighten_schedule)}
          </div>
          <div class="settings-list">
            ${this._renderSetting("Ramp Duration", "How long each brightness transition should take.", `
              <div class="number-input-wrap" data-suffix="min">
                <input
                  class="native-number"
                  aria-label="Ramp Duration Minutes"
                  type="number"
                  min="1"
                  max="720"
                  inputmode="numeric"
                  value="${durationToMinutes(global.ramp_duration)}"
                  data-path="global.ramp_duration"
                  data-duration="minutes"
                >
              </div>
            `)}
            ${this._renderSetting("Override Resume", "Choose how manual changes return to Dimsome control.", selectHtml({
              path: "global.override_resume_mode",
              value: global.override_resume_mode || "manual_only",
              options: RESUME_MODES,
            }))}
            ${this._renderSetting("Grace Period", "Delay before automatic resume after a manual change.", `
              <div class="number-input-wrap" data-suffix="min">
                <input
                  class="native-number"
                  aria-label="Grace Period Minutes"
                  type="number"
                  min="1"
                  max="720"
                  inputmode="numeric"
                  value="${durationToMinutes(global.override_grace_period, 15)}"
                  data-path="global.override_grace_period"
                  data-duration="minutes"
                >
              </div>
            `)}
            ${this._renderSetting("Split Brightness & Color Calls", "Enable by default for lights that reject combined brightness/color updates.", `
              <ha-switch
                aria-label="Split Brightness &amp; Color Calls"
                ${global.split_turn_on_calls ? "checked" : ""}
                data-path="global.split_turn_on_calls"
              ></ha-switch>
            `)}
            ${this._renderSetting("Apply On Recovery", "Apply the current day, night, or ramp target when a light recovers online while on.", `
              <ha-switch
                aria-label="Apply On Recovery"
                ${(global.apply_on_recovered_on ?? true) ? "checked" : ""}
                data-path="global.apply_on_recovered_on"
              ></ha-switch>
            `)}
            ${this._renderSetting("Native Users", "Comma-separated HA user IDs (e.g. the Node-RED token user) whose light changes are treated like automations instead of manual overrides.", `
              <input
                class="native-text"
                aria-label="Native user IDs"
                type="text"
                placeholder="user id, user id, …"
                value="${escapeHtml((global.native_user_ids || []).join(", "))}"
                data-path="global.native_user_ids"
                data-list="csv"
              >
            `)}
          </div>
        </div>
        </ha-expansion-panel>
      </ha-card>
    `;
  }

  _renderLight(light, index) {
    const state = this._lightStates[light.entity_id] || {};
    const hasColor = Boolean(light.min_color && light.max_color);
    const hasOverrides = hasTimingOverride(light);
    const overrideDetails = hasOverrides ? [
      `dim ${formatScheduleSummary(light.dim_schedule || this._config.global.dim_schedule)}`,
      `brighten ${formatScheduleSummary(light.brighten_schedule || this._config.global.brighten_schedule)}`,
      `ramp ${durationToMinutes(light.ramp_duration, durationToMinutes(this._config.global.ramp_duration))} min`,
    ].join(" · ") : "Using global timing";
    const entityName = formatEntityName(state, light.entity_id);
    return `
      <ha-card class="light-card" data-entity-id="${escapeHtml(light.entity_id)}">
        <div class="card-content">
          <div class="light-header-row">
            <div class="entity-title">
              <div class="entity-icon">
                ${light.entity_id
                  ? `<ha-state-icon data-entity-id="${escapeHtml(light.entity_id)}"></ha-state-icon>`
                  : `<ha-icon icon="mdi:lightbulb-outline"></ha-icon>`}
              </div>
              <div class="entity-info">
                <div class="entity-name">${escapeHtml(entityName)}</div>
                <div class="status-line">${escapeHtml(this._statusText(light))}</div>
              </div>
            </div>
            <div class="light-actions">
              ${hasOverrides ? `
                <span class="status-chip status-custom-schedule">
                  <span class="chip-dot"></span>Custom schedule
                </span>
              ` : ""}
              ${this._renderStatusChip(light.entity_id)}
              <ha-icon-button
                label="Resume this light"
                title="Resume"
                data-action="resume"
                data-entity-id="${escapeHtml(light.entity_id)}"
              ></ha-icon-button>
              <ha-icon-button
                label="Remove light"
                class="remove-btn"
                data-action="remove-light"
                data-index="${index}"
              ></ha-icon-button>
            </div>
          </div>
          <div class="field-grid top-gap">
            ${this._renderField("Light Entity", `
              <ha-entity-picker
                allow-custom-entity
                data-value="${escapeHtml(light.entity_id || "")}"
                data-path="lights.${index}.entity_id"
              ></ha-entity-picker>
            `)}
            ${this._renderField("Minimum Brightness", `
              <div class="number-input-wrap" data-suffix="%">
                <input
                  class="native-number"
                  aria-label="Minimum Brightness"
                  type="number"
                  min="1"
                  max="100"
                  inputmode="numeric"
                  value="${light.min_brightness_pct ?? 10}"
                  data-number="int"
                  data-path="lights.${index}.min_brightness_pct"
                >
              </div>
            `)}
            ${this._renderField("Maximum Brightness", `
              <div class="number-input-wrap" data-suffix="%">
                <input
                  class="native-number"
                  aria-label="Maximum Brightness"
                  type="number"
                  min="1"
                  max="100"
                  inputmode="numeric"
                  value="${light.max_brightness_pct ?? 80}"
                  data-number="int"
                  data-path="lights.${index}.max_brightness_pct"
                >
              </div>
            `)}
          </div>
          <ha-expansion-panel
            outlined
            class="light-advanced"
            header="Color &amp; advanced"
            secondary="Color temperature, settle delay, recovery, split calls"
          >
          <div class="settings-list">
            ${this._renderSetting("Adjust Color Temperature", "Set a Kelvin range during the ramp.", `
              <ha-switch
                aria-label="Adjust Color Temperature"
                ${hasColor ? "checked" : ""}
                data-color-toggle="${index}"
              ></ha-switch>
            `)}
            ${this._renderSetting("Split Brightness & Color Calls", "Use separate service calls for this light.", `
              <ha-switch
                aria-label="Split Brightness &amp; Color Calls"
                ${light.split_turn_on_calls ? "checked" : ""}
                data-path="lights.${index}.split_turn_on_calls"
              ></ha-switch>
            `)}
            ${this._renderSetting("Apply On Recovery", "Apply the current day, night, or ramp target when this light recovers online while on.", `
              <ha-switch
                aria-label="Apply On Recovery"
                ${(light.apply_on_recovered_on ?? true) ? "checked" : ""}
                data-path="lights.${index}.apply_on_recovered_on"
              ></ha-switch>
            `)}
            ${this._renderSetting("Settle Delay", "Wait after this light turns on before applying the current target.", `
              <div class="number-input-wrap" data-suffix="s">
                <input
                  class="native-number"
                  aria-label="Settle Delay Seconds"
                  type="number"
                  min="0"
                  max="30"
                  step="0.1"
                  inputmode="decimal"
                  value="${durationToSeconds(light.settle_delay)}"
                  data-path="lights.${index}.settle_delay"
                  data-number="float"
                >
              </div>
            `)}
          </div>
          ${hasColor ? `
            <div class="field-grid top-gap">
              ${this._renderField("Minimum Brightness Color Temperature", `
                <div class="number-input-wrap" data-suffix="K">
                  <input
                    class="native-number"
                    aria-label="Minimum Brightness Color Temperature"
                    type="number"
                    min="1000"
                    max="12000"
                    step="50"
                    inputmode="numeric"
                    value="${light.min_color?.value ?? 2200}"
                    data-number="int"
                    data-path="lights.${index}.min_color.value"
                  >
                </div>
              `)}
              ${this._renderField("Maximum Brightness Color Temperature", `
                <div class="number-input-wrap" data-suffix="K">
                  <input
                    class="native-number"
                    aria-label="Maximum Brightness Color Temperature"
                    type="number"
                    min="1000"
                    max="12000"
                    step="50"
                    inputmode="numeric"
                    value="${light.max_color?.value ?? 4000}"
                    data-number="int"
                    data-path="lights.${index}.max_color.value"
                  >
                </div>
              `)}
            </div>
          ` : ""}
          </ha-expansion-panel>

          <ha-expansion-panel
            outlined
            class="light-override"
            header="Custom schedule"
            secondary="${escapeHtml(overrideDetails)}"
            ${hasOverrides ? "expanded" : ""}
          >
          <div class="settings-list">
            ${this._renderSetting("Override Global Timing", "Use a separate schedule and resume behavior for this light.", `
              <ha-switch
                aria-label="Override Global Timing"
                ${hasOverrides ? "checked" : ""}
                data-override-toggle="${index}"
              ></ha-switch>
            `)}
          </div>
          ${hasOverrides ? `
            <div class="override-box">
              <div class="two-col">
                ${this._renderSchedule("Dimming Override", `lights.${index}.dim_schedule`, this._config.global.dim_schedule)}
                ${this._renderSchedule("Brightening Override", `lights.${index}.brighten_schedule`, this._config.global.brighten_schedule)}
              </div>
              <div class="field-grid top-gap">
                ${this._renderField("Ramp Duration", `
                  <div class="number-input-wrap" data-suffix="min">
                    <input
                      class="native-number"
                      aria-label="Ramp Duration Minutes"
                      type="number"
                      min="1"
                      max="720"
                      inputmode="numeric"
                      value="${durationToMinutes(light.ramp_duration, durationToMinutes(this._config.global.ramp_duration))}"
                      data-path="lights.${index}.ramp_duration"
                      data-duration="minutes"
                    >
                  </div>
                `)}
                ${this._renderField("Override Resume", selectHtml({
                  path: `lights.${index}.override_resume_mode`,
                  value: light.override_resume_mode || this._config.global.override_resume_mode || "manual_only",
                  options: RESUME_MODES,
                }))}
                ${this._renderField("Grace Period", `
                  <div class="number-input-wrap" data-suffix="min">
                    <input
                      class="native-number"
                      aria-label="Grace Period Minutes"
                      type="number"
                      min="1"
                      max="720"
                      inputmode="numeric"
                      value="${durationToMinutes(light.override_grace_period, durationToMinutes(this._config.global.override_grace_period, 15))}"
                      data-path="lights.${index}.override_grace_period"
                      data-duration="minutes"
                    >
                  </div>
                `)}
              </div>
            </div>
          ` : ""}
          </ha-expansion-panel>
        </div>
      </ha-card>
    `;
  }

  _renderToolbar(actions = true) {
    return `
      <div class="panel-toolbar">
        <ha-menu-button></ha-menu-button>
        <div class="panel-title">Dimsome</div>
        ${actions ? `
          <div class="panel-actions">
            <ha-icon-button
              label="Reload config"
              title="Reload"
              data-action="reload"
            ></ha-icon-button>
            <ha-icon-button
              label="Save configuration"
              title="Save"
              data-action="save"
              ${this._saving ? "disabled" : ""}
            ></ha-icon-button>
          </div>
        ` : ""}
      </div>
    `;
  }

  _renderAddDialog() {
    if (!this._addDialogOpen) return "";
    return `
      <ha-dialog
        open
        hideActions
        heading="Add Light"
        scrimClickAction="cancel"
        escapeKeyAction="cancel"
      >
        <div class="dialog-form">
          ${this._addError ? `<ha-alert alert-type="error">${escapeHtml(this._addError)}</ha-alert>` : ""}
          <ha-entity-picker
            allow-custom-entity
            label="Light Entity"
            data-value="${escapeHtml(this._addDraft.entity_id || "")}"
            data-draft-path="entity_id"
            autofocus
          ></ha-entity-picker>
          <div class="dialog-row">
            ${this._renderField("Min Brightness", `
              <div class="number-input-wrap" data-suffix="%">
                <input
                  class="native-number"
                  aria-label="Min Brightness"
                  type="number"
                  min="1"
                  max="100"
                  inputmode="numeric"
                  value="${this._addDraft.min_brightness_pct ?? 10}"
                  data-draft-path="min_brightness_pct"
                  data-number="int"
                >
              </div>
            `)}
            ${this._renderField("Max Brightness", `
              <div class="number-input-wrap" data-suffix="%">
                <input
                  class="native-number"
                  aria-label="Max Brightness"
                  type="number"
                  min="1"
                  max="100"
                  inputmode="numeric"
                  value="${this._addDraft.max_brightness_pct ?? 80}"
                  data-draft-path="max_brightness_pct"
                  data-number="int"
                >
              </div>
            `)}
          </div>
          <div class="dialog-actions">
            <ha-button data-action="cancel-add">Cancel</ha-button>
            <ha-button raised data-action="confirm-add">Add Light</ha-button>
          </div>
        </div>
      </ha-dialog>
    `;
  }

  _render() {
    if (!this.shadowRoot) return;

    if (!this._loaded) {
      this.shadowRoot.innerHTML = `
        ${this._styles()}
        ${this._renderToolbar(false)}
        <div class="center-state-wrap">
          <div class="center-state">
            <ha-circular-progress active></ha-circular-progress>
            <p>Loading Dimsome…</p>
          </div>
        </div>
      `;
      this._hydrateNativeComponents();
      return;
    }

    if (!this._configured) {
      this.shadowRoot.innerHTML = `
        ${this._styles()}
        ${this._renderToolbar(false)}
        <div class="center-state-wrap">
          <div class="center-state">
            <ha-icon icon="mdi:brightness-6" class="empty-icon"></ha-icon>
            <h2>Not Configured</h2>
            <p>Add Dimsome from Settings &gt; Devices &amp; Services &gt; Add Integration.</p>
            <a href="/config/integrations">Open Integrations</a>
          </div>
        </div>
      `;
      this._hydrateNativeComponents();
      return;
    }

    this.shadowRoot.innerHTML = `
      ${this._styles()}
      ${this._renderToolbar(true)}

      <main class="page-body">
        <div class="announcements" aria-live="polite">
          ${this._error ? `<ha-alert alert-type="error">${escapeHtml(this._error)}</ha-alert>` : ""}
          ${this._message ? `<ha-alert alert-type="success">${escapeHtml(this._message)}</ha-alert>` : ""}
        </div>

        ${this._renderHero()}

        <div class="section-head">
          <h2>Configuration</h2>
        </div>
        <section>
          ${this._renderGlobal()}
        </section>

        <div class="section-head">
          <h2>Lights</h2>
          <div class="section-head-actions">
            <span class="lights-count">${this._config.lights.length} configured</span>
            <ha-button raised data-action="open-add-dialog">
              <ha-svg-icon slot="icon" path="${MDI_PLUS}"></ha-svg-icon>
              Add Light
            </ha-button>
          </div>
        </div>

        <section class="lights-list">
          ${this._config.lights.map((light, index) => this._renderLight(light, index)).join("") || `
            <ha-card>
              <div class="center-state">
                <ha-icon icon="mdi:lightbulb-outline" class="empty-icon"></ha-icon>
                <h2>No Lights Yet</h2>
                <p>Add a light to start adaptive dimming.</p>
                <ha-button raised data-action="open-add-dialog">Add Light</ha-button>
              </div>
            </ha-card>
          `}
        </section>
      </main>

      ${this._renderAddDialog()}
    `;
    this._hydrateNativeComponents();
  }

  _styles() {
    return `
      <style>
        :host {
          display: block;
          color: var(--primary-text-color);
        }

        /* ── Toolbar ─────────────────────────────────────────────────── */
        .panel-toolbar {
          --icon-primary-color: var(--app-header-text-color, var(--text-primary-color));
          align-items: center;
          background-color: var(--app-header-background-color, var(--primary-color));
          color: var(--app-header-text-color, var(--text-primary-color));
          display: flex;
          height: 64px;
          padding-inline-end: 4px;
          position: sticky;
          top: 0;
          z-index: 4;
        }

        ha-menu-button {
          --mdc-icon-button-size: 40px;
          --mdc-icon-size: 24px;
          margin-inline-start: 4px;
        }

        .panel-title {
          flex: 1;
          font-size: var(--mdc-typography-headline6-font-size, 1.25rem);
          font-weight: var(--mdc-typography-headline6-font-weight, 500);
          letter-spacing: 0.0125em;
          overflow: hidden;
          padding: 0 16px;
          text-overflow: ellipsis;
          white-space: nowrap;
        }

        .panel-actions {
          align-items: center;
          display: flex;
        }

        /* ── Page body ───────────────────────────────────────────────── */
        .page-body {
          box-sizing: border-box;
          max-width: 1120px;
          margin: 0 auto;
          padding: 16px max(16px, env(safe-area-inset-right)) 40px max(16px, env(safe-area-inset-left));
        }

        /* Reset bare elements — shadow DOM doesn't inherit HA globals */
        h2,
        h3,
        p {
          margin: 0;
        }

        ha-card,
        ha-alert {
          display: block;
        }

        ha-selector,
        ha-entity-picker {
          width: 100%;
        }

        /* Native <select> styled to match HA's outlined inputs. HA's frontend
           doesn't reliably expose a self-contained dropdown component to
           custom panels, so we use a real <select> wrapped for a theme-aware
           dropdown chevron. */
        .native-select-wrap {
          display: block;
          position: relative;
          width: 100%;
        }

        .native-select-wrap::after {
          border-right: 2px solid var(--secondary-text-color);
          border-bottom: 2px solid var(--secondary-text-color);
          content: "";
          height: 8px;
          pointer-events: none;
          position: absolute;
          right: 14px;
          top: 50%;
          transform: translateY(-70%) rotate(45deg);
          width: 8px;
        }

        .native-select {
          appearance: none;
          -webkit-appearance: none;
          background-color: var(--card-background-color, var(--primary-background-color));
          border: 1px solid var(--divider-color);
          border-radius: 4px;
          box-sizing: border-box;
          color: var(--primary-text-color);
          cursor: pointer;
          font: inherit;
          font-size: 1rem;
          line-height: 1.2;
          min-height: 40px;
          padding: 0 32px 0 12px;
          transition: border-color 120ms ease, box-shadow 120ms ease;
          width: 100%;
        }

        .native-select:hover {
          border-color: var(--secondary-text-color);
        }

        .native-select:focus {
          border-color: var(--primary-color);
          box-shadow: 0 0 0 1px var(--primary-color);
          outline: none;
        }

        .native-select:disabled {
          color: var(--disabled-text-color);
          cursor: not-allowed;
        }

        .number-input-wrap {
          position: relative;
          width: 100%;
        }

        .number-input-wrap::after {
          color: var(--secondary-text-color);
          content: attr(data-suffix);
          pointer-events: none;
          position: absolute;
          right: 12px;
          top: 50%;
          transform: translateY(-50%);
        }

        .native-number,
        .native-text {
          background-color: var(--card-background-color, var(--primary-background-color));
          border: 1px solid var(--divider-color);
          border-radius: 4px;
          box-sizing: border-box;
          color: var(--primary-text-color);
          font: inherit;
          font-size: 1rem;
          line-height: 1.2;
          min-height: 40px;
          padding: 0 28px 0 12px;
          transition: border-color 120ms ease, box-shadow 120ms ease;
          width: 100%;
        }

        .native-text {
          padding-right: 12px;
        }

        .native-text::placeholder {
          color: var(--disabled-text-color);
        }

        .native-number:hover,
        .native-text:hover {
          border-color: var(--secondary-text-color);
        }

        .native-number:focus,
        .native-text:focus {
          border-color: var(--primary-color);
          box-shadow: 0 0 0 1px var(--primary-color);
          outline: none;
        }

        ha-button,
        ha-switch,
        ha-icon-button {
          touch-action: manipulation;
        }

        .card-content {
          padding: 16px;
        }

        /* ── Settings rows ───────────────────────────────────────────── */
        .settings-list {
          margin-top: 8px;
        }

        .setting-row {
          align-items: center;
          border-top: 1px solid var(--divider-color);
          display: grid;
          gap: 16px;
          grid-template-columns: minmax(0, 1fr) minmax(140px, 240px);
          padding: 8px 0;
        }

        .setting-row:first-child {
          border-top: none;
        }

        .setting-copy {
          display: grid;
          gap: 2px;
          min-width: 0;
        }

        .setting-heading {
          color: var(--primary-text-color);
          font-size: 1rem;
          line-height: 1.25;
        }

        .setting-description {
          color: var(--secondary-text-color);
          font-size: 0.875rem;
          line-height: 1.35;
        }

        .setting-control {
          min-width: 0;
        }

        .setting-control > ha-switch {
          display: block;
          margin-left: auto;
          width: max-content;
        }

        /* ── Layout ──────────────────────────────────────────────────── */
        .announcements {
          display: grid;
          gap: 8px;
          margin-bottom: 16px;
        }

        .panel-grid,
        .lights-list {
          display: grid;
          gap: 16px;
        }

        .section-head {
          align-items: center;
          display: flex;
          gap: 16px;
          justify-content: space-between;
          margin: 24px 0 12px;
        }

        .section-head h2 {
          color: var(--secondary-text-color);
          font-size: 0.75rem;
          font-weight: 500;
          letter-spacing: 0.1em;
          text-transform: uppercase;
        }

        .section-head-actions {
          align-items: center;
          display: flex;
          gap: 12px;
        }

        .lights-count {
          color: var(--secondary-text-color);
          font-size: 0.875rem;
        }

        /* ── Field grids ─────────────────────────────────────────────── */
        /* align-items: start prevents shorter inputs from stretching to match
           taller siblings (e.g. ha-selector[type=time] with its 3-box layout). */
        .two-col,
        .field-grid {
          align-items: start;
          display: grid;
          gap: 16px;
          grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
        }

        .field-grid.compact {
          grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
        }

        .field-wrap {
          display: grid;
          gap: 6px;
          min-width: 0;
        }

        .field-label {
          color: var(--primary-text-color);
          font-size: 0.875rem;
          font-weight: 500;
          line-height: 1.25;
        }

        .top-gap {
          margin-top: 16px;
        }

        /* ── Schedule sub-card ───────────────────────────────────────── */
        .schedule-card {
          background: var(--secondary-background-color);
          border: 1px solid var(--ha-card-border-color, var(--divider-color));
          border-radius: var(--ha-card-border-radius, 12px);
          padding: 16px;
        }

        .schedule-title {
          color: var(--secondary-text-color);
          font-size: 0.875rem;
          font-weight: 500;
          margin-bottom: 12px;
        }

        /* ── Per-light override box ──────────────────────────────────── */
        /* Negative margin cancels card-content's 16px padding so the field-grid
           inside starts at the same x-position as the main field-grid above. */
        .override-box {
          background: var(--secondary-background-color);
          border-top: 1px solid var(--divider-color);
          margin: 0 -16px;
          padding: 16px;
        }

        /* ── Light card header ───────────────────────────────────────── */
        .light-header-row {
          align-items: flex-start;
          display: flex;
          gap: 16px;
          justify-content: space-between;
        }

        .entity-title {
          align-items: center;
          display: flex;
          flex: 1;
          gap: 12px;
          min-width: 0;
        }

        .entity-icon {
          align-items: center;
          display: flex;
          flex: 0 0 40px;
          height: 40px;
          justify-content: center;
          width: 40px;
        }

        .entity-info {
          min-width: 0;
        }

        .entity-name {
          font-weight: 500;
          overflow-wrap: anywhere;
        }

        .status-line {
          color: var(--secondary-text-color);
          font-size: 0.875rem;
          font-variant-numeric: tabular-nums;
          margin-top: 2px;
          overflow-wrap: anywhere;
        }

        .light-actions {
          align-items: center;
          display: flex;
          flex-shrink: 0;
          gap: 4px;
        }

        .remove-btn {
          color: var(--error-color);
        }

        /* ── Center states ───────────────────────────────────────────── */
        .center-state-wrap {
          align-items: center;
          display: flex;
          justify-content: center;
          min-height: 60vh;
        }

        .center-state {
          align-items: center;
          display: grid;
          gap: 12px;
          justify-items: center;
          padding: 48px 16px;
          text-align: center;
        }

        .center-state h2 {
          color: var(--primary-text-color);
          font-size: 1.25rem;
          font-weight: 500;
        }

        .center-state p {
          color: var(--secondary-text-color);
        }

        .empty-icon {
          color: var(--secondary-text-color);
          --mdc-icon-size: 40px;
        }

        /* ── Add Light dialog ────────────────────────────────────────── */
        ha-dialog {
          --mdc-dialog-min-width: min(420px, calc(100vw - 32px));
          --mdc-dialog-max-width: 560px;
          --dialog-content-padding: 0;
        }

        .dialog-form {
          display: grid;
          gap: 16px;
          padding: 8px 24px 8px;
        }

        .dialog-row {
          display: grid;
          gap: 12px;
          grid-template-columns: 1fr 1fr;
        }

        .dialog-actions {
          display: flex;
          gap: 8px;
          justify-content: flex-end;
          margin-top: 8px;
        }

        a {
          color: var(--primary-color);
          text-decoration: none;
        }

        :focus-visible {
          outline: 2px solid var(--primary-color);
          outline-offset: 3px;
        }

        /* ── Hero card ───────────────────────────────────────────────── */
        .hero-card {
          --ha-card-border-radius: 18px;
          overflow: hidden;
          margin-bottom: 8px;
        }
        .hero-content {
          display: grid;
          gap: 16px;
          padding: 20px 24px 16px;
        }
        .hero-headline-row {
          align-items: flex-start;
          display: flex;
          gap: 16px;
          justify-content: space-between;
        }
        .hero-eyebrow {
          color: var(--secondary-text-color);
          font-size: 0.75rem;
          font-weight: 600;
          letter-spacing: 0.12em;
          text-transform: uppercase;
        }
        .hero-headline {
          color: var(--primary-text-color);
          font-size: 1.75rem;
          font-weight: 500;
          line-height: 1.2;
          margin-top: 4px;
        }
        .hero-sub {
          color: var(--secondary-text-color);
          font-size: 0.95rem;
          margin-top: 4px;
        }
        .sun-curve {
          border-radius: 12px;
          display: block;
          height: auto;
          width: 100%;
        }
        .sun-curve .grid { stroke: var(--divider-color); stroke-width: 1; opacity: 0.5; }
        .sun-curve .tick { fill: var(--secondary-text-color); font-size: 11px; }
        .sun-curve .horizon { stroke: var(--secondary-text-color); stroke-width: 1.2; opacity: 0.6; }
        .sun-curve .twilight {
          stroke: var(--secondary-text-color);
          stroke-width: 1;
          stroke-dasharray: 4 4;
          opacity: 0.5;
        }
        .sun-curve .axis-label { fill: var(--secondary-text-color); font-size: 10px; opacity: 0.8; }
        .sun-curve .sun-path { stroke: var(--warning-color, #ffb300); stroke-width: 2.5; }
        .sun-curve .ramp-dim { fill: var(--info-color, #039be5); opacity: 0.18; }
        .sun-curve .ramp-bri { fill: var(--success-color, #43a047); opacity: 0.18; }
        .sun-curve .event-line.ev-dim {
          stroke: var(--info-color, #039be5);
          stroke-width: 1.5;
          stroke-dasharray: 2 3;
        }
        .sun-curve .event-line.ev-bri {
          stroke: var(--success-color, #43a047);
          stroke-width: 1.5;
          stroke-dasharray: 2 3;
        }
        .sun-curve .event-label { font-size: 11px; font-weight: 600; }
        .sun-curve .event-label.ev-dim { fill: var(--info-color, #039be5); }
        .sun-curve .event-label.ev-bri { fill: var(--success-color, #43a047); }
        .sun-curve .now-line { stroke: var(--primary-color); stroke-width: 2; }
        .sun-curve .now-dot {
          fill: var(--primary-color);
          stroke: var(--card-background-color);
          stroke-width: 2;
        }
        .legend {
          align-items: center;
          color: var(--secondary-text-color);
          display: flex;
          flex-wrap: wrap;
          font-size: 0.85rem;
          gap: 6px 16px;
        }
        .legend-swatch {
          border-radius: 3px;
          display: inline-block;
          height: 12px;
          margin-right: 6px;
          vertical-align: middle;
          width: 18px;
        }
        .swatch-dim { background: var(--info-color, #039be5); opacity: 0.5; }
        .swatch-bri { background: var(--success-color, #43a047); opacity: 0.5; }
        .swatch-now {
          background: var(--primary-color);
          height: 12px;
          width: 3px;
          vertical-align: middle;
          margin-right: 6px;
          display: inline-block;
        }

        /* ── Status chip ─────────────────────────────────────────────── */
        .status-chip {
          align-items: center;
          background: var(--secondary-background-color);
          border-radius: 999px;
          color: var(--secondary-text-color);
          display: inline-flex;
          font-size: 0.75rem;
          font-weight: 500;
          gap: 6px;
          letter-spacing: 0.02em;
          padding: 4px 10px;
          white-space: nowrap;
        }
        .chip-dot {
          background: currentColor;
          border-radius: 50%;
          display: inline-block;
          height: 8px;
          width: 8px;
        }
        .status-chip.status-ramping {
          background: color-mix(in srgb, var(--info-color, #039be5) 18%, transparent);
          color: var(--info-color, #039be5);
        }
        .status-chip.status-tracking {
          background: color-mix(in srgb, var(--success-color, #43a047) 14%, transparent);
          color: var(--success-color, #43a047);
        }
        .status-chip.status-custom-schedule {
          background: color-mix(in srgb, var(--primary-color) 14%, transparent);
          color: var(--primary-color);
        }
        .status-chip.status-manual_override {
          background: color-mix(in srgb, var(--warning-color, #ffb300) 22%, transparent);
          color: var(--warning-color, #ff8f00);
        }
        .status-chip.status-stood_down {
          background: color-mix(in srgb, var(--secondary-text-color) 18%, transparent);
        }
        .status-chip.status-disabled {
          background: color-mix(in srgb, var(--error-color, #d32f2f) 14%, transparent);
          color: var(--error-color, #d32f2f);
        }

        ha-expansion-panel.light-advanced,
        ha-expansion-panel.light-override {
          margin-top: 16px;
          --ha-card-border-radius: 10px;
          display: block;
        }

        /* ── Mobile ──────────────────────────────────────────────────── */
        @media (max-width: 720px) {
          .hero-content { padding: 16px; }
          .hero-headline { font-size: 1.4rem; }
          .hero-headline-row { flex-wrap: wrap; }
          .light-header-row {
            display: grid;
          }

          .light-actions {
            justify-content: flex-start;
          }

          .field-grid,
          .field-grid.compact,
          .two-col {
            grid-template-columns: 1fr;
          }

          .setting-row {
            align-items: start;
            grid-template-columns: 1fr;
          }

          .setting-control > ha-switch {
            margin-left: 0;
          }

          .section-head {
            flex-wrap: wrap;
          }

          .dialog-row {
            grid-template-columns: 1fr;
          }
        }
      </style>
    `;
  }
}

customElements.define("dimsome-panel", DimsomePanel);
