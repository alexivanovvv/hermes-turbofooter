# TurboFooter for Hermes and OpenClaw

A richer **runtime footer** for the [Hermes agent](https://github.com/NousResearch/hermes-agent) gateway
and for [OpenClaw](https://github.com/openclaw/openclaw) (see [OpenClaw version](#openclaw-version)).

The stock footer is a single line at the end of each reply (`gpt-5.6 · 67%`). TurboFooter adds more
fields and, on Telegram, lets you choose **where** the footer lives, so your replies stay clean for
forwarding:

```
gpt-5.6-terra · med · 🅲 72% · ⏱ 5h: 11% · ↻ 4:42
```

| Style | What you see |
|---|---|
| `text` | Stock behaviour: the line at the end of the message |
| `button` | An inline button under the reply; tap it to remove it (the message itself stays clean) |
| `menu` | The chat **menu button** left of the input field shows a compact line (`🅲72% ⏱11% ↻4:42`); tapping it opens a details page |
| `keyboard` | A persistent reply keyboard with the footer as its only key (tapping it does nothing) |
| `placeholder` | The footer as the input-field placeholder (shown while the input is empty) |

Everything is switched from the chat with **`/turbofooter`**. The stock `/footer` command is left untouched.

## Install

```bash
hermes plugins install alexivanovvv/turbofooter
hermes plugins enable turbofooter
systemctl --user restart hermes-gateway   # or however you run `hermes gateway run`
```

Hermes warns that this is a custom (unreviewed) source; read the code first if you like, it is two
Python files. To check it loaded:

```bash
grep "turbofooter loaded" ~/.hermes/logs/agent.log | tail -1   # expect failed=none
```

Update: `hermes plugins update turbofooter` and restart the gateway. Remove: `hermes plugins remove turbofooter`.

## Commands

| Command | Effect |
|---|---|
| `/turbofooter` | Status and help |
| `/turbofooter status` | Status only |
| `/turbofooter on` / `off` | Turn the footer on or off |
| `/turbofooter text\|button\|menu\|keyboard\|placeholder` | Choose the style. Switching away from `menu` or `keyboard`/`placeholder` cleans up what that style left in the chat |
| `/turbofooter fields model,effort,ctx,quota_pct,quota_left` | Fields of the full line |
| `/turbofooter menu-fields ctx,quota_pct,quota_left` | Fields of the compact menu-button line |
| `/turbofooter url https://…` | Page the menu button opens; `/turbofooter url default` returns to the built-in details page |
| `/turbofooter idle <text>` | Menu button text while the footer is not on it (default `/turbofooter`); `idle restore` brings back the chat's previous button instead, `idle default` resets |

Settings are saved globally in `config.yaml` and apply from the next reply.

## Fields

Stock fields still work (`model`, `context_pct`, `cwd`, `latency`, `served_model`, whatever your Hermes
version supports). TurboFooter adds:

| Field | Example | Meaning |
|---|---|---|
| `effort` | `med` | Reasoning effort of the session (`min`, `low`, `med`, `high`, `xhigh`, `off`) |
| `ctx` | `🅲 72%` | Percent of the context window in use |
| `ctx_tokens` | `🅲 118K/272K` | Context tokens used / window size |
| `quota_pct` | `⏱ 5h: 11%` | Percent of the 5-hour quota used |
| `quota_left` | `↻ 4:42` | Time left until the 5-hour window resets (h:mm countdown) |
| `quota_5h` | `⏱ 11% (↻ 4:42 /5h)` | Both in one field |
| `session_pct` | `5h 11%` | Older short form of `quota_pct` |
| `session_reset` | `↻14:01` | Clock time of the reset (uses `timezone`) |

In the `menu` style the button shows only about 20 characters, so it uses `menu_fields` with tight glyphs:
`🅲72% ⏱11% ↻4:42`.

## Configuration

All keys live under `display.runtime_footer` in your Hermes `config.yaml` (per-platform overrides under
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
button over. It is stored per bot and chat in `~/.hermes/plugin-data/turbofooter/state.json`, so with
several [profiles](https://github.com/NousResearch/hermes-agent) running their own Telegram bots, `/turbofooter`
only touches the button of the bot you sent it to.

## Limitations

- **5-hour quota fields need the `openai-codex` provider** (ChatGPT account usage). With other providers
  `quota_*` and `session_*` are simply left out of the line.
- **Styles other than `text` work on Telegram only.** Other platforms keep the text footer.
- **Streamed replies** (when the reply was already streamed into the chat) keep the text footer, since the
  message is already sent.
- The menu button is **per chat**, not per message: it always shows the latest numbers. Use `button` if you
  want the numbers kept with each reply.
- Quota numbers are cached for 60 seconds and refreshed in the background, so they may lag by up to a minute.
  Only the first reply after a gateway restart waits for them (up to 3 s).
- `keyboard` and `placeholder` replace any other reply keyboard in the chat.
- TurboFooter works by **monkeypatching Hermes internals** in the gateway process. It is fail-open: if a
  Hermes update changes those internals, the affected part switches off with a `WARNING` in
  `~/.hermes/logs/agent.log` and the stock footer keeps working. Check `failed=` in the load line after
  updating Hermes.
- Tested with Hermes 0.21.4, Python 3.14, python-telegram-bot 22.7.

## OpenClaw version

The [`openclaw/`](openclaw/) folder holds a native OpenClaw plugin with the same fields, `/turbofooter`
command and details page, in the `text`, `button` and `menu` styles (OpenClaw plugins cannot set a reply
keyboard, so `keyboard` and `placeholder` are Hermes-only):

```bash
git clone https://github.com/alexivanovvv/turbofooter.git
openclaw plugins install ./turbofooter/openclaw --accept-capabilities
openclaw plugins enable turbofooter
openclaw gateway restart
```

Details, configuration and limitations: [openclaw/README.md](openclaw/README.md).

## Development

```bash
# from a Hermes checkout (hermes-agent on sys.path)
python -m pytest path/to/turbofooter/tests
```

## License

MIT, see [LICENSE](LICENSE).
