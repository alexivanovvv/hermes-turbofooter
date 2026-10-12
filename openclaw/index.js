// TurboFooter for OpenClaw — runtime footer (model, reasoning effort, context, Codex 5h quota) on agent replies:
// in the text (any channel), as an inline button under the reply, or in the Telegram chat menu button.
// Command: /turbofooter.
//
// Flow: reply_payload_sending (kind "final") carries usageState — model, effort, context occupancy — for live
// replies; the line is added once per run. Fail-open: any error leaves the reply untouched.

import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import {
  DEFAULT_MENU_IDLE, DEFAULT_MENU_URL, HERMES_ONLY_STYLES, KNOWN_FIELDS, STYLES, menuUrl, parseFields, renderLine,
  resolveSettings, telegramChatId,
} from "./footer.js";

const ID = "turbofooter";
const NS = "tf"; // interactive callback namespace: "tf:hide" removes the footer button
const USAGE_TTL_MS = 60 * 1000;
const USAGE_HELP = [
  "`/turbofooter` — runtime footer settings",
  "`/turbofooter status` · `on` · `off`",
  "Style: `/turbofooter text|button|menu`",
  "Fields: `/turbofooter fields model,effort,ctx,quota_pct,quota_left`",
  "Menu button fields: `/turbofooter menu-fields ctx,quota_pct,quota_left`",
  "Menu button link: `/turbofooter url https://…` or `/turbofooter url default`",
  "Menu button when idle: `/turbofooter idle <text>` · `idle restore` · `idle default`",
].join("\n");

