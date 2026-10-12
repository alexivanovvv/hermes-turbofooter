# TurboFooter for OpenClaw

The [OpenClaw](https://github.com/openclaw/openclaw) version of TurboFooter: a **runtime footer** on agent
replies with the model, reasoning effort, context window and the Codex 5-hour quota:

```
gpt-5.6 · med · 🅲 43% · ⏱ 5h: 11% · ↻ 4:42
```

| Style | What you see |
|---|---|
| `text` | The line at the end of the reply (any channel) |
| `button` | Telegram: an inline button under the reply; tap it to remove it (the message itself stays clean) |
| `menu` | Telegram: the chat **menu button** left of the input field shows a compact line (`🅲43% ⏱11% ↻4:42`); tapping it opens a details page |

On channels other than Telegram, `button` and `menu` fall back to `text`. The Hermes-only `keyboard` and
`placeholder` styles are not available: OpenClaw plugins cannot put a reply keyboard on agent replies.

Everything is switched from the chat with **`/turbofooter`**. It works alongside OpenClaw's own
`/usage` footer; turn that off (`/usage off`) if you want only one footer.

## Install

Needs OpenClaw 2026.10 or newer (tested on 2026.10.2). The plugin uses no npm dependencies.

```bash
git clone https://github.com/alexivanovvv/turbofooter.git
openclaw plugins install ./turbofooter/openclaw --accept-capabilities
openclaw plugins enable turbofooter
openclaw gateway restart        # or however you run the gateway
```

OpenClaw warns that this is a local (unreviewed) source and asks to confirm (without a terminal, e.g. in a
script, add `--force`); read the code first if you like, it is two JavaScript files. To check it loaded:

```bash
openclaw plugins inspect turbofooter --runtime   # expect status loaded, hook reply_payload_sending, command turbofooter
```

Update: `git pull`, rerun the install command with `--force`, restart the gateway. With `--link` added to
the install command OpenClaw uses the checkout in place, so `git pull` and a restart are enough.
Remove: `openclaw plugins uninstall turbofooter`.

## Commands

```
/turbofooter                      status + help
/turbofooter on | off
/turbofooter text | button | menu
/turbofooter fields model,effort,ctx,quota_pct,quota_left
/turbofooter menu-fields ctx,quota_pct,quota_left
/turbofooter url https://…        menu button link (url default = built-in details page)
/turbofooter idle <text>          menu button text when the footer is off or in another style
/turbofooter idle restore         … or bring back the chat's previous menu button
/turbofooter idle default
```

The command needs an authorized sender and runs without the model. Settings changed from the chat are saved
in `<state dir>/plugin-data/turbofooter/state.json` and take precedence over the config.

## Fields

`model`, `effort`, `ctx`, `ctx_tokens`, `context_pct`, `tokens` (`↑118K ↓900`, prompt and output tokens of
the last model call), `quota_pct`, `quota_left`, `quota_5h`, `session_pct`, `session_reset`. The meaning is
the same as in the [Hermes version](../README.md#fields).

## Configuration

In `openclaw.json`, under `plugins.entries.turbofooter.config` (all keys optional):

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

## How it works and limitations

- The footer comes from the `reply_payload_sending` hook: OpenClaw passes the model, reasoning effort and
  context use of the run with the final reply. Replies delivered from the durable queue or replayed after a
  restart carry no usage data and get no footer. A reply split into several messages gets the footer once.
- **Quota fields** need an OpenAI (ChatGPT/Codex) OAuth sign-in in OpenClaw; the plugin reads it through the
  runtime and asks `chatgpt.com/backend-api/wham/usage`, at most once a minute. With API keys or other
  providers the quota fields are just left out.
- **`button`** needs inline buttons enabled for the chat: `channels.telegram.capabilities.inlineButtons`
  (`dm`, `group`, `all`, or `allowlist`, the default, which covers allowlisted senders).
- **`menu`** calls the Telegram Bot API itself (`setChatMenuButton`) with the bot token from
  `channels.telegram` (`tokenFile` or `botToken`, per account or top-level) or `TELEGRAM_BOT_TOKEN`. A token
  given as a SecretRef is not resolved, so `menu` stays inactive then. The chat's own menu button is saved
  before the footer first takes it over and comes back with `/turbofooter idle restore`.
- OpenClaw marks the plugin hook API as evolving; a future release may need an update here.

## Development

```bash
cd openclaw && node --test test/*.test.js
```

## License

MIT, see [LICENSE](../LICENSE).
