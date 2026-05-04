const DEFAULT_CONFIG = {
  global: {
    dim_schedule: { type: "civil_sun", event: "civil_dusk" },
    brighten_schedule: { type: "fixed_time", at: "06:00:00" },
    ramp_duration: "01:00:00",
    override_resume_mode: "manual_only",
    override_grace_period: "00:15:00",
    split_turn_on_calls: false,
  },
  lights: [],
};

const SCHEDULE_TYPES = [
  ["fixed_time", "Fixed time"],
  ["civil_sun", "Civil sun"],
];

const SUN_EVENTS = [
  ["civil_dawn", "Civil dawn"],
  ["civil_dusk", "Civil dusk"],
];

const RESUME_MODES = [
  ["manual_only", "Manual only"],
  ["after_grace_period", "After grace period"],
];

const COLOR_MODE = "color_temp_kelvin";

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

const getPath = (object, path) => path.split(".").reduce((value, key) => value?.[key], object);

const setPath = (object, path, value) => {
  const parts = path.split(".");
  let current = object;
  for (const part of parts.slice(0, -1)) {
    if (!current[part]) current[part] = {};
    current = current[part];
  }
  current[parts.at(-1)] = value;
};

const optionHtml = (options, current) => options.map(([value, label]) => (
  `<option value="${escapeHtml(value)}" ${value === current ? "selected" : ""}>${escapeHtml(label)}</option>`
)).join("");

class DimsomePanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._hass = null;
    this._panel = null;
    this._loaded = false;
    this._saving = false;
    this._configured = false;
    this._config = clone(DEFAULT_CONFIG);
    this._lightStates = {};
    this._error = "";
    this._message = "";
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._loaded) this._loadConfig();
    this._render();
  }

  get hass() {
    return this._hass;
  }

  set panel(panel) {
    this._panel = panel;
  }

  connectedCallback() {
    this.shadowRoot.addEventListener("click", (event) => this._handleClick(event));
    this.shadowRoot.addEventListener("input", (event) => this._handleInput(event));
    this.shadowRoot.addEventListener("change", (event) => this._handleInput(event));
    this._render();
  }

  async _loadConfig() {
    if (!this._hass) return;
    this._loaded = true;
    try {
      const result = await this._hass.callWS({ type: "dimsome/config" });
      this._configured = result.configured;
      this._config = this._normalizeConfig(result.config || DEFAULT_CONFIG);
      this._lightStates = result.light_states || {};
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
    this._message = "Saving...";
    this._render();
    try {
      await this._hass.callWS({ type: "dimsome/save_config", config: this._config });
      this._message = "Saved. Dimsome was reloaded.";
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
    await this._hass.callService("dimsome", "resume", data);
    this._message = entityId ? `Resumed ${entityId}.` : "Resumed all Dimsome lights.";
    this._render();
  }

  _handleClick(event) {
    const button = event.target.closest("button[data-action]");
    if (!button) return;
    const action = button.dataset.action;
    const index = Number(button.dataset.index);
    if (action === "save") this._saveConfig();
    if (action === "reload") this._loadConfig();
    if (action === "add-light") this._addLight();
    if (action === "remove-light") this._removeLight(index);
    if (action === "resume") this._resume(button.dataset.entityId || null);
  }

  _handleInput(event) {
    const input = event.target;
    if (!input.dataset) return;
    if (input.dataset.path) {
      let value = input.type === "checkbox" ? input.checked : input.value;
      if (input.dataset.number === "int") value = Number(value);
      if (input.dataset.duration === "minutes") value = minutesToDuration(value);
      setPath(this._config, input.dataset.path, value);
      this._normalizeScheduleForPath(input.dataset.path);
      this._render();
      return;
    }
    if (input.dataset.colorToggle) {
      const index = Number(input.dataset.colorToggle);
      const light = this._config.lights[index];
      if (input.checked) {
        light.min_color = { mode: COLOR_MODE, value: 2200 };
        light.max_color = { mode: COLOR_MODE, value: 4000 };
      } else {
        delete light.min_color;
        delete light.max_color;
      }
      this._render();
      return;
    }
    if (input.dataset.overrideToggle) {
      const index = Number(input.dataset.overrideToggle);
      const light = this._config.lights[index];
      if (input.checked) {
        light.dim_schedule = clone(this._config.global.dim_schedule);
        light.brighten_schedule = clone(this._config.global.brighten_schedule);
        light.ramp_duration = this._config.global.ramp_duration;
        light.override_resume_mode = this._config.global.override_resume_mode;
        light.override_grace_period = this._config.global.override_grace_period;
      } else {
        delete light.dim_schedule;
        delete light.brighten_schedule;
        delete light.ramp_duration;
        delete light.override_resume_mode;
        delete light.override_grace_period;
      }
      this._render();
    }
  }

  _normalizeScheduleForPath(path) {
    if (!path.endsWith(".type")) return;
    const schedulePath = path.slice(0, -5);
    const schedule = getPath(this._config, schedulePath);
    if (schedule.type === "fixed_time") {
      schedule.at ||= "06:00:00";
      delete schedule.event;
    } else {
      schedule.event ||= "civil_dusk";
      delete schedule.at;
    }
  }

  _addLight() {
    this._config.lights.push({
      entity_id: "",
      min_brightness_pct: 10,
      max_brightness_pct: 80,
      split_turn_on_calls: this._config.global.split_turn_on_calls || false,
    });
    this._render();
  }

  _removeLight(index) {
    this._config.lights.splice(index, 1);
    this._render();
  }

  _availableLightOptions() {
    const states = this._hass?.states || {};
    return Object.keys(states)
      .filter((entityId) => entityId.startsWith("light."))
      .sort()
      .map((entityId) => `<option value="${escapeHtml(entityId)}"></option>`)
      .join("");
  }

  _renderSchedule(title, path, fallback) {
    const schedule = getPath(this._config, path) || fallback;
    const type = schedule.type || "fixed_time";
    return `
      <div class="schedule-grid">
        <h4>${escapeHtml(title)}</h4>
        <label>Type
          <select data-path="${path}.type">${optionHtml(SCHEDULE_TYPES, type)}</select>
        </label>
        ${type === "fixed_time" ? `
          <label>Time
            <input type="time" step="1" value="${escapeHtml(schedule.at || "06:00:00")}" data-path="${path}.at">
          </label>
        ` : `
          <label>Sun event
            <select data-path="${path}.event">${optionHtml(SUN_EVENTS, schedule.event || "civil_dusk")}</select>
          </label>
        `}
      </div>
    `;
  }

  _renderGlobal() {
    const global = this._config.global;
    return `
      <section class="card">
        <div class="card-title">
          <div>
            <h2>Global defaults</h2>
            <p>Used by every light unless a light overrides them.</p>
          </div>
        </div>
        <div class="two-col">
          ${this._renderSchedule("Dimming", "global.dim_schedule", DEFAULT_CONFIG.global.dim_schedule)}
          ${this._renderSchedule("Brightening", "global.brighten_schedule", DEFAULT_CONFIG.global.brighten_schedule)}
        </div>
        <div class="form-grid">
          <label>Ramp duration
            <input type="number" min="1" max="720" value="${durationToMinutes(global.ramp_duration)}" data-path="global.ramp_duration" data-duration="minutes"> minutes
          </label>
          <label>Override resume
            <select data-path="global.override_resume_mode">${optionHtml(RESUME_MODES, global.override_resume_mode || "manual_only")}</select>
          </label>
          <label>Grace period
            <input type="number" min="1" max="720" value="${durationToMinutes(global.override_grace_period, 15)}" data-path="global.override_grace_period" data-duration="minutes"> minutes
          </label>
          <label class="check"><input type="checkbox" ${global.split_turn_on_calls ? "checked" : ""} data-path="global.split_turn_on_calls"> Split brightness and color calls by default</label>
        </div>
      </section>
    `;
  }

  _renderLight(light, index) {
    const state = this._lightStates[light.entity_id] || {};
    const attrs = state.attributes || {};
    const hasColor = Boolean(light.min_color && light.max_color);
    const hasOverrides = Boolean(light.dim_schedule || light.brighten_schedule || light.ramp_duration || light.override_resume_mode);
    return `
      <section class="card light-card">
        <div class="card-title">
          <div>
            <h3>${escapeHtml(light.entity_id || "New light")}</h3>
            <p>${escapeHtml(state.state || "not found")}${attrs.brightness ? ` · brightness ${attrs.brightness}/255` : ""}${attrs.color_temp_kelvin ? ` · ${attrs.color_temp_kelvin} K` : ""}</p>
          </div>
          <div class="actions">
            <button type="button" data-action="resume" data-entity-id="${escapeHtml(light.entity_id || "")}">Resume</button>
            <button type="button" class="danger" data-action="remove-light" data-index="${index}">Remove</button>
          </div>
        </div>
        <div class="form-grid">
          <label>Light entity
            <input value="${escapeHtml(light.entity_id || "")}" list="dimsome-light-entities" data-path="lights.${index}.entity_id">
          </label>
          <label>Minimum brightness
            <input type="number" min="1" max="100" value="${light.min_brightness_pct ?? 10}" data-number="int" data-path="lights.${index}.min_brightness_pct"> %
          </label>
          <label>Maximum brightness
            <input type="number" min="1" max="100" value="${light.max_brightness_pct ?? 80}" data-number="int" data-path="lights.${index}.max_brightness_pct"> %
          </label>
          <label class="check"><input type="checkbox" ${light.split_turn_on_calls ? "checked" : ""} data-path="lights.${index}.split_turn_on_calls"> Split brightness and color calls</label>
          <label class="check"><input type="checkbox" ${hasColor ? "checked" : ""} data-color-toggle="${index}"> Adjust color temperature</label>
          ${hasColor ? `
            <label>Minimum color temperature
              <input type="number" min="1000" max="12000" step="50" value="${light.min_color?.value ?? 2200}" data-number="int" data-path="lights.${index}.min_color.value"> K
            </label>
            <label>Maximum color temperature
              <input type="number" min="1000" max="12000" step="50" value="${light.max_color?.value ?? 4000}" data-number="int" data-path="lights.${index}.max_color.value"> K
            </label>
          ` : ""}
          <label class="check"><input type="checkbox" ${hasOverrides ? "checked" : ""} data-override-toggle="${index}"> Override global timing for this light</label>
        </div>
        ${hasOverrides ? `
          <div class="override-box">
            <div class="two-col">
              ${this._renderSchedule("Dimming override", `lights.${index}.dim_schedule`, this._config.global.dim_schedule)}
              ${this._renderSchedule("Brightening override", `lights.${index}.brighten_schedule`, this._config.global.brighten_schedule)}
            </div>
            <div class="form-grid">
              <label>Ramp duration
                <input type="number" min="1" max="720" value="${durationToMinutes(light.ramp_duration, durationToMinutes(this._config.global.ramp_duration))}" data-path="lights.${index}.ramp_duration" data-duration="minutes"> minutes
              </label>
              <label>Override resume
                <select data-path="lights.${index}.override_resume_mode">${optionHtml(RESUME_MODES, light.override_resume_mode || this._config.global.override_resume_mode || "manual_only")}</select>
              </label>
              <label>Grace period
                <input type="number" min="1" max="720" value="${durationToMinutes(light.override_grace_period, durationToMinutes(this._config.global.override_grace_period, 15))}" data-path="lights.${index}.override_grace_period" data-duration="minutes"> minutes
              </label>
            </div>
          </div>
        ` : ""}
      </section>
    `;
  }

  _render() {
    if (!this.shadowRoot) return;
    if (!this._loaded) {
      this.shadowRoot.innerHTML = `${this._styles()}<main><div class="loading">Loading Dimsome...</div></main>`;
      return;
    }

    if (!this._configured) {
      this.shadowRoot.innerHTML = `${this._styles()}<main><section class="card"><h1>Dimsome</h1><p>Dimsome is not set up yet. Add it from Settings -> Devices & services -> Add integration -> Dimsome.</p></section></main>`;
      return;
    }

    this.shadowRoot.innerHTML = `
      ${this._styles()}
      <main>
        <datalist id="dimsome-light-entities">${this._availableLightOptions()}</datalist>
        <header>
          <div>
            <h1>Dimsome</h1>
            <p>Manage adaptive dimming schedules, brightness ranges, and manual override behavior.</p>
          </div>
          <div class="toolbar">
            <button type="button" data-action="reload">Reload</button>
            <button type="button" data-action="resume">Resume all</button>
            <button type="button" class="primary" data-action="save" ${this._saving ? "disabled" : ""}>Save</button>
          </div>
        </header>
        ${this._error ? `<div class="alert error">${escapeHtml(this._error)}</div>` : ""}
        ${this._message ? `<div class="alert ok">${escapeHtml(this._message)}</div>` : ""}
        ${this._renderGlobal()}
        <section class="section-head">
          <div>
            <h2>Lights</h2>
            <p>${this._config.lights.length} configured</p>
          </div>
          <button type="button" data-action="add-light">Add light</button>
        </section>
        ${this._config.lights.map((light, index) => this._renderLight(light, index)).join("") || `<section class="card empty"><p>No lights configured yet.</p><button type="button" data-action="add-light">Add your first light</button></section>`}
      </main>
    `;
  }

  _styles() {
    return `
      <style>
        :host { display: block; color: var(--primary-text-color); }
        main { max-width: 1180px; margin: 0 auto; padding: 24px; box-sizing: border-box; }
        header, .section-head, .card-title { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; }
        h1, h2, h3, h4, p { margin: 0; }
        h1 { font-size: 32px; }
        h2 { font-size: 22px; }
        h3 { font-size: 18px; }
        h4 { font-size: 15px; margin-bottom: 10px; }
        p { color: var(--secondary-text-color); margin-top: 4px; }
        .toolbar, .actions { display: flex; gap: 8px; flex-wrap: wrap; }
        .card, .section-head { margin-top: 18px; }
        .card { background: var(--card-background-color); border-radius: 18px; padding: 18px; box-shadow: var(--ha-card-box-shadow, 0 2px 8px rgba(0,0,0,.16)); border: 1px solid var(--divider-color); }
        .light-card { border-left: 5px solid var(--accent-color); }
        .form-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(230px, 1fr)); gap: 14px; margin-top: 16px; }
        .two-col { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 16px; margin-top: 16px; }
        .schedule-grid, .override-box { border: 1px solid var(--divider-color); border-radius: 14px; padding: 14px; }
        .override-box { margin-top: 16px; background: color-mix(in srgb, var(--primary-background-color) 65%, transparent); }
        label { display: block; font-weight: 600; font-size: 13px; color: var(--secondary-text-color); }
        label.check { display: flex; align-items: center; gap: 8px; color: var(--primary-text-color); }
        input, select { width: 100%; box-sizing: border-box; margin-top: 6px; padding: 9px 10px; border-radius: 10px; border: 1px solid var(--divider-color); background: var(--primary-background-color); color: var(--primary-text-color); font: inherit; }
        input[type="checkbox"] { width: auto; margin: 0; }
        button { border: 1px solid var(--divider-color); border-radius: 999px; background: var(--card-background-color); color: var(--primary-text-color); padding: 9px 14px; cursor: pointer; font-weight: 600; }
        button.primary { background: var(--accent-color); color: var(--text-primary-color, white); border-color: var(--accent-color); }
        button.danger { color: var(--error-color); }
        button:disabled { opacity: .6; cursor: progress; }
        .alert { margin-top: 16px; padding: 12px 14px; border-radius: 12px; }
        .alert.error { background: color-mix(in srgb, var(--error-color) 16%, transparent); color: var(--error-color); }
        .alert.ok { background: color-mix(in srgb, var(--success-color, #4caf50) 16%, transparent); }
        .loading, .empty { text-align: center; color: var(--secondary-text-color); }
        @media (max-width: 720px) {
          main { padding: 14px; }
          header, .section-head, .card-title { display: block; }
          .toolbar, .actions { margin-top: 12px; }
        }
      </style>
    `;
  }
}

customElements.define("dimsome-panel", DimsomePanel);
