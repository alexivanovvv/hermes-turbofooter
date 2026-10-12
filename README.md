# TurboFooter for Hermes and OpenClaw

A richer **runtime footer** for agent replies, as a plugin for both the
[Hermes agent](https://github.com/NousResearch/hermes-agent) gateway and [OpenClaw](https://github.com/openclaw/openclaw):
model, reasoning effort, context window and the Codex 5-hour quota with a reset countdown.

```
gpt-5.6-terra · med · 🅲 72% · ⏱ 5h: 11% · ↻ 4:42
```

On Telegram it also lets you choose **where** the footer lives, so your replies stay clean for forwarding:

| Style | What you see | Hermes | OpenClaw |
|---|---|:-:|:-:|
| `text` | The line at the end of the reply (any channel) | ✓ | ✓ |
| `button` | An inline button under the reply; tap it to remove it (the message itself stays clean) | ✓ | ✓ |
| `menu` | The chat **menu button** left of the input field shows a compact line (`🅲72% ⏱11% ↻4:42`); tapping it opens a details page | ✓ | ✓ |
| `keyboard` | A persistent reply keyboard with the footer as its only key (tapping it does nothing) | ✓ | – |
| `placeholder` | The footer as the input-field placeholder (shown while the input is empty) | ✓ | – |

`keyboard` and `placeholder` are Hermes-only: OpenClaw plugins cannot put a reply keyboard on agent replies.
On channels other than Telegram every style falls back to `text`.

Everything is switched from the chat with **`/turbofooter`**. The stock footer commands are left untouched
(Hermes `/footer`, OpenClaw `/usage`; on OpenClaw turn `/usage off` if you want only one footer).

## Install

| Agent | Command |
|---|---|
| Hermes | `hermes plugins install alexivanovvv/turbofooter` |
| OpenClaw | `openclaw plugins install git:alexivanovvv/turbofooter --accept-capabilities` |

Then enable the plugin and restart the gateway. Both agents warn that this is an unreviewed source; read the
code first if you like, it is two files per agent.

### Hermes

```bash
hermes plugins install alexivanovvv/turbofooter
hermes plugins enable turbofooter
systemctl --user restart hermes-gateway   # or however you run `hermes gateway run`
```

Check it loaded:

```bash
grep "turbofooter loaded" ~/.hermes/logs/agent.log | tail -1   # expect failed=none
```

Update: `hermes plugins update turbofooter` and restart the gateway. Remove: `hermes plugins remove turbofooter`.

### OpenClaw

Needs OpenClaw 2026.10 or newer (tested on 2026.10.2). No npm dependencies.

```bash
openclaw plugins install git:alexivanovvv/turbofooter --accept-capabilities
openclaw plugins enable turbofooter
openclaw gateway restart        # or however you run the gateway
```

Without a terminal (e.g. in a script) add `--force` to skip the confirmation. Check it loaded:

```bash
openclaw plugins inspect turbofooter --runtime   # expect status loaded, hook reply_payload_sending, command turbofooter
```

Update: `openclaw plugins update turbofooter` and restart the gateway. Remove: `openclaw plugins uninstall turbofooter`.

OpenClaw clones the repo and loads [`openclaw/index.js`](openclaw/) through the root `package.json` and
`openclaw.plugin.json` (Hermes ignores both). From a local checkout use
`openclaw plugins install ./turbofooter/openclaw --accept-capabilities` (add `--link` to use the checkout in
place, then `git pull` and a restart are enough to update).

## Commands

| Command | Effect |
|---|---|
| `/turbofooter` | Status and help |
| `/turbofooter status` | Status only |
| `/turbofooter on` / `off` | Turn the footer on or off |
| `/turbofooter text\|button\|menu\|keyboard\|placeholder` | Choose the style (`keyboard`, `placeholder`: Hermes only). Switching away from `menu` or `keyboard`/`placeholder` cleans up what that style left in the chat |
| `/turbofooter fields model,effort,ctx,quota_pct,quota_left` | Fields of the full line |
| `/turbofooter menu-fields ctx,quota_pct,quota_left` | Fields of the compact menu-button line |
| `/turbofooter url https://…` | Page the menu button opens; `/turbofooter url default` returns to the built-in details page |
| `/turbofooter idle <text>` | Menu button text while the footer is not on it (default `/turbofooter`); `idle restore` brings back the chat's previous button instead, `idle default` resets |

Settings apply from the next reply. Hermes saves them globally in `config.yaml`. OpenClaw saves them in
`<state dir>/plugin-data/turbofooter/state.json`, where they take precedence over the config; there the
command needs an authorized sender and runs without the model.

## Fields

| Field | Example | Meaning |
|---|---|---|
| `model` | `gpt-5.6` | Model of the reply |
| `effort` | `med` | Reasoning effort of the session (`min`, `low`, `med`, `high`, `xhigh`, `off`) |
| `ctx` | `🅲 72%` | Percent of the context window in use |
| `ctx_tokens` | `🅲 118K/272K` | Context tokens used / window size |
| `quota_pct` | `⏱ 5h: 11%` | Percent of the 5-hour quota used |
| `quota_left` | `↻ 4:42` | Time left until the 5-hour window resets (h:mm countdown) |
| `quota_5h` | `⏱ 11% (↻ 4:42 /5h)` | Both in one field |
| `session_pct` | `5h 11%` | Older short form of `quota_pct` |
| `session_reset` | `↻14:01` | Clock time of the reset (uses `timezone`) |

Per agent extras: on **Hermes** the stock fields still work (`context_pct`, `cwd`, `latency`, `served_model`,
whatever your Hermes version supports); on **OpenClaw** there are also `context_pct` and `tokens`
(`↑118K ↓900`, prompt and output tokens of the last model call).

In the `menu` style the button shows only about 20 characters, so it uses `menu_fields` with tight glyphs:
`🅲72% ⏱11% ↻4:42`.

## Configuration

The keys are the same on both agents: `enabled`, `style`, `fields`, `menu_fields`, `menu_url`, `menu_data`,
`menu_idle`, `separator`, `timezone`. All are optional.

### Hermes

Under `display.runtime_footer` in `config.yaml` (per-platform overrides under
`display.platforms.<platform>.runtime_footer` work too):

```yaml
display:
  runtime_footer:
    enabled: true
    fields: [model, effort, ctx, quota_pct, quota_left]
    style: menu                       # text | button | menu | keyboard | placeholder
    menu_fields: [ctx, quota_pct, quota_left]
    menu_url: https://example.com/    # optional, must be https; default: built-in details page
    menu_data: false                  # optional: append footer numbers to a custom menu_url too
    menu_idle: /turbofooter           # optional: menu button text when the footer is off or in another style; "restore" = previous button
    separator: " · "                  # optional
    timezone: Europe/Berlin           # optional, only for session_reset; default: top-level timezone or server time
```

### OpenClaw

Under `plugins.entries.turbofooter.config` in `openclaw.json`:

```json5
{
  plugins: {
    entries: {
      turbofooter: {
        enabled: true,
        config: {
          enabled: true,
          style: "menu",                          // text | button | menu
          fields: ["model", "effort", "ctx", "quota_pct", "quota_left"],
          menu_fields: ["ctx", "quota_pct", "quota_left"],
          menu_url: "https://example.com/",       // must be https; default: built-in details page
          menu_data: false,                       // append footer numbers to a custom menu_url too
          menu_idle: "/turbofooter",              // menu button text when idle; "restore" = previous button
          separator: " · ",
          timezone: "Europe/Berlin"               // only for session_reset
        }
      }
    }
  }
}
```

### The menu button and its details page

Telegram only lets a bot put custom text on the menu button if the button opens an `https` web page. By
default it opens the plugin's static [details page](https://alexivanovvv.github.io/turbofooter/)
(source: [`docs/index.html`](docs/index.html)). The numbers travel in the link's query string; the page has
no backend, stores nothing and sends nothing anywhere. You can host your own copy and point `menu_url` at
it (set `menu_data: true` so it still gets the numbers), or point `menu_url` at any page of yours, such
as your own dashboard.

When you turn the footer off or switch to another style, the menu button stays a web-app button labelled
`/turbofooter` (set your own label with `/turbofooter idle <text>`, up to 64 characters) that opens the same
page. With `/turbofooter idle restore` the chat's previous menu button (say, a web app you set up yourself, or
Telegram's default command menu) comes back instead: it is saved the first time the `menu` style takes the
button over. It is stored per bot and chat in the plugin's `state.json` (Hermes:
`~/.hermes/plugin-data/turbofooter/`, OpenClaw: `<state dir>/plugin-data/turbofooter/`), so with several
bots running, `/turbofooter` only touches the button of the bot you sent it to.

