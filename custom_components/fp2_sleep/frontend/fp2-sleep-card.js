/*
 * Aqara FP2 Sleep card (custom:fp2-sleep-card).
 *
 * One night at a glance: time asleep, a hypnogram of the night's stages by
 * Aqara's own times, the stage breakdown, and heart rate, breathing and
 * movement through the night. Finds the FP2 Sleep integration's entities by
 * itself; also works with the SleepRadar app's aqara_fp2_sleep_* entities.
 */

const CARD_VERSION = "0.1.0";
const DOMAIN = "fp2_sleep";
const DEFAULT_PREFIX = "aqara_fp2_sleep";
const HISTORY_REFRESH_MS = 5 * 60 * 1000;
const NIGHT_START_HOUR = 18;

// Stage keys as the integration's sleep_stage sensor reports them, with
// Aqara's raw codes (SleepRadar's sleep_state) mapped onto them.
const CODE_STAGES = { 0: "out_of_bed", 1: "in_bed", 2: "awake", 3: "rem", 4: "light", 5: "deep" };
const STAGE_LABELS = {
  out_of_bed: "Out of bed",
  in_bed: "In bed",
  awake: "Awake",
  rem: "REM",
  light: "Light",
  deep: "Deep",
};
const ASLEEP = new Set(["rem", "light", "deep"]);
// Hypnogram rows, top to bottom. In bed counts as awake.
const ROWS = ["awake", "rem", "light", "deep"];
const ROW_OF = { awake: 0, in_bed: 0, rem: 1, light: 2, deep: 3 };

// Entity keys (the integration's translation keys and SleepRadar's suffixes).
const KEYS = [
  "heart_rate",
  "respiration_rate",
  "body_movement",
  "sleep_stage",
  "sleep_state",
  "night_asleep",
  "night_light_sleep",
  "night_deep_sleep",
  "night_rem_sleep",
  "fell_asleep",
  "woke_up",
  "fp2_mode",
  "fp2_online",
  "connection_problem",
];
const BINARY_KEYS = new Set(["fp2_online", "connection_problem"]);

// ---------------------------------------------------------------- helpers

function stageOf(raw) {
  if (raw === undefined || raw === null) return null;
  const text = String(raw).trim();
  if (text in STAGE_LABELS) return text;
  if (/^\d+$/.test(text)) return CODE_STAGES[Number(text)] || null;
  return null;
}

function formatDuration(hours) {
  if (hours === null || hours === undefined || !isFinite(hours)) return "–";
  const minutes = Math.round(hours * 60);
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  if (h === 0) return `${m}m`;
  return m === 0 ? `${h}h` : `${h}h ${m}m`;
}

function hour12(hass) {
  const pref = hass && hass.locale && hass.locale.time_format;
  if (pref === "12") return true;
  if (pref === "24") return false;
  const lang = (hass && hass.locale && hass.locale.language) || undefined;
  try {
    return new Intl.DateTimeFormat(lang, { hour: "numeric" }).resolvedOptions().hour12 !== false;
  } catch (_err) {
    return true;
  }
}

function timeZoneOf(hass) {
  const locale = hass && hass.locale;
  const config = hass && hass.config;
  if (locale && locale.time_zone === "server" && config && config.time_zone) return config.time_zone;
  return undefined;
}

function formatTime(date, hass, withMinutes = true) {
  if (!date) return "–";
  const lang = (hass && hass.locale && hass.locale.language) || undefined;
  const options = { hour: "numeric", hour12: hour12(hass), timeZone: timeZoneOf(hass) };
  if (withMinutes) options.minute = "2-digit";
  try {
    return new Intl.DateTimeFormat(lang, options).format(date);
  } catch (_err) {
    return date.toLocaleTimeString();
  }
}