export default {
  id: ID,
  name: "TurboFooter",
  description: "Runtime footer: model, effort, context, Codex 5h quota; text, button or Telegram menu button",
  register(api) {
    const log = api.logger || console;
    const store = jsonFile(path.join(resolveStateDir(api), "plugin-data", ID, "state.json"));
    const footered = new Set(); // runIds that already got their footer (a reply can have several payloads)
    const settings = () => resolveSettings(api.pluginConfig, store.read().settings);

    api.on("reply_payload_sending", async (event, ctx) => {
      try {
        const s = settings();
        const u = event?.usageState;
        const payload = event?.payload;
        if (!s.enabled || event?.kind !== "final" || !u || !payload || payload.isStatusNotice) return;
        if (!payload.text && !payload.presentation) return; // media-only: nothing to hang the footer on
        const runKey = event.runId || ctx?.runId || `${event.sessionKey || ctx?.sessionKey}:${u.sessionId}`;
        if (footered.has(runKey)) return;
        footered.add(runKey);
        if (footered.size > 500) footered.delete(footered.values().next().value);

        const turn = turnData(u);
        const quota = await codexUsage(api, log);
        const channel = event.channel || ctx?.channelId;
        const style = channel === "telegram" ? s.style : "text";

        if (style === "menu") {
          const line = renderLine(s.menu_fields, turn, quota, { compact: true });
          const chatId = telegramChatId(ctx?.conversationId);
          const token = botToken(api.config, ctx?.accountId);
          if (line && chatId && token) {
            setFooterButton(store, token, chatId, line, menuUrl(s, turn, quota))
              .catch((e) => log.warn?.(`turbofooter: menu button failed: ${e.message}`));
          }
          return;
        }
        const line = renderLine(s.fields, turn, quota, { separator: s.separator, timezone: s.timezone });
        if (!line) return;
        if (style === "button") {
          const blocks = [...(payload.presentation?.blocks || []),
            { type: "buttons", buttons: [{ label: line.slice(0, 64), action: { type: "callback", value: `${NS}:hide` } }] }];
          return { payload: { ...payload, presentation: { ...(payload.presentation || {}), blocks } } };
        }
        return { payload: { ...payload, text: `${payload.text || ""}\n\n${line}`.trimStart() } };
      } catch (e) {
        log.warn?.(`turbofooter: footer skipped: ${e.message}`);
      }
    });

    api.registerInteractiveHandler?.({
      channel: "telegram",
      namespace: NS,
      handler: async (ctx) => {
        if (ctx.callback?.payload !== "hide") return { handled: false };
        if (ctx.auth?.isAuthorizedSender) await ctx.respond.clearButtons().catch(() => {});
        return { handled: true };
      },
    });

    api.registerCommand({
      name: ID,
      description: "Runtime footer: style, fields, menu button",
      acceptsArgs: true,
      requireAuth: true,
      handler: async (ctx) => ({ text: await command(ctx) }),
    });

    async function command(ctx) {
      const arg = String(ctx.args || "").trim();
      const low = arg.toLowerCase();
      const eff = settings();
      const save = (patch) => store.update((st) => { st.settings = { ...(st.settings || {}), ...patch }; });
      const tg = ctx.channel === "telegram" ? {
        chatId: telegramChatId(ctx.to) || telegramChatId(ctx.from) || telegramChatId(ctx.senderId),
        token: botToken(ctx.config || api.config, ctx.accountId),
      } : null;
      // Hand the menu button back when the footer leaves it (off / another style / new idle label).
      const idleMenu = async (s, onlyIfTaken = false) => {
        if (!tg?.chatId || !tg.token) return;
        try {
          await idleButton(store, tg.token, tg.chatId, s.menu_idle, s.menu_url, onlyIfTaken);
        } catch (e) {
          log.warn?.(`turbofooter: menu button reset failed: ${e.message}`);
        }
      };

      if (["", "status", "?", "help"].includes(low)) {
        const status = [
          `📎 Runtime footer: ${eff.enabled ? "on" : "off"} · style \`${eff.style}\``,
          `Fields: \`${eff.fields.join(", ")}\``,
          `Menu button fields: \`${eff.menu_fields.join(", ")}\``,
          `Menu button opens: ${eff.menu_url === DEFAULT_MENU_URL ? "built-in details page" : `\`${eff.menu_url}\``}`,
          `Menu button when idle: ${eff.menu_idle.toLowerCase() === "restore" ? "the chat's previous button" : `\`${eff.menu_idle}\``}`,
        ].join("\n");
        return low === "status" || low === "?" ? status : `${status}\n\n${USAGE_HELP}`;
      }
      if (low === "on" || low === "off") {
        save({ enabled: low === "on" });
        if (low === "off" && eff.style === "menu") await idleMenu(eff);
        return `📎 Runtime footer: ${low} · style \`${eff.style}\``;
      }
      if (STYLES.includes(low)) {
        save({ style: low });
        if (eff.style === "menu" && low !== "menu") await idleMenu(eff);
        let notice = `📎 Runtime footer style: ${low} — shows up with the next reply` +
          (eff.enabled ? "" : " (footer is off — `/turbofooter on`)");
        if (low !== "text" && ctx.channel !== "telegram") notice += `\n(only Telegram supports \`${low}\`; ${ctx.channel} keeps the text footer)`;
        return notice;
      }
      if (HERMES_ONLY_STYLES.includes(low)) {
        return `\`${low}\` is not available on OpenClaw: plugins cannot set a reply keyboard on agent replies. Use \`text\`, \`button\` or \`menu\`.`;
      }
      const head = low.split(" ")[0];
      const rest = arg.slice(head.length).trim();
      if (["fields", "menu-fields", "menu_fields"].includes(head)) {
        const fields = parseFields(rest.toLowerCase());
        if (!fields) return `Usage: \`/turbofooter ${head} a,b,c\`\nKnown: \`${KNOWN_FIELDS.join(", ")}\``;
        const leaf = head === "fields" ? "fields" : "menu_fields";
        save({ [leaf]: fields });
        return `📎 Runtime footer ${leaf.replace("_", " ")}: \`${fields.join(", ")}\``;
      }
      if (head === "url") {
        if (["", "default", "reset"].includes(rest.toLowerCase())) {
          save({ menu_url: null });
          return "📎 Menu button opens the built-in details page";
        }
        if (!rest.startsWith("https://")) return "Menu button link must be an `https://` URL (Telegram web-app rule)";
        save({ menu_url: rest });
        return `📎 Menu button opens \`${rest}\``;
      }
      if (head === "idle") {
        if (!rest) return "Usage: `/turbofooter idle <text>` · `idle restore` · `idle default`";
        const value = rest.toLowerCase() === "default" ? null
          : rest.toLowerCase() === "restore" ? "restore" : rest.slice(0, 64);
        save({ menu_idle: value });
        if (!(eff.enabled && eff.style === "menu")) { // idle right now: show it at once
          await idleMenu(resolveSettings(eff, { menu_idle: value }), eff.style !== "menu");
        }
        return value === "restore" ? "📎 Idle menu button: the chat's previous button"
          : `📎 Idle menu button: \`${value || DEFAULT_MENU_IDLE}\``;
      }
      return USAGE_HELP;
    }

    log.info?.(`turbofooter loaded (state: ${store.file})`);
  },
};

