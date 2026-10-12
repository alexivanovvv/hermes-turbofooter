// TurboFooter for OpenClaw — pure helpers: settings, field rendering, details-page URL (no OpenClaw imports,
// so they run under plain `node --test`).

export const STYLES = ["text", "button", "menu"];
// Hermes-only styles: OpenClaw plugins cannot put a reply keyboard on agent replies.
export const HERMES_ONLY_STYLES = ["keyboard", "placeholder"];
export const KNOWN_FIELDS = ["model", "effort", "ctx", "ctx_tokens", "context_pct", "tokens", "quota_pct", "quota_left",
  "quota_5h", "session_pct", "session_reset"];
export const DEFAULT_MENU_URL = "https://alexivanovvv.github.io/turbofooter/";
export const DEFAULT_MENU_IDLE = "/turbofooter";
export const SEP = " · ";
export const DEFAULTS = Object.freeze({
  enabled: true,
  style: "text",
  fields: ["model", "effort", "ctx", "quota_pct", "quota_left"],
  menu_fields: ["ctx", "quota_pct", "quota_left"],
  menu_url: DEFAULT_MENU_URL,
  menu_data: null,
  menu_idle: DEFAULT_MENU_IDLE,
  separator: SEP,
  timezone: "",
});

const EFFORT_SHORT = { minimal: "min", medium: "med", none: "off" };

/** Defaults ← plugin config (openclaw.json) ← overrides saved by /turbofooter. Invalid values are ignored. */
export function resolveSettings(...layers) {
  const out = { ...DEFAULTS, fields: [...DEFAULTS.fields], menu_fields: [...DEFAULTS.menu_fields] };
  for (const s of layers) {
    if (!s || typeof s !== "object") continue;
    if (typeof s.enabled === "boolean") out.enabled = s.enabled;
    if (STYLES.includes(s.style)) out.style = s.style;
    if (Array.isArray(s.fields) && s.fields.length) out.fields = s.fields.map(String);
    if (Array.isArray(s.menu_fields) && s.menu_fields.length) out.menu_fields = s.menu_fields.map(String);
    if (typeof s.menu_url === "string" && s.menu_url.startsWith("https://")) out.menu_url = s.menu_url;
    if (s.menu_url === null) out.menu_url = DEFAULT_MENU_URL;
    if (typeof s.menu_data === "boolean") out.menu_data = s.menu_data;
    if (typeof s.menu_idle === "string" && s.menu_idle.trim()) out.menu_idle = s.menu_idle.trim().slice(0, 64);
    if (s.menu_idle === null) out.menu_idle = DEFAULT_MENU_IDLE;
    if (typeof s.separator === "string" && s.separator) out.separator = s.separator;
    if (typeof s.timezone === "string" && s.timezone) out.timezone = s.timezone;
  }
  return out;
}

export function parseFields(text) {
  const fields = String(text || "").split(/[\s,]+/).filter(Boolean);
  return fields.length && fields.every((f) => KNOWN_FIELDS.includes(f)) ? fields : null;
}

export function fmtTokens(n) {
  n = Math.trunc(Number(n) || 0);
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(2).replace(/\.?0+$/, "") + "M";
  if (n >= 1000) return `${Math.round(n / 1000)}K`;
  return String(n);
}

/** h:mm until ``resetAt`` (epoch seconds). */
export function fmtLeft(resetAt, now = Date.now()) {
  if (!resetAt) return "";
  const secs = Math.max(0, Math.trunc(resetAt - now / 1000));
  return `${Math.trunc(secs / 3600)}:${String(Math.trunc((secs % 3600) / 60)).padStart(2, "0")}`;
}

export function fmtReset(resetAt, tz) {
  if (!resetAt) return "";
  try {
    const t = new Date(resetAt * 1000).toLocaleTimeString("en-GB", {
      hour: "2-digit", minute: "2-digit", hour12: false, ...(tz ? { timeZone: tz } : {}),
    });
    return "↻" + t;
  } catch {
    return "";
  }
}

export function effortLabel(level) {
  if (!level) return "";
  const l = String(level).toLowerCase();
  return EFFORT_SHORT[l] || l;
}

/**
 * One footer line.
 * turn: { model, thinking (reasoning effort), ctxUsed, ctxTotal, input, output }; quota: { usedPercent, resetAt } | null.
 * compact = the menu-button form (~20 chars visible): tight glyphs, space separator.
 */
export function renderLine(fields, turn, quota, { separator = SEP, timezone = "", compact = false, now } = {}) {
  const parts = [];
  const pct = turn.ctxTotal ? Math.max(0, Math.min(100, Math.round((turn.ctxUsed / turn.ctxTotal) * 100))) : null;
  const qPct = quota && quota.usedPercent != null ? Math.round(quota.usedPercent) : null;
  const left = quota ? fmtLeft(quota.resetAt, now) : "";
  for (const f of fields) {
    let val = "";
    if (f === "model") val = turn.model || "";
    else if (f === "effort") val = effortLabel(turn.thinking);
    else if (f === "ctx") val = pct == null ? "" : compact ? `🅲${pct}%` : `🅲 ${pct}%`;
    else if (f === "ctx_tokens") val = turn.ctxTotal ? `🅲 ${fmtTokens(turn.ctxUsed)}/${fmtTokens(turn.ctxTotal)}` : "";
    else if (f === "context_pct") val = pct == null ? "" : (fields.includes("session_pct") ? "ctx " : "") + `${pct}%`;
    else if (f === "tokens") val = turn.input || turn.output ? `↑${fmtTokens(turn.input)} ↓${fmtTokens(turn.output)}` : "";
    else if (f === "quota_pct") val = qPct == null ? "" : compact ? `⏱${qPct}%` : `⏱ 5h: ${qPct}%`;
    else if (f === "quota_left") val = !left ? "" : compact ? `↻${left}` : `↻ ${left}`;
    else if (f === "quota_5h") val = qPct == null ? "" : `⏱ ${qPct}%` + (left ? ` (↻ ${left} /5h)` : "");
    else if (f === "session_pct") val = qPct == null ? "" : `5h ${qPct}%`;
    else if (f === "session_reset") val = quota ? fmtReset(quota.resetAt, timezone) : "";
    if (val) parts.push(val);
  }
  return parts.join(compact ? " " : separator);
}

/** Menu web-app URL; the built-in details page (or menu_data: true) gets the numbers in the query string.
 *  Query, not fragment: Telegram appends its launch data to the fragment. */
export function menuUrl(settings, turn, quota, nowSec = Math.trunc(Date.now() / 1000)) {
  const url = settings.menu_url || DEFAULT_MENU_URL;
  const send = settings.menu_data ?? url === DEFAULT_MENU_URL;
  if (!send) return url;
  const q = {
    m: turn.model || "", e: effortLabel(turn.thinking), cu: turn.ctxUsed || "", ct: turn.ctxTotal || "",
    q: quota && quota.usedPercent != null ? Math.round(quota.usedPercent) : "",
    r: quota && quota.resetAt ? Math.trunc(quota.resetAt) : "", ts: nowSec,
  };
  const qs = new URLSearchParams(Object.entries(q).filter(([, v]) => v !== "" && v != null).map(([k, v]) => [k, String(v)]));
  return url + (url.includes("?") ? "&" : "?") + qs.toString();
}

/** Telegram chat id out of an OpenClaw target ("telegram:123", "telegram:group:-100…:topic:5", "123"). */
export function telegramChatId(target) {
  const m = String(target ?? "").match(/-?\d{5,}/);
  return m ? m[0] : "";
}
