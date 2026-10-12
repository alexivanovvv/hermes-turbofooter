# TurboFooter for OpenClaw

The [OpenClaw](https://github.com/openclaw/openclaw) plugin of TurboFooter: a **runtime footer** on agent
replies with the model, reasoning effort, context window and the Codex 5-hour quota:

```
gpt-5.6 · med · 🅲 43% · ⏱ 5h: 11% · ↻ 4:42
```

## Install

Needs OpenClaw 2026.10 or newer (tested on 2026.10.2).

```bash
openclaw plugins install git:alexivanovvv/turbofooter --accept-capabilities
openclaw plugins enable turbofooter
openclaw gateway restart        # or however you run the gateway
```

Styles, commands, fields, configuration and limitations for both Hermes and OpenClaw are in the
[main README](../README.md).

## License

MIT, see [LICENSE](../LICENSE).