function resolveStateDir(api) {
  try {
    const dir = api.runtime?.state?.resolveStateDir?.();
    if (dir) return dir;
  } catch { /* older runtime */ }
  return process.env.OPENCLAW_STATE_DIR || path.join(os.homedir(), ".openclaw");
}

function turnData(u) {
  const last = u.lastUsage || u.usage || {};
  const prompt = (last.input || 0) + (last.cacheRead || 0) + (last.cacheWrite || 0);
  return {
    model: u.model || "",
    thinking: u.reasoningEffort || "",
    ctxUsed: u.contextUsedTokens || prompt || 0,
    ctxTotal: u.contextTokenBudget || 0,
    input: prompt,
    output: last.output || 0,
  };
}

// --------------------------------------------------------------------------- Codex 5h quota
const usageCache = { at: 0, value: null, inflight: null };

/** The ChatGPT-subscription OAuth profile OpenClaw runs Codex models with ({ access, accountId }), or null. */
function codexCredentials(api) {
  let profiles = {};
  try {
    profiles = api.runtime?.modelAuth?.ensureAuthProfileStore?.()?.profiles || {};
  } catch { /* runtime without auth access */ }
  for (const p of Object.values(profiles)) {
    if (p?.type === "oauth" && ["openai", "openai-codex"].includes(p.provider) && p.access &&
        (!p.expires || p.expires > Date.now())) {
      return p;
    }
  }
  return null;
}

async function fetchCodexUsage(cred) {
  const headers = { Authorization: `Bearer ${cred.access}`, Accept: "application/json", originator: "openclaw",
    "User-Agent": "openclaw-turbofooter" };
  if (cred.accountId) headers["ChatGPT-Account-Id"] = cred.accountId;
  const res = await fetch("https://chatgpt.com/backend-api/wham/usage", { headers, signal: AbortSignal.timeout(5000) });
  if (!res.ok) throw new Error(`wham/usage HTTP ${res.status}`);
  const w = (await res.json())?.rate_limit?.primary_window;
  if (!w || w.used_percent == null) return null;
  const resetAt = w.reset_at || (w.reset_after_seconds != null ? Date.now() / 1000 + w.reset_after_seconds : null);
  return { usedPercent: Number(w.used_percent), resetAt: resetAt ? Number(resetAt) : null };
}

/** Cached 60 s, refreshed in the background; only the very first fetch is awaited (bounded by its timeout). */
async function codexUsage(api, log) {
  const cred = codexCredentials(api);
  if (!cred) return null; // local check, not cached: a fresh login shows up on the next reply
  if (Date.now() - usageCache.at > USAGE_TTL_MS && !usageCache.inflight) {
    usageCache.inflight = fetchCodexUsage(cred)
      .then((v) => { usageCache.value = v; })
      .catch((e) => log.debug?.(`turbofooter: usage fetch failed: ${e.message}`))
      .finally(() => { usageCache.at = Date.now(); usageCache.inflight = null; });
  }
  if (usageCache.at === 0 && usageCache.inflight) await usageCache.inflight;
  return usageCache.value;
}