function relative(date, now) {
  if (!date) return null;
  const seconds = Math.max(0, Math.round((now - date) / 1000));
  if (seconds < 45) return "just now";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} h ago`;
  return `${Math.round(hours / 24)} d ago`;
}

function nightStart(now) {
  const start = new Date(now);
  start.setHours(NIGHT_START_HOUR, 0, 0, 0);
  if (start > now) start.setDate(start.getDate() - 1);
  return start;
}

/*
 * The night from its stage changes ([{time: Date, stage}], oldest first):
 * segments for the hypnogram, the time in each stage, and when sleep started
 * and ended. The latest change is open: it runs to `now` and is marked
 * unconfirmed, as the integration does not count it yet either.
 */
function analyzeNight(changes, now) {
  const segments = [];
  for (let i = 0; i < changes.length; i += 1) {
    const end = i + 1 < changes.length ? changes[i + 1].time : now;
    if (end <= changes[i].time) continue;
    segments.push({ stage: changes[i].stage, start: changes[i].time, end, open: i + 1 === changes.length });
  }
  const firstSleep = segments.findIndex((s) => ASLEEP.has(s.stage));
  let lastSleep = -1;
  segments.forEach((s, i) => {
    if (ASLEEP.has(s.stage)) lastSleep = i;
  });
  const minutes = { light: 0, deep: 0, rem: 0, awake: 0 };
  let wakeUps = 0;
  if (firstSleep >= 0) {
    for (let i = firstSleep; i <= lastSleep; i += 1) {
      const s = segments[i];
      if (s.open) continue;
      const span = (s.end - s.start) / 60000;
      if (ASLEEP.has(s.stage)) minutes[s.stage] += span;
      else minutes.awake += span;
      if (!ASLEEP.has(s.stage) && i > firstSleep && ASLEEP.has(segments[i - 1].stage)) wakeUps += 1;
    }
  }
  const sleepStart = firstSleep >= 0 ? segments[firstSleep].start : null;
  const last = lastSleep >= 0 ? segments[lastSleep] : null;
  const stillAsleep = Boolean(last && last.open);
  const sleepEnd = last && !stillAsleep ? last.end : null;
  const bedStart = segments.length ? (segments.find((s) => s.stage !== "out_of_bed") || {}).start || null : null;
  const asleepMin = minutes.light + minutes.deep + minutes.rem;
  const inBedMin = asleepMin + minutes.awake;
  return {
    segments,
    minutes,
    asleepHours: asleepMin / 60,
    sleepStart,
    sleepEnd,
    stillAsleep,
    wakeUps,
    bedStart,
    efficiency: inBedMin > 0 ? asleepMin / inBedMin : null,
  };
}

/* Stage changes from the integration's `stages` attribute. */
function changesFromAttribute(stages) {
  if (!Array.isArray(stages)) return null;
  const out = [];
  for (const item of stages) {
    const time = new Date(item && item.time);
    const stage = stageOf(item && item.stage);
    if (!isNaN(time) && stage) out.push({ time, stage });
  }
  return out.sort((a, b) => a.time - b.time);
}

/* Stage changes from Home Assistant's history of the stage entity (arrival
 * times, so later than Aqara's by the FP2's upload delay). */
function changesFromHistory(points) {
  const out = [];
  for (const point of points || []) {
    const stage = stageOf(point.s);
    const time = new Date((point.lu || point.lc) * 1000);
    if (!stage || isNaN(time)) continue;
    if (out.length && out[out.length - 1].stage === stage) continue;
    out.push({ time, stage });
  }
  return out;
}

function numericSeries(points) {
  const out = [];
  for (const point of points || []) {
    const value = Number(point.s);
    const time = (point.lu || point.lc) * 1000;
    if (point.s !== "" && point.s !== null && isFinite(value) && isFinite(time)) out.push([time, value]);
  }
  return out;
}

function escapeHtml(text) {
  return String(text).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

// ---------------------------------------------------------------- card

const STYLE = `
  :host {
    display: block;
    --fp2-awake: #ff8a65;
    --fp2-rem: #4dd0e1;
    --fp2-light: #42a5f5;
    --fp2-deep: #5c6bc0;
    --fp2-out: var(--disabled-text-color, #9e9e9e);
    --fp2-heart: #ef5350;
    --fp2-breath: #29b6f6;
    --fp2-move: #ffa726;
    --fp2-muted: var(--secondary-text-color);
    --fp2-line: var(--divider-color, rgba(127, 127, 127, 0.2));
  }
  ha-card { display: block; padding: 16px 16px 14px; container-type: inline-size; overflow: hidden; }
  .head { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
  .title { font-size: 16px; font-weight: 500; color: var(--primary-text-color); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .pills { display: flex; gap: 6px; flex-wrap: wrap; justify-content: flex-end; }
  .pill { display: inline-flex; align-items: center; gap: 6px; padding: 3px 10px 3px 8px; border-radius: 999px;
          font-size: 12px; font-weight: 500; background: color-mix(in srgb, var(--pill) 14%, transparent); color: var(--primary-text-color); white-space: nowrap; }
  .pill .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--pill); }
  .pill.live .dot { animation: pulse 2.4s ease-in-out infinite; }
  .pill.warn { --pill: var(--error-color, #db4437); }
  @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.35; } }
  @media (prefers-reduced-motion: reduce) { .pill.live .dot { animation: none; } }

  .hero { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin: 14px 0 6px; }
  .big { font-size: 34px; line-height: 1.1; font-weight: 600; letter-spacing: -0.5px; color: var(--primary-text-color); font-variant-numeric: tabular-nums; }
  .big small { font-size: 15px; font-weight: 500; color: var(--fp2-muted); margin-left: 6px; letter-spacing: 0; }
  .sub { margin-top: 4px; font-size: 13px; color: var(--fp2-muted); }
  .times { flex: none; display: grid; grid-template-columns: auto auto; column-gap: 12px; row-gap: 4px; align-items: baseline; }
  .times span { font-size: 12px; color: var(--fp2-muted); }
  .times b { font-size: 15px; font-weight: 600; color: var(--primary-text-color); text-align: right; font-variant-numeric: tabular-nums; }

  .hypno { margin: 10px 0 4px; }
  .hypno svg { display: block; overflow: visible; }
  .hypno .label { font-size: 11px; fill: var(--fp2-muted); }
  .hypno .grid { stroke: var(--fp2-line); stroke-width: 1; }
  .empty { padding: 26px 0; text-align: center; font-size: 13px; color: var(--fp2-muted); }

  .bar { display: flex; height: 8px; border-radius: 4px; overflow: hidden; margin: 14px 0 10px; background: var(--fp2-line); }
  .bar span { height: 100%; }
  .legend { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 8px; }
  .legend div { min-width: 0; }
  .legend .k { display: flex; align-items: center; gap: 6px; font-size: 12px; color: var(--fp2-muted); }
  .legend .k i { width: 8px; height: 8px; border-radius: 2px; flex: none; }
  .legend b { display: block; margin-top: 2px; font-size: 15px; font-weight: 600; color: var(--primary-text-color); font-variant-numeric: tabular-nums; }
  .legend em { font-style: normal; font-size: 12px; font-weight: 400; color: var(--fp2-muted); margin-left: 4px; }

  .vitals { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; margin-top: 16px; }
  .vital { border-radius: 12px; padding: 10px 10px 6px; background: var(--secondary-background-color, rgba(127, 127, 127, 0.06)); min-width: 0; }
  .vital .k { display: flex; align-items: center; gap: 6px; font-size: 12px; color: var(--fp2-muted); white-space: nowrap; }
  .vital .k ha-icon { --mdc-icon-size: 16px; color: var(--c); }
  .vital .v { margin-top: 2px; font-size: 20px; font-weight: 600; color: var(--primary-text-color); font-variant-numeric: tabular-nums; }
  .vital .v small { font-size: 12px; font-weight: 500; color: var(--fp2-muted); margin-left: 3px; }
  .vital svg { display: block; width: 100%; height: 30px; margin-top: 4px; }
  .vital .r { font-size: 11px; color: var(--fp2-muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

  .foot { display: flex; justify-content: space-between; gap: 8px; margin-top: 12px; font-size: 11px; color: var(--fp2-muted); }

  @container (max-width: 380px) {
    .legend { grid-template-columns: repeat(2, minmax(0, 1fr)); row-gap: 10px; }
    .vitals { grid-template-columns: 1fr; }
    .vital { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1.2fr); align-items: center; column-gap: 12px; }
    .vital .k, .vital .v, .vital .r { grid-column: 1; }
    .vital svg { grid-column: 2; grid-row: 1 / span 3; margin: 0; height: 40px; }
    .big { font-size: 30px; }
    .times { column-gap: 8px; }
    .times b { font-size: 14px; }
  }
`;

class FP2SleepCard extends HTMLElement {
  static getConfigForm() {
    return {
      schema: [
        { name: "name", selector: { text: {} } },
        { name: "device_id", selector: { device: { integration: DOMAIN } } },
        { name: "show_vitals", default: true, selector: { boolean: {} } },
      ],
      computeLabel: (schema) =>
        ({ name: "Title", device_id: "FP2 (optional)", show_vitals: "Show heart rate, breathing and movement" })[schema.name],
    };
  }

  static getStubConfig() {
    return {};
  }

  setConfig(config) {
    if (config && config.entities && typeof config.entities !== "object") {
      throw new Error("entities must map keys to entity ids");
    }
    this._config = { show_vitals: true, ...(config || {}) };
    this._historyKey = null;
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    if (hass.entities !== this._registry || hass.states !== this._lastStates) {
      // hass.states is replaced on every change anywhere; look only at ours.
      this._lastStates = hass.states;
      if (hass.entities !== this._registry || !this._entities) {
        this._registry = hass.entities;
        this._entities = this._findEntities();
      }
      const ids = Object.values(this._entities);
      const changed = !this._seen || ids.length !== this._seen.length || ids.some((id, i) => hass.states[id] !== this._seen[i]);
      this._seen = ids.map((id) => hass.states[id]);
      this._maybeFetchHistory();
      if (changed) this._render();
    }
  }

  getCardSize() {
    return this._config && this._config.show_vitals === false ? 5 : 7;
  }

  getGridOptions() {
    return { columns: 12, min_columns: 6, rows: "auto" };
  }

  connectedCallback() {
    this._tick = setInterval(() => {
      this._maybeFetchHistory();
      this._render();
    }, 60 * 1000);
    if (typeof ResizeObserver !== "undefined") {
      // The hypnogram is drawn at the card's real width, so its text stays
      // readable at any size. 32 px is the card's side padding.
      this._resize = new ResizeObserver((entries) => {
        const width = entries[0] && entries[0].contentRect.width - 32;
        if (width > 0 && Math.abs(width - (this._width || 0)) > 1) {
          this._width = width;
          this._render();
        }
      });
      this._resize.observe(this);
    }
  }

  disconnectedCallback() {
    clearInterval(this._tick);
    if (this._resize) this._resize.disconnect();
  }

  // Entities: the integration's (by device and translation key), then any
  // configured overrides, then <prefix>_<key> for the SleepRadar app.
  _findEntities() {
    const hass = this._hass;
    const config = this._config || {};
    const found = {};
    const registry = (hass && hass.entities) || {};
    const mine = Object.values(registry).filter((e) => e.platform === DOMAIN);
    const deviceId = config.device_id || (mine[0] && mine[0].device_id);
    for (const entry of mine) {
      if (entry.device_id === deviceId && KEYS.includes(entry.translation_key)) found[entry.translation_key] = entry.entity_id;
    }
    this._deviceId = deviceId;
    const prefix = config.entity_prefix || DEFAULT_PREFIX;
    for (const key of KEYS) {
      if (config.entities && config.entities[key]) found[key] = config.entities[key];
      else if (!found[key]) {
        const id = `${BINARY_KEYS.has(key) ? "binary_sensor" : "sensor"}.${prefix}_${key}`;
        if (hass && hass.states[id]) found[key] = id;
      }
    }
    return found;
  }

  _state(key) {
    const id = this._entities && this._entities[key];
    return id && this._hass ? this._hass.states[id] : undefined;
  }

  _number(key) {
    const state = this._state(key);
    const value = state ? Number(state.state) : NaN;
    return state && state.state !== "" && isFinite(value) ? value : null;
  }

  _maybeFetchHistory() {
    const hass = this._hass;
    if (!hass || !hass.callWS || !this._entities) return;
    const now = Date.now();
    const start = nightStart(new Date(now));
    const ids = ["heart_rate", "respiration_rate", "body_movement", "sleep_stage", "sleep_state"]
      .map((k) => this._entities[k])
      .filter(Boolean);
    if (!ids.length) return;
    const key = `${start.getTime()}|${ids.join(",")}`;
    if (this._historyKey === key && now - this._historyAt < HISTORY_REFRESH_MS) return;
    if (this._historyLoading) return;
    this._historyKey = key;
    this._historyAt = now;
    this._historyLoading = true;
    hass
      .callWS({
        type: "history/history_during_period",
        start_time: start.toISOString(),
        end_time: new Date(now).toISOString(),
        entity_ids: ids,
        minimal_response: true,
        no_attributes: true,
        significant_changes_only: false,
      })
      .then((result) => {
        this._history = result || {};
        this._render();
      })
      .catch(() => {
        this._history = this._history || {};
      })
      .finally(() => {
        this._historyLoading = false;
      });
  }

  _changes(now) {
    const asleep = this._state("night_asleep");
    const fromAttribute = asleep && changesFromAttribute(asleep.attributes.stages);
    if (fromAttribute) return { changes: fromAttribute, exact: true };
    const history = this._history || {};
    const id = this._entities.sleep_stage || this._entities.sleep_state;
    const start = nightStart(now);
    const changes = changesFromHistory(history[id]).filter((c) => c.time >= start);
    return { changes, exact: false };
  }

  _render() {
    if (!this._config) return;
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    const hass = this._hass;
    if (!hass) return;
    const now = new Date();
    const { changes, exact } = this._changes(now);
    const night = analyzeNight(changes, now);

    // Prefer the integration's exact totals; fall back to the card's own.
    const totals = {
      asleep: this._number("night_asleep") ?? night.asleepHours,
      light: this._number("night_light_sleep") ?? night.minutes.light / 60,
      deep: this._number("night_deep_sleep") ?? night.minutes.deep / 60,
      rem: this._number("night_rem_sleep") ?? night.minutes.rem / 60,
      awake: night.minutes.awake / 60,
    };
    const fell = this._state("fell_asleep");
    const woke = this._state("woke_up");
    const sleepStart = fell && !isNaN(new Date(fell.state)) ? new Date(fell.state) : night.sleepStart;
    const sleepEnd = woke && !isNaN(new Date(woke.state)) ? new Date(woke.state) : night.sleepEnd;
    const wakeUps = this._state("night_asleep")?.attributes?.awakenings ?? night.wakeUps;

    const title = this._config.name || this._deviceName() || "Sleep";
    this.shadowRoot.innerHTML = `
      <style>${STYLE}</style>
      <ha-card>
        <div class="head">
          <div class="title">${escapeHtml(title)}</div>
          <div class="pills">${this._pills()}</div>
        </div>
        ${this._hero(totals, sleepStart, sleepEnd, wakeUps, night)}
        ${this._hypnogram(night, now, exact)}
        ${this._breakdown(totals)}
        ${this._config.show_vitals === false ? "" : this._vitals()}
        ${this._footer(now, exact)}
      </ha-card>`;
  }

  _deviceName() {
    const device = this._deviceId && this._hass.devices && this._hass.devices[this._deviceId];
    return device ? device.name_by_user || device.name : null;
  }

  _pills() {
    const pills = [];
    const problem = this._state("connection_problem");
    const online = this._state("fp2_online");
    const mode = this._state("fp2_mode");
    if (problem && problem.state === "on") pills.push(`<span class="pill warn"><span class="dot"></span>No connection</span>`);
    else if (online && online.state === "off") pills.push(`<span class="pill warn"><span class="dot"></span>FP2 offline</span>`);
    if (mode && !["sleep_monitoring", "Sleep monitoring", "unknown", "unavailable"].includes(mode.state)) {
      pills.push(`<span class="pill warn"><span class="dot"></span>Not in sleep mode</span>`);
    }
    const stageState = this._state("sleep_stage") || this._state("sleep_state");
    const stage = stageState && stageOf(stageState.state);
    if (stage) {
      const live = ASLEEP.has(stage) ? " live" : "";
      pills.push(
        `<span class="pill${live}" style="--pill: var(--fp2-${stage === "in_bed" ? "awake" : stage === "out_of_bed" ? "out" : stage})"><span class="dot"></span>${STAGE_LABELS[stage]}</span>`,
      );
    }
    return pills.join("");
  }

  _hero(totals, sleepStart, sleepEnd, wakeUps, night) {
    const hass = this._hass;
    const parts = [];
    if (!sleepStart) parts.push("No sleep recorded yet tonight");
    else {
      parts.push(`${wakeUps} wake-up${wakeUps === 1 ? "" : "s"}`);
      if (night.efficiency !== null) parts.push(`${Math.round(night.efficiency * 100)}% efficiency`);
    }
    const times = sleepStart
      ? `<div class="times">
           <span>Fell asleep</span><b>${formatTime(sleepStart, hass)}</b>
           <span>${sleepEnd ? "Woke up" : "Still"}</span><b>${sleepEnd ? formatTime(sleepEnd, hass) : "asleep"}</b>
         </div>`
      : "";
    return `
      <div class="hero">
        <div>
          <div class="big">${formatDuration(totals.asleep)}<small>asleep</small></div>
          <div class="sub" title="Efficiency: time asleep out of the time between falling asleep and waking">${escapeHtml(parts.join(" · "))}</div>
        </div>
        ${times}
      </div>`;
  }

  _hypnogram(night, now, exact) {
    // Leading out-of-bed time is dropped; a trailing one only shows briefly.
    let segments = night.segments.filter((s) => !(s.stage === "out_of_bed" && night.bedStart && s.end <= night.bedStart));
    if (!segments.length || !night.bedStart) {
      return `<div class="hypno"><div class="empty">The night's sleep stages appear here once the FP2 reports them.</div></div>`;
    }
    const lastIn = segments.reduce((i, s, idx) => (s.stage !== "out_of_bed" ? idx : i), -1);
    segments = segments.slice(0, lastIn + 1);
    const hass = this._hass;
    const W = Math.max(240, Math.round(this._width || 460));
    const start = segments[0].start.getTime();
    const end = Math.max(segments[segments.length - 1].end.getTime(), start + 3600 * 1000);
    const left = 44;
    const right = 4;
    const rowH = 26;
    const top = 6;
    const plotBottom = top + ROWS.length * rowH;
    const H = plotBottom + 20;
    const x = (t) => left + ((t - start) / (end - start)) * (W - left - right);
    const y = (row) => top + row * rowH + rowH / 2;
    let svg = "";
    ROWS.forEach((row, i) => {
      svg += `<line class="grid" x1="${left}" x2="${W - right}" y1="${y(i)}" y2="${y(i)}" stroke-dasharray="2 4"/>`;
      svg += `<text class="label" x="0" y="${y(i) + 4}">${STAGE_LABELS[row]}</text>`;
    });
    const spanH = (end - start) / 3600000;
    const pxPerHour = (W - left - right) / spanH;
    const step = [1, 2, 3, 4, 6].find((n) => n * pxPerHour >= 56) || 6;
    const tick = new Date(start);
    tick.setMinutes(0, 0, 0);
    tick.setHours(tick.getHours() + 1);
    while (tick.getTime() < end) {
      if (tick.getHours() % step === 0) {
        const tx = x(tick.getTime());
        svg += `<line class="grid" x1="${tx}" x2="${tx}" y1="${top}" y2="${plotBottom}"/>`;
        svg += `<text class="label" x="${tx}" y="${H - 3}" text-anchor="middle">${escapeHtml(formatTime(tick, hass, false))}</text>`;
      }
      tick.setHours(tick.getHours() + 1);
    }
    let bars = "";
    let links = "";
    let prev = null;
    for (const s of segments) {
      const row = ROW_OF[s.stage];
      if (row === undefined) {
        prev = null;
        continue;
      }
      const x1 = x(s.start.getTime());
      const x2 = Math.max(x1 + 2, x(s.end.getTime()));
      const color = `var(--fp2-${s.stage === "in_bed" ? "awake" : s.stage})`;
      if (prev !== null && prev !== row) {
        links += `<line x1="${x1}" x2="${x1}" y1="${Math.min(y(prev), y(row))}" y2="${Math.max(y(prev), y(row))}" stroke="var(--fp2-line)" stroke-width="1.5"/>`;
      }
      const minutes = Math.round((s.end - s.start) / 60000);
      const tip = `${STAGE_LABELS[s.stage]} · ${formatTime(s.start, hass)} – ${s.open ? "now" : formatTime(s.end, hass)} (${formatDuration(minutes / 60)})${s.open ? ", not confirmed yet" : ""}`;
      bars += `<rect x="${x1.toFixed(1)}" y="${y(row) - 8}" width="${(x2 - x1).toFixed(1)}" height="16" rx="4" fill="${color}"${s.open ? ' fill-opacity="0.45"' : ""}><title>${escapeHtml(tip)}</title></rect>`;
      prev = row;
    }
    const label = `Sleep stages from ${formatTime(new Date(start), hass)} to ${formatTime(new Date(end), hass)}${exact ? "" : ", by arrival time"}`;
    return `<div class="hypno"><svg width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" role="img" aria-label="${escapeHtml(label)}">${svg}${links}${bars}</svg></div>`;
  }

  _breakdown(totals) {
    const parts = [
      ["light", "Light", totals.light],
      ["deep", "Deep", totals.deep],
      ["rem", "REM", totals.rem],
      ["awake", "Awake", totals.awake],
    ];
    const sum = parts.reduce((acc, p) => acc + (p[2] || 0), 0);
    const bar = sum > 0
      ? parts.map(([key, , h]) => (h > 0 ? `<span style="width:${((h / sum) * 100).toFixed(2)}%;background:var(--fp2-${key})"></span>` : "")).join("")
      : "";
    const asleep = totals.light + totals.deep + totals.rem;
    const legend = parts
      .map(([key, label, h]) => {
        const pct = key !== "awake" && asleep > 0 ? `<em>${Math.round((h / asleep) * 100)}%</em>` : "";
        return `<div><div class="k"><i style="background:var(--fp2-${key})"></i>${label}</div><b>${formatDuration(h)}${pct}</b></div>`;
      })
      .join("");
    return `<div class="bar" role="img" aria-label="Stage breakdown">${bar}</div><div class="legend">${legend}</div>`;
  }

  _vitals() {
    const history = this._history || {};
    const tiles = [
      ["heart_rate", "Heart rate", "bpm", "mdi:heart-pulse", "--fp2-heart"],
      ["respiration_rate", "Breathing", "br/min", "mdi:lungs", "--fp2-breath"],
      ["body_movement", "Movement", "", "mdi:run", "--fp2-move"],
    ];
    return `<div class="vitals">${tiles
      .map(([key, label, unit, icon, color]) => {
        const value = this._number(key);
        const series = numericSeries(history[this._entities[key]]);
        let range = "No readings tonight";
        if (series.length) {
          const values = series.map((p) => p[1]);
          const lo = Math.min(...values);
          const hi = Math.max(...values);
          const avg = values.reduce((a, b) => a + b, 0) / values.length;
          range = lo === hi ? `Steady at ${lo}` : `${lo}–${hi} · avg ${Math.round(avg)}`;
        }
        return `<div class="vital" style="--c: var(${color})">
          <div class="k"><ha-icon icon="${icon}"></ha-icon>${label}</div>
          <div class="v">${value === null ? "–" : Math.round(value)}${unit && value !== null ? `<small>${unit}</small>` : ""}</div>
          ${sparkline(series, `var(${color})`)}
          <div class="r">${escapeHtml(range)}</div>
        </div>`;
      })
      .join("")}</div>`;
  }

  _footer(now, exact) {
    let newest = null;
    for (const key of ["heart_rate", "respiration_rate", "sleep_stage", "sleep_state"]) {
      const state = this._state(key);
      const raw = state && state.attributes && state.attributes.measured_at;
      const when = raw ? new Date(raw) : null;
      if (when && !isNaN(when) && (!newest || when > newest)) newest = when;
    }
    const left = newest ? `FP2 reading ${relative(newest, now)}` : "";
    const right = exact ? "Stages by Aqara's times" : "Stages by arrival time";
    return `<div class="foot"><span>${escapeHtml(left)}</span><span>${right}</span></div>`;
  }
}

