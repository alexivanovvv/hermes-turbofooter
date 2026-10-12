import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterEach, beforeEach, test } from "node:test";

import { DEFAULT_MENU_URL, fmtLeft, fmtTokens, menuUrl, parseFields, renderLine, resolveSettings, telegramChatId }
  from "../footer.js";
import plugin from "../index.js";

const TURN = { model: "gpt-5.6", thinking: "medium", ctxUsed: 118_000, ctxTotal: 272_000, input: 118_000, output: 800 };
const NOW = 1_800_000_000_000;
const QUOTA = { usedPercent: 11.4, resetAt: NOW / 1000 + 4 * 3600 + 42 * 60 + 5 };

test("full and compact lines", () => {
  assert.equal(renderLine(["model", "effort", "ctx", "quota_pct", "quota_left"], TURN, QUOTA, { now: NOW }),
    "gpt-5.6 · med · 🅲 43% · ⏱ 5h: 11% · ↻ 4:42");
  assert.equal(renderLine(["ctx", "quota_pct", "quota_left"], TURN, QUOTA, { compact: true, now: NOW }), "🅲43% ⏱11% ↻4:42");
  assert.equal(renderLine(["ctx_tokens", "tokens", "quota_5h"], TURN, QUOTA, { separator: " | ", now: NOW }),
    "🅲 118K/272K | ↑118K ↓800 | ⏱ 11% (↻ 4:42 /5h)");
});

test("missing data drops fields", () => {
  assert.equal(renderLine(["model", "effort", "ctx", "quota_pct"], { model: "m", ctxUsed: 5 }, null), "m");
});

test("helpers", () => {
  assert.equal(fmtTokens(1_250_000), "1.25M");
  assert.equal(fmtTokens(2_000_000), "2M");
  assert.equal(fmtLeft(NOW / 1000 + 59, NOW), "0:00");
  assert.deepEqual(parseFields("model, ctx quota_pct"), ["model", "ctx", "quota_pct"]);
  assert.equal(parseFields("model,bogus"), null);
  assert.equal(telegramChatId("telegram:group:-1001234567:topic:5"), "-1001234567");
  assert.equal(telegramChatId("telegram:123456789"), "123456789");
});

test("settings layers ignore invalid values", () => {
  const s = resolveSettings({ style: "menu", menu_url: "http://x" }, { style: "keyboard", fields: ["model"] });
  assert.equal(s.style, "menu");
  assert.equal(s.menu_url, DEFAULT_MENU_URL);
  assert.deepEqual(s.fields, ["model"]);
});

test("menu url carries numbers only for the built-in page", () => {
  const s = resolveSettings();
  assert.equal(menuUrl(s, TURN, QUOTA, 1), DEFAULT_MENU_URL + `?m=gpt-5.6&e=med&cu=118000&ct=272000&q=11&r=${Math.trunc(QUOTA.resetAt)}&ts=1`);
  assert.equal(menuUrl(resolveSettings({ menu_url: "https://me.example/" }), TURN, QUOTA), "https://me.example/");
});

// --------------------------------------------------------------------------- plugin wiring with a fake api
let dir;
let calls;
let realFetch;

function fakeApi(pluginConfig = {}) {
  const hooks = {};
  let cmd;
  let interactive;
  plugin.register({
    pluginConfig,
    config: { channels: { telegram: { botToken: "111:abc" } } },
    logger: { info() {}, warn() {}, debug() {} },
    runtime: { state: { resolveStateDir: () => dir }, modelAuth: { ensureAuthProfileStore: () => ({ profiles: {} }) } },
    on: (name, fn) => { hooks[name] = fn; },
    registerCommand: (c) => { cmd = c; },
    registerInteractiveHandler: (h) => { interactive = h; },
  });
  return { hooks, cmd, interactive };
}

beforeEach(() => {
  dir = fs.mkdtempSync(path.join(os.tmpdir(), "tf-"));
  calls = [];
  realFetch = globalThis.fetch;
  globalThis.fetch = async (url, opts) => {
    calls.push({ url: String(url), body: opts?.body ? JSON.parse(opts.body) : null });
    return { ok: true, json: async () => ({ ok: true, result: { type: "commands" } }) };
  };
});

afterEach(() => {
  globalThis.fetch = realFetch;
  fs.rmSync(dir, { recursive: true, force: true });
});

const USAGE = { provider: "openai", model: "gpt-5.6", reasoningEffort: "high", contextUsedTokens: 50_000,
  contextTokenBudget: 200_000, lastUsage: { input: 40_000, cacheRead: 10_000, output: 300 }, sessionId: "s1" };
const send = (hooks, { kind = "final", text = "Hello", runId = "r1", usageState = USAGE } = {}) =>
  hooks.reply_payload_sending({ payload: { text }, kind, channel: "telegram", runId, usageState },
    { channelId: "telegram", conversationId: "telegram:123456789", runId });