## Limitations

Both agents:

- **5-hour quota fields need a ChatGPT/Codex sign-in** (Hermes: the `openai-codex` provider; OpenClaw: OpenAI
  OAuth). With API keys or other providers `quota_*` and `session_*` are simply left out of the line.
- The menu button is **per chat**, not per message: it always shows the latest numbers. Use `button` if you
  want the numbers kept with each reply.
- Quota numbers are cached for 60 seconds, so they may lag by up to a minute.

Hermes:

- **Streamed replies** (when the reply was already streamed into the chat) keep the text footer, since the
  message is already sent. Only the first reply after a gateway restart waits for quota numbers (up to 3 s).
- `keyboard` and `placeholder` replace any other reply keyboard in the chat.
- TurboFooter works by **monkeypatching Hermes internals** in the gateway process. It is fail-open: if a
  Hermes update changes those internals, the affected part switches off with a `WARNING` in
  `~/.hermes/logs/agent.log` and the stock footer keeps working. Check `failed=` in the load line after
  updating Hermes.
- Tested with Hermes 0.21.4, Python 3.14, python-telegram-bot 22.7.

OpenClaw:

- The footer comes from the `reply_payload_sending` hook: OpenClaw passes the model, reasoning effort and
  context use of the run with the final reply. Replies delivered from the durable queue or replayed after a
  restart carry no usage data and get no footer. A reply split into several messages gets the footer once.
- Quota is read through the runtime's OpenAI OAuth from `chatgpt.com/backend-api/wham/usage`, at most once a minute.
- **`button`** needs inline buttons enabled for the chat: `channels.telegram.capabilities.inlineButtons`
  (`dm`, `group`, `all`, or `allowlist`, the default, which covers allowlisted senders).
- **`menu`** calls the Telegram Bot API itself (`setChatMenuButton`) with the bot token from
  `channels.telegram` (`tokenFile` or `botToken`, per account or top-level) or `TELEGRAM_BOT_TOKEN`. A token
  given as a SecretRef is not resolved, so `menu` stays inactive then.
- OpenClaw marks the plugin hook API as evolving; a future release may need an update here.
- Tested with OpenClaw 2026.10.2.

## Development

```bash
# Hermes, from a Hermes checkout (hermes-agent on sys.path)
python -m pytest path/to/turbofooter/tests

# OpenClaw
node --test openclaw/test/*.test.js
```

Code: Hermes plugin in the repo root (`plugin.yaml`, `__init__.py`, `turbofooter.py`), OpenClaw plugin in
[`openclaw/`](openclaw/). If you change `openclaw/openclaw.plugin.json`, copy it to the root too (a test checks).

## License

MIT, see [LICENSE](LICENSE).
