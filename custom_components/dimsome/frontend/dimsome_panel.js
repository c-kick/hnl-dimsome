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
  `<ha-list-item value="${escapeHtml(value)}" ${value === current ? "selected" : ""}>${escapeHtml(label)}</ha-list-item>`
)).join("");

const percentBrightness = (value) => {
  if (!value) return "";
  return `${Math.round((Number(value) / 255) * 100)}%`;
};

const formatEntityName = (state, entityId) => (
  state?.attributes?.friendly_name || entityId || "New Light"
);

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
    const hadHass = Boolean(this._hass);
    this._hass = hass;
    if (!this._loaded) this._loadConfig();
    if (!hadHass && this.shadowRoot?.hasChildNodes()) this._hydrateNativeComponents();
  }

  get hass() {
    return this._hass;
  }

  set panel(panel) {
    this._panel = panel;
  }

  connectedCallback() {
    this.shadowRoot.addEventListener("click", (event) => this._handleClick(event));
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
    } catch (error) {
      this._error = error.message || String(error);
      this._message = "";
    }
    this._render();
  }

  _handleClick(event) {
    if (!(event.target instanceof Element)) return;
    const button = event.target.closest("[data-action]");
    if (!button) return;
    const action = button.dataset.action;
    const index = Number(button.dataset.index);
    if (action === "save") this._saveConfig();
    if (action === "reload") this._loadConfig();
    if (action === "add-light") this._addLight();
    if (action === "remove-light") this._removeLight(index);
    if (action === "resume") this._resume(button.dataset.entityId || null);
  }

  _handleControlInput(control, event) {
    if (!control?.dataset) return;
    if (control.dataset.path) {
      let value = this._controlValue(control, event);
      if (control.dataset.number === "int") value = Number(value);
      if (control.dataset.duration === "minutes") value = minutesToDuration(value);
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

  _controlValue(input, event) {
    if (input.localName === "ha-switch" || input.type === "checkbox") return input.checked;
    if (input.localName === "ha-select" && event.type === "selected") {
      const item = input.items?.[event.detail?.index];
      if (item?.value !== undefined) return item.value;
    }
    if (event.detail?.item?.value !== undefined) return event.detail.item.value;
    if (event.detail?.value !== undefined) return event.detail.value || "";
    return input.value ?? "";
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
    const light = this._config.lights[index];
    const name = light?.entity_id || "this light";
    if (!window.confirm(`Remove ${name} from Dimsome?`)) return;
    this._config.lights.splice(index, 1);
    this._render();
  }

  _hydrateNativeComponents() {
    if (!this.shadowRoot) return;
    this.shadowRoot.querySelectorAll("ha-entity-picker").forEach((picker) => {
      picker.hass = this._hass;
      picker.includeDomains = ["light"];
      picker.value = picker.dataset.value || "";
    });
    this.shadowRoot.querySelectorAll("ha-state-icon").forEach((icon) => {
      icon.hass = this._hass;
      icon.stateObj = this._hass?.states?.[icon.dataset.entityId];
    });
    this.shadowRoot.querySelectorAll("ha-selector[data-selector='time']").forEach((selector) => {
      selector.hass = this._hass;
      selector.selector = { time: {} };
      selector.value = selector.dataset.value || "";
    });
    this.shadowRoot.querySelectorAll("ha-select[data-value]").forEach((select) => {
      select.fixedMenuPosition = true;
      select.naturalMenuWidth = true;
      select.value = select.dataset.value;
      select.options = [...select.querySelectorAll("ha-list-item")].map((item) => ({
        value: item.getAttribute("value") || "",
        label: item.textContent.trim(),
      }));
      select.requestUpdate?.("options");
    });
    this.shadowRoot.querySelectorAll("ha-textfield[data-value]").forEach((field) => {
      field.value = field.dataset.value;
    });
    this.shadowRoot.querySelectorAll("ha-switch").forEach((control) => {
      control.checked = control.hasAttribute("checked");
    });
    this.shadowRoot
      .querySelectorAll("[data-path], [data-color-toggle], [data-override-toggle]")
      .forEach((control) => this._bindControl(control));
  }

  _bindControl(control) {
    const handler = (event) => this._handleControlInput(control, event);
    if (control.localName === "ha-switch") {
      control.addEventListener("change", handler);
      return;
    }
    if (control.localName === "ha-select") {
      control.addEventListener("selected", handler);
      control.addEventListener("closed", (event) => event.stopPropagation());
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

  _renderSchedule(title, path, fallback) {
    const schedule = getPath(this._config, path) || fallback;
    const type = schedule.type || "fixed_time";
    return `
      <section class="schedule-card" aria-labelledby="${path.replaceAll(".", "-")}-title">
        <div class="section-title">
          <h3 id="${path.replaceAll(".", "-")}-title">${escapeHtml(title)}</h3>
        </div>
        <div class="field-grid compact">
          <ha-select
            label="Schedule"
            data-path="${path}.type"
            data-render-on-change="true"
            data-value="${escapeHtml(type)}"
          >${optionHtml(SCHEDULE_TYPES, type)}</ha-select>
        ${type === "fixed_time" ? `
          <ha-selector
            label="Time"
            data-value="${escapeHtml(schedule.at || "06:00:00")}" 
            data-path="${path}.at"
            data-selector="time"
          ></ha-selector>
        ` : `
          <ha-select
            label="Sun Event"
            data-path="${path}.event"
            data-value="${escapeHtml(schedule.event || "civil_dusk")}"
          >${optionHtml(SUN_EVENTS, schedule.event || "civil_dusk")}</ha-select>
        `}
        </div>
      </section>
    `;
  }

  _renderSetting(title, description, controlHtml, options = {}) {
    return `
      <div class="setting-row ${options.slim ? "slim" : ""}">
        <div class="setting-copy">
          <div class="setting-title">${escapeHtml(title)}</div>
          <div class="setting-description">${escapeHtml(description)}</div>
        </div>
        <div class="setting-control">${controlHtml}</div>
      </div>
    `;
  }

  _renderGlobal() {
    const global = this._config.global;
    return `
      <ha-card>
        <div class="card-header">
          <div>
            <h2>Global Defaults</h2>
            <p>Used by every light unless a light overrides them.</p>
          </div>
        </div>
        <div class="card-content">
          <div class="two-col">
            ${this._renderSchedule("Dimming", "global.dim_schedule", DEFAULT_CONFIG.global.dim_schedule)}
            ${this._renderSchedule("Brightening", "global.brighten_schedule", DEFAULT_CONFIG.global.brighten_schedule)}
          </div>
          <div class="settings-list">
            ${this._renderSetting("Ramp Duration", "How long each brightness transition should take.", `
              <ha-textfield
                label="Minutes"
                type="number"
                min="1"
                max="720"
                inputmode="numeric"
                suffix="min"
                data-value="${durationToMinutes(global.ramp_duration)}"
                data-path="global.ramp_duration"
                data-duration="minutes"
              ></ha-textfield>
            `)}
            ${this._renderSetting("Override Resume", "Choose how manual changes return to Dimsome control.", `
              <ha-select
                label="Mode"
                data-path="global.override_resume_mode"
                data-value="${escapeHtml(global.override_resume_mode || "manual_only")}"
              >${optionHtml(RESUME_MODES, global.override_resume_mode || "manual_only")}</ha-select>
            `)}
            ${this._renderSetting("Grace Period", "Delay before automatic resume after a manual change.", `
              <ha-textfield
                label="Minutes"
                type="number"
                min="1"
                max="720"
                inputmode="numeric"
                suffix="min"
                data-value="${durationToMinutes(global.override_grace_period, 15)}"
                data-path="global.override_grace_period"
                data-duration="minutes"
              ></ha-textfield>
            `)}
            ${this._renderSetting("Split Brightness & Color Calls", "Enable by default for lights that reject combined brightness/color updates.", `
              <ha-switch aria-label="Split Brightness &amp; Color Calls" ${global.split_turn_on_calls ? "checked" : ""} data-path="global.split_turn_on_calls"></ha-switch>
            `)}
          </div>
        </div>
      </ha-card>
    `;
  }

  _renderLight(light, index) {
    const state = this._lightStates[light.entity_id] || {};
    const hasColor = Boolean(light.min_color && light.max_color);
    const hasOverrides = Boolean(light.dim_schedule || light.brighten_schedule || light.ramp_duration || light.override_resume_mode);
    const entityName = formatEntityName(state, light.entity_id);
    return `
      <ha-card class="light-card">
        <div class="card-header light-header">
          <div class="entity-title">
            <div class="entity-icon" aria-hidden="true">
              ${light.entity_id ? `<ha-state-icon data-entity-id="${escapeHtml(light.entity_id)}"></ha-state-icon>` : `<ha-icon icon="mdi:lightbulb-outline"></ha-icon>`}
            </div>
            <div>
              <h2>${escapeHtml(entityName)}</h2>
              <p class="status-line">${escapeHtml(this._statusText(light))}</p>
            </div>
          </div>
          <div class="actions">
            <ha-button variant="neutral" data-action="resume" data-entity-id="${escapeHtml(light.entity_id || "")}" ${light.entity_id ? "" : "disabled"}>Resume</ha-button>
            <ha-button variant="warning" class="danger" data-action="remove-light" data-index="${index}">Remove</ha-button>
          </div>
        </div>
        <div class="card-content">
          <div class="field-grid">
            <ha-entity-picker
              label="Light Entity"
              helper="Pick a light controlled by Dimsome."
              allow-custom-entity
              data-value="${escapeHtml(light.entity_id || "")}"
              data-path="lights.${index}.entity_id"
            ></ha-entity-picker>
            <ha-textfield
              label="Minimum Brightness"
              type="number"
              min="1"
              max="100"
              inputmode="numeric"
              suffix="%"
              data-value="${light.min_brightness_pct ?? 10}"
              data-number="int"
              data-path="lights.${index}.min_brightness_pct"
            ></ha-textfield>
            <ha-textfield
              label="Maximum Brightness"
              type="number"
              min="1"
              max="100"
              inputmode="numeric"
              suffix="%"
              data-value="${light.max_brightness_pct ?? 80}"
              data-number="int"
              data-path="lights.${index}.max_brightness_pct"
            ></ha-textfield>
          </div>
          <div class="settings-list compact-list">
            ${this._renderSetting("Split Brightness & Color Calls", "Use separate service calls for this light.", `
              <ha-switch aria-label="Split Brightness &amp; Color Calls" ${light.split_turn_on_calls ? "checked" : ""} data-path="lights.${index}.split_turn_on_calls"></ha-switch>
            `, { slim: true })}
            ${this._renderSetting("Adjust Color Temperature", "Set a Kelvin range during the ramp.", `
              <ha-switch aria-label="Adjust Color Temperature" ${hasColor ? "checked" : ""} data-color-toggle="${index}"></ha-switch>
            `, { slim: true })}
          </div>
          ${hasColor ? `
            <div class="field-grid inline-section">
              <ha-textfield
                label="Minimum Color Temperature"
                type="number"
                min="1000"
                max="12000"
                step="50"
                inputmode="numeric"
                suffix="K"
                data-value="${light.min_color?.value ?? 2200}"
                data-number="int"
                data-path="lights.${index}.min_color.value"
              ></ha-textfield>
              <ha-textfield
                label="Maximum Color Temperature"
                type="number"
                min="1000"
                max="12000"
                step="50"
                inputmode="numeric"
                suffix="K"
                data-value="${light.max_color?.value ?? 4000}"
                data-number="int"
                data-path="lights.${index}.max_color.value"
              ></ha-textfield>
            </div>
          ` : ""}
          <div class="settings-list compact-list">
            ${this._renderSetting("Override Global Timing", "Use a separate schedule and resume behavior for this light.", `
              <ha-switch aria-label="Override Global Timing" ${hasOverrides ? "checked" : ""} data-override-toggle="${index}"></ha-switch>
            `, { slim: true })}
          </div>
        ${hasOverrides ? `
          <section class="override-box" aria-label="Timing Overrides">
            <div class="two-col">
              ${this._renderSchedule("Dimming Override", `lights.${index}.dim_schedule`, this._config.global.dim_schedule)}
              ${this._renderSchedule("Brightening Override", `lights.${index}.brighten_schedule`, this._config.global.brighten_schedule)}
            </div>
            <div class="field-grid inline-section">
              <ha-textfield
                label="Ramp Duration"
                type="number"
                min="1"
                max="720"
                inputmode="numeric"
                suffix="min"
                data-value="${durationToMinutes(light.ramp_duration, durationToMinutes(this._config.global.ramp_duration))}"
                data-path="lights.${index}.ramp_duration"
                data-duration="minutes"
              ></ha-textfield>
              <ha-select
                label="Override Resume"
                data-path="lights.${index}.override_resume_mode"
                data-value="${escapeHtml(light.override_resume_mode || this._config.global.override_resume_mode || "manual_only")}"
              >${optionHtml(RESUME_MODES, light.override_resume_mode || this._config.global.override_resume_mode || "manual_only")}</ha-select>
              <ha-textfield
                label="Grace Period"
                type="number"
                min="1"
                max="720"
                inputmode="numeric"
                suffix="min"
                data-value="${durationToMinutes(light.override_grace_period, durationToMinutes(this._config.global.override_grace_period, 15))}"
                data-path="lights.${index}.override_grace_period"
                data-duration="minutes"
              ></ha-textfield>
            </div>
          </section>
        ` : ""}
        </div>
      </ha-card>
    `;
  }

  _render() {
    if (!this.shadowRoot) return;
    if (!this._loaded) {
      this.shadowRoot.innerHTML = `${this._styles()}<main><ha-card><div class="center-state"><ha-circular-progress active></ha-circular-progress><p>Loading Dimsome…</p></div></ha-card></main>`;
      return;
    }

    if (!this._configured) {
      this.shadowRoot.innerHTML = `${this._styles()}<main><ha-card><div class="card-content"><h1>Dimsome</h1><p>Dimsome is not set up yet. Add it from Settings &gt; Devices &amp; Services &gt; Add Integration &gt; Dimsome.</p><p><a href="/config/integrations">Open Integrations</a></p></div></ha-card></main>`;
      return;
    }

    this.shadowRoot.innerHTML = `
      ${this._styles()}
      <main>
        <header class="page-header">
          <div>
            <h1>Dimsome</h1>
            <p>Configure adaptive dimming without leaving Home Assistant.</p>
          </div>
          <div class="toolbar">
            <ha-button variant="neutral" data-action="reload">Reload</ha-button>
            <ha-button variant="neutral" data-action="resume">Resume All</ha-button>
            <ha-button data-action="save" ${this._saving ? "disabled loading" : ""}>Save</ha-button>
          </div>
        </header>
        <div class="announcements" aria-live="polite">
          ${this._error ? `<ha-alert alert-type="error">${escapeHtml(this._error)}</ha-alert>` : ""}
          ${this._message ? `<ha-alert alert-type="success">${escapeHtml(this._message)}</ha-alert>` : ""}
        </div>
        <section class="panel-grid">
          ${this._renderGlobal()}
        </section>
        <section class="section-head" aria-labelledby="lights-title">
          <div>
            <h2 id="lights-title">Lights</h2>
            <p>${this._config.lights.length} configured</p>
          </div>
          <ha-button variant="neutral" data-action="add-light">Add Light</ha-button>
        </section>
        <section class="lights-list">
          ${this._config.lights.map((light, index) => this._renderLight(light, index)).join("") || `<ha-card><div class="center-state empty"><ha-icon icon="mdi:lightbulb-outline"></ha-icon><h2>No Lights Yet</h2><p>Add a light to start adaptive dimming.</p><ha-button data-action="add-light">Add Light</ha-button></div></ha-card>`}
        </section>
      </main>
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

        main {
          box-sizing: border-box;
          max-width: 1120px;
          margin: 0 auto;
          padding: 24px max(16px, env(safe-area-inset-right)) 40px max(16px, env(safe-area-inset-left));
        }

        h1,
        h2,
        h3,
        p {
          margin: 0;
        }

        h1 {
          font-size: 32px;
          font-weight: 600;
          letter-spacing: -0.02em;
          line-height: 1.15;
        }

        h2 {
          font-size: 20px;
          font-weight: 500;
          line-height: 1.3;
        }

        h3 {
          font-size: 16px;
          font-weight: 500;
          line-height: 1.3;
        }

        p,
        .status-line {
          color: var(--secondary-text-color);
          margin-top: 4px;
        }

        a {
          color: var(--primary-color);
        }

        ha-card,
        ha-alert {
          display: block;
        }

        ha-textfield,
        ha-select,
        ha-selector,
        ha-entity-picker {
          width: 100%;
        }

        ha-button,
        ha-switch {
          touch-action: manipulation;
        }

        .page-header,
        .section-head,
        .card-header,
        .light-header {
          display: flex;
          align-items: flex-start;
          justify-content: space-between;
          gap: 16px;
        }

        .page-header {
          margin-bottom: 20px;
        }

        .toolbar,
        .actions {
          display: flex;
          flex-wrap: wrap;
          justify-content: flex-end;
          gap: 8px;
        }

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
          margin: 24px 0 12px;
        }

        .card-header {
          padding: 16px 16px 0;
        }

        .card-content {
          padding: 16px;
        }

        .two-col,
        .field-grid {
          display: grid;
          grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
          gap: 16px;
        }

        .field-grid.compact {
          grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
        }

        .schedule-card,
        .override-box {
          border: 1px solid var(--divider-color);
          border-radius: var(--ha-card-border-radius, 12px);
          padding: 16px;
        }

        .section-title {
          margin-bottom: 12px;
        }

        .settings-list {
          display: grid;
          gap: 0;
          margin-top: 8px;
        }

        .setting-row {
          align-items: center;
          border-top: 1px solid var(--divider-color);
          display: grid;
          gap: 16px;
          grid-template-columns: minmax(0, 1fr) minmax(220px, 280px);
          min-height: 72px;
          padding: 12px 0;
        }

        .setting-row:first-child {
          border-top: 0;
        }

        .setting-row.slim {
          min-height: 56px;
        }

        .setting-title {
          font-weight: 500;
          line-height: 1.4;
        }

        .setting-description {
          color: var(--secondary-text-color);
          line-height: 1.4;
          margin-top: 2px;
        }

        .setting-control {
          align-items: center;
          display: flex;
          justify-content: flex-end;
          min-width: 0;
        }

        .setting-control ha-switch {
          flex: 0 0 auto;
        }

        .compact-list {
          border-top: 1px solid var(--divider-color);
          margin-top: 16px;
        }

        .inline-section,
        .override-box {
          margin-top: 16px;
        }

        .entity-title {
          display: flex;
          align-items: center;
          gap: 12px;
          min-width: 0;
        }

        .entity-icon {
          align-items: center;
          background: var(--primary-background-color);
          border-radius: 50%;
          display: inline-flex;
          flex: 0 0 40px;
          height: 40px;
          justify-content: center;
          width: 40px;
        }

        .entity-title h2,
        .status-line {
          overflow-wrap: anywhere;
        }

        .status-line {
          font-variant-numeric: tabular-nums;
        }

        .danger {
          --mdc-theme-primary: var(--error-color);
        }

        .center-state {
          align-items: center;
          color: var(--secondary-text-color);
          display: grid;
          gap: 12px;
          justify-items: center;
          padding: 48px 16px;
          text-align: center;
        }

        .center-state h2 {
          color: var(--primary-text-color);
        }

        .empty ha-icon {
          color: var(--secondary-text-color);
          --mdc-icon-size: 40px;
        }

        :focus-visible {
          outline: 2px solid var(--primary-color);
          outline-offset: 3px;
        }

        @media (max-width: 720px) {
          main {
            padding-top: 16px;
          }

          .page-header,
          .section-head,
          .card-header,
          .light-header {
            display: grid;
          }

          .toolbar,
          .actions {
            justify-content: flex-start;
          }

          .field-grid,
          .field-grid.compact,
          .two-col {
            grid-template-columns: 1fr;
          }

          .setting-row {
            align-items: stretch;
            grid-template-columns: 1fr;
            gap: 10px;
          }

          .setting-control {
            justify-content: flex-start;
          }
        }
      </style>
    `;
  }
}

customElements.define("dimsome-panel", DimsomePanel);