test("text style: footer once per run, only on final replies with usage", async () => {
  const { hooks } = fakeApi();
  assert.equal(await send(hooks, { kind: "block" }), undefined);
  assert.equal(await send(hooks, { usageState: null }), undefined); // replay / durable delivery
  const out = await send(hooks);
  assert.equal(out.payload.text, "Hello\n\ngpt-5.6 · high · 🅲 25%"); // no Codex credentials -> no quota fields
  assert.equal(await send(hooks, { text: "second chunk" }), undefined);
  assert.match((await send(hooks, { runId: "r2" })).payload.text, /🅲 25%$/);
});

test("button style: inline callback button; tf:hide clears it", async () => {
  const { hooks, interactive } = fakeApi({ style: "button" });
  const out = await send(hooks);
  assert.equal(out.payload.text, "Hello");
  const btn = out.payload.presentation.blocks.at(-1).buttons[0];
  assert.equal(btn.label, "gpt-5.6 · high · 🅲 25%");
  assert.deepEqual(btn.action, { type: "callback", value: "tf:hide" });
  let cleared = false;
  const res = await interactive.handler({ callback: { payload: "hide" }, auth: { isAuthorizedSender: true },
    respond: { clearButtons: async () => { cleared = true; } } });
  assert.deepEqual(res, { handled: true });
  assert.ok(cleared);
});

test("non-Telegram channels fall back to the text footer", async () => {
  const { hooks } = fakeApi({ style: "menu" });
  const out = await hooks.reply_payload_sending({ payload: { text: "Hi" }, kind: "final", channel: "discord",
    runId: "r1", usageState: USAGE }, { channelId: "discord" });
  assert.match(out.payload.text, /^Hi\n\ngpt-5.6/);
});

test("menu style: sets the Telegram menu button, text stays clean; idle on switch away", async () => {
  const { hooks, cmd } = fakeApi({ style: "menu" });
  assert.equal(await send(hooks), undefined);
  await new Promise((r) => setTimeout(r, 20));
  const set = calls.find((c) => c.url.endsWith("/setChatMenuButton"));
  assert.equal(set.body.chat_id, 123456789);
  assert.equal(set.body.menu_button.text, "🅲25%");
  assert.match(set.body.menu_button.web_app.url, /\?m=gpt-5.6&e=high&cu=50000&ct=200000&ts=/);
  assert.ok(calls[0].url.endsWith("/getChatMenuButton")); // original saved first

  calls = [];
  const reply = await cmd.handler({ channel: "telegram", to: "telegram:123456789", args: "text" });
  assert.match(reply.text, /style: text/);
  assert.equal(calls.at(-1).body.menu_button.text, "/turbofooter");

  calls = [];
  await cmd.handler({ channel: "telegram", to: "telegram:123456789", args: "idle restore" });
  assert.deepEqual(calls.at(-1).body.menu_button, { type: "commands" }); // the saved original is back
});

test("quota from the Codex OAuth profile", async () => {
  globalThis.fetch = async (url, opts) => {
    calls.push({ url: String(url), headers: opts?.headers });
    return { ok: true, json: async () => ({ rate_limit: { primary_window: { used_percent: 37, reset_after_seconds: 3600 } } }) };
  };
  const hooks = {};
  plugin.register({
    pluginConfig: { fields: ["quota_pct", "quota_left"] }, config: {}, logger: {},
    runtime: { state: { resolveStateDir: () => dir }, modelAuth: { ensureAuthProfileStore: () => ({ profiles: {
      "openai:me": { type: "oauth", provider: "openai", access: "tok", accountId: "acc", expires: Date.now() + 1e6 } } }) } },
    on: (n, fn) => { hooks[n] = fn; }, registerCommand() {},
  });
  const out = await send(hooks, { runId: "q1" });
  const fetched = calls.find((c) => c.url.includes("wham/usage"));
  assert.equal(fetched.headers["ChatGPT-Account-Id"], "acc");
  assert.match(out.payload.text, /^Hello\n\n⏱ 5h: 37% · ↻ (0:59|1:00)$/);
});

test("command: settings persist; Hermes-only styles refused", async () => {
  const { cmd } = fakeApi();
  assert.match((await cmd.handler({ channel: "telegram", args: "fields model,ctx_tokens" })).text, /model, ctx_tokens/);
  assert.match((await cmd.handler({ channel: "telegram", args: "status" })).text, /Fields: `model, ctx_tokens`/);
  assert.match((await cmd.handler({ channel: "telegram", args: "keyboard" })).text, /not available on OpenClaw/);
  assert.match((await cmd.handler({ channel: "telegram", args: "button" })).text, /style: button/);
  assert.match((await cmd.handler({ channel: "telegram", args: "url http://x" })).text, /https/);
  const { cmd: again } = fakeApi(); // new process: overrides read back from state.json
  assert.match((await again.handler({ channel: "telegram", args: "status" })).text, /model, ctx_tokens/);
});