function sparkline(series, color) {
  if (series.length < 2) return `<svg viewBox="0 0 100 30" preserveAspectRatio="none" aria-hidden="true"></svg>`;
  const t0 = series[0][0];
  const t1 = series[series.length - 1][0];
  const values = series.map((p) => p[1]);
  let lo = Math.min(...values);
  let hi = Math.max(...values);
  if (hi - lo < 1e-9) {
    lo -= 1;
    hi += 1;
  }
  const px = (t) => (t1 > t0 ? ((t - t0) / (t1 - t0)) * 100 : 0);
  const py = (v) => 27 - ((v - lo) / (hi - lo)) * 24;
  // Steps, as the values hold until the next reading.
  let d = `M${px(series[0][0]).toFixed(2)},${py(series[0][1]).toFixed(2)}`;
  for (let i = 1; i < series.length; i += 1) {
    d += `H${px(series[i][0]).toFixed(2)}V${py(series[i][1]).toFixed(2)}`;
  }
  const area = `${d}V30H0Z`;
  return `<svg viewBox="0 0 100 30" preserveAspectRatio="none" aria-hidden="true">
    <path d="${area}" fill="${color}" fill-opacity="0.12"/>
    <path d="${d}" fill="none" stroke="${color}" stroke-width="1.5" vector-effect="non-scaling-stroke" stroke-linejoin="round"/>
  </svg>`;
}

if (typeof customElements !== "undefined" && !customElements.get("fp2-sleep-card")) {
  customElements.define("fp2-sleep-card", FP2SleepCard);
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: "fp2-sleep-card",
    name: "Aqara FP2 Sleep",
    description: "A night's sleep from an Aqara FP2: time asleep, stages, heart rate and breathing.",
    preview: true,
    documentationURL: "https://github.com/kkrankall/aqara-fp2-sleep",
  });
  console.info(`%c FP2-SLEEP-CARD %c ${CARD_VERSION} `, "color:#fff;background:#3949ab;border-radius:3px 0 0 3px", "color:#3949ab;background:#e8eaf6;border-radius:0 3px 3px 0");
}

if (typeof module !== "undefined") {
  module.exports = { analyzeNight, changesFromAttribute, changesFromHistory, formatDuration, stageOf, nightStart, numericSeries };
}
