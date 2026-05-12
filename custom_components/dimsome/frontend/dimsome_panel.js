const DEFAULT_CONFIG = {
  global: {
    dim_schedule: { type: "civil_sun", event: "civil_dusk" },
    brighten_schedule: { type: "fixed_time", at: "06:00:00" },
    ramp_duration: "01:00:00",
    override_resume_mode: "manual_only",
    override_grace_period: "00:15:00",
    split_turn_on_calls: false,
    apply_on_recovered_on: true,
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
    this._error = "";
    this._message = "";
    this._addDialogOpen = false;
    this._addDraft = { ...ADD_DRAFT_DEFAULT };
    this._addError = "";
    this._pendingScrollToTop = false;
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
    this.shadowRoot?.querySelectorAll("ha-settings-row").forEach((row) => {
      row.narrow = narrow;
    });
  }

  get narrow() {
    return this._narrow;
  }

  set route(value) {
    this._route = value;
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
    if (event.detail?.value !== undefined) return event.detail.value ?? "";
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

  _hydrateNativeComponents() {
    if (!this.shadowRoot) return;

    // Menu button — native sidebar toggle for narrow screens
    const menuBtn = this.shadowRoot.querySelector("ha-menu-button");
    if (menuBtn) {
      menuBtn.hass = this._hass;
      menuBtn.narrow = this._narrow;
    }

    // ha-settings-row uses the panel's narrow prop to stack heading/control.
    this.shadowRoot.querySelectorAll("ha-settings-row").forEach((row) => {
      row.narrow = this._narrow;
    });

    // Icon button SVG paths
    this.shadowRoot.querySelectorAll("ha-icon-button[data-action]").forEach((btn) => {
      const action = btn.dataset.action;
      if (action === "reload") btn.path = MDI_REFRESH;
      if (action === "resume") btn.path = MDI_PLAY_CIRCLE;
      if (action === "save") btn.path = MDI_CONTENT_SAVE;
      if (action === "remove-light") btn.path = MDI_DELETE;
      if (action === "open-add-dialog") btn.path = MDI_PLUS;
    });

    // Entity pickers
    this.shadowRoot.querySelectorAll("ha-entity-picker").forEach((picker) => {
      picker.hass = this._hass;
      picker.includeDomains = ["light"];
      picker.value = picker.dataset.value || "";
    });

    // State icons
    this.shadowRoot.querySelectorAll("ha-state-icon").forEach((icon) => {
      icon.hass = this._hass;
      icon.stateObj = this._hass?.states?.[icon.dataset.entityId];
    });

    // Time selectors
    this.shadowRoot.querySelectorAll("ha-selector[data-selector='time']").forEach((selector) => {
      selector.hass = this._hass;
      selector.selector = { time: {} };
      selector.value = selector.dataset.value || "";
    });

    // Native <select> elements drive themselves via the `selected` option
    // attribute, so they need no hydration.

    // Text fields
    this.shadowRoot.querySelectorAll("ha-textfield[data-value]").forEach((field) => {
      field.value = field.dataset.value;
    });

    // Switches
    this.shadowRoot.querySelectorAll("ha-switch").forEach((control) => {
      control.checked = control.hasAttribute("checked");
    });

    // Dialog lifecycle — handles ESC / scrim / programmatic close.
    const dialog = this.shadowRoot.querySelector("ha-dialog");
    if (dialog && !dialog._dimsomeBound) {
      dialog._dimsomeBound = true;
      dialog.addEventListener("closed", () => {
        if (this._addDialogOpen) this._closeAddDialog();
      });
    }

    // Bind all data controls
    this.shadowRoot
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
                data-value="${escapeHtml(schedule.at || "06:00:00")}"
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
      <ha-settings-row>
        <span slot="heading">${escapeHtml(title)}</span>
        <span slot="description">${escapeHtml(description)}</span>
        ${controlHtml}
      </ha-settings-row>
    `;
  }

  _renderGlobal() {
    const global = this._config.global;
    return `
      <ha-card header="Global Defaults">
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
            ${this._renderSetting("Override Resume", "Choose how manual changes return to Dimsome control.", selectHtml({
              path: "global.override_resume_mode",
              value: global.override_resume_mode || "manual_only",
              options: RESUME_MODES,
            }))}
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
      <ha-card>
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
              <ha-textfield
                type="number"
                min="1"
                max="100"
                inputmode="numeric"
                suffix="%"
                data-value="${light.min_brightness_pct ?? 10}"
                data-number="int"
                data-path="lights.${index}.min_brightness_pct"
              ></ha-textfield>
            `)}
            ${this._renderField("Maximum Brightness", `
              <ha-textfield
                type="number"
                min="1"
                max="100"
                inputmode="numeric"
                suffix="%"
                data-value="${light.max_brightness_pct ?? 80}"
                data-number="int"
                data-path="lights.${index}.max_brightness_pct"
              ></ha-textfield>
            `)}
          </div>
          <div class="settings-list">
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
              <ha-textfield
                label="Seconds"
                type="number"
                min="0"
                max="30"
                step="0.1"
                inputmode="decimal"
                suffix="s"
                data-value="${durationToSeconds(light.settle_delay)}"
                data-path="lights.${index}.settle_delay"
                data-number="float"
              ></ha-textfield>
            `)}
            ${this._renderSetting("Adjust Color Temperature", "Set a Kelvin range during the ramp.", `
              <ha-switch
                aria-label="Adjust Color Temperature"
                ${hasColor ? "checked" : ""}
                data-color-toggle="${index}"
              ></ha-switch>
            `)}
          </div>
          ${hasColor ? `
            <div class="field-grid top-gap">
              ${this._renderField("Minimum Brightness Color Temperature", `
                <ha-textfield
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
              `)}
              ${this._renderField("Maximum Brightness Color Temperature", `
                <ha-textfield
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
              `)}
            </div>
          ` : ""}
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
                ${this._renderField("Override Resume", selectHtml({
                  path: `lights.${index}.override_resume_mode`,
                  value: light.override_resume_mode || this._config.global.override_resume_mode || "manual_only",
                  options: RESUME_MODES,
                }))}
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
            </div>
          ` : ""}
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
              label="Resume all lights"
              title="Resume All"
              data-action="resume"
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
            <ha-textfield
              label="Min Brightness"
              type="number"
              min="1"
              max="100"
              inputmode="numeric"
              suffix="%"
              data-value="${this._addDraft.min_brightness_pct ?? 10}"
              data-draft-path="min_brightness_pct"
              data-number="int"
            ></ha-textfield>
            <ha-textfield
              label="Max Brightness"
              type="number"
              min="1"
              max="100"
              inputmode="numeric"
              suffix="%"
              data-value="${this._addDraft.max_brightness_pct ?? 80}"
              data-draft-path="max_brightness_pct"
              data-number="int"
            ></ha-textfield>
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

        <section class="panel-grid">
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

        ha-textfield,
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

        ha-button,
        ha-switch,
        ha-icon-button {
          touch-action: manipulation;
        }

        .card-content {
          padding: 16px;
        }

        /* ── Settings rows ───────────────────────────────────────────── */
        /* ha-settings-row owns its internal grid; we only manage rhythm
           and the divider between consecutive rows. */
        .settings-list {
          margin-top: 8px;
        }

        ha-settings-row {
          --paper-item-body-two-line-min-height: 0;
          border-top: 1px solid var(--divider-color);
          padding: 4px 0;
        }

        ha-settings-row:first-child {
          border-top: none;
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

        /* ── Mobile ──────────────────────────────────────────────────── */
        @media (max-width: 720px) {
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