// --------------------------------------------------------------------------- Telegram menu button
/** Same order as OpenClaw: account tokenFile/botToken, channel tokenFile/botToken, env (default account).
 *  SecretRef objects are not resolved here: the menu style then stays off. */
function botToken(config, accountId) {
  const tg = config?.channels?.telegram || {};
  const acc = (accountId && tg.accounts?.[accountId]) || null;
  for (const src of [acc, tg]) {
    if (!src) continue;
    if (typeof src.tokenFile === "string") {
      try { return fs.readFileSync(src.tokenFile, "utf8").trim(); } catch { /* unreadable */ }
    }
    if (typeof src.botToken === "string" && src.botToken) return src.botToken;
  }
  return !accountId || accountId === "default" ? process.env.TELEGRAM_BOT_TOKEN || "" : "";
}

async function botApi(token, method, body) {
  const res = await fetch(`https://api.telegram.org/bot${token}/${method}`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    signal: AbortSignal.timeout(10000),
  });
  const data = await res.json().catch(() => ({}));
  if (!data.ok) throw new Error(`${method}: ${data.description || res.status}`);
  return data.result;
}

// The chat's own menu button (say, a "Dashboard" web app) is saved before the footer first takes it over,
// per bot and chat (several bots can talk to one user), and brought back by `idle restore`.
const chatKey = (token, chatId) => `${String(token).split(":")[0]}:${chatId}`;

async function rememberButton(store, token, chatId) {
  const key = chatKey(token, chatId);
  const st = store.read();
  if (key in (st.menu_buttons || {})) return;
  let raw = await botApi(token, "getChatMenuButton", { chat_id: Number(chatId) }).catch(() => undefined);
  if (raw === undefined) return;
  if (raw?.text && raw.text === st.footer_text?.[key]) raw = null; // our own leftover, not the original
  store.update((s) => { (s.menu_buttons ||= {})[key] = raw; });
}

async function setFooterButton(store, token, chatId, line, url) {
  await rememberButton(store, token, chatId);
  store.update((s) => { (s.footer_text ||= {})[chatKey(token, chatId)] = line; });
  await botApi(token, "setChatMenuButton", {
    chat_id: Number(chatId), menu_button: { type: "web_app", text: line, web_app: { url } },
  });
}

async function idleButton(store, token, chatId, idle, url, onlyIfTaken) {
  const key = chatKey(token, chatId);
  if (String(idle).toLowerCase() === "restore") {
    const st = store.read();
    if (!(key in (st.menu_buttons || {}))) return; // never taken over: leave it alone
    const raw = st.menu_buttons[key];
    await botApi(token, "setChatMenuButton", { chat_id: Number(chatId), menu_button: raw || { type: "default" } });
    store.update((s) => { delete s.menu_buttons[key]; });
    return;
  }
  if (onlyIfTaken && !(key in (store.read().menu_buttons || {}))) return;
  await rememberButton(store, token, chatId); // keep the original for a later "idle restore"
  await botApi(token, "setChatMenuButton", {
    chat_id: Number(chatId),
    menu_button: { type: "web_app", text: idle || DEFAULT_MENU_IDLE, web_app: { url: url || DEFAULT_MENU_URL } },
  });
}

// --------------------------------------------------------------------------- state file
function jsonFile(file) {
  const read = () => {
    try { return JSON.parse(fs.readFileSync(file, "utf8")); } catch { return {}; }
  };
  return {
    file,
    read,
    update(fn) {
      const st = read();
      fn(st);
      fs.mkdirSync(path.dirname(file), { recursive: true });
      fs.writeFileSync(file, JSON.stringify(st, null, 1));
    },
  };
}
