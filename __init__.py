"""turbofooter — Hermes plugin entry point."""

from __future__ import annotations

import sys

from . import turbofooter


def register(ctx) -> None:
    ctx.register_command(
        "turbofooter", turbofooter.turbofooter_command,
        description="Runtime footer: style, fields, menu button",
        args_hint="[status|on|off|text|button|menu|keyboard|placeholder|fields …|menu-fields …|url …]",
    )
    # Monkeypatch only inside the gateway (the CLI has no Telegram surfaces to decorate).
    if _in_gateway():
        turbofooter.apply()


def _in_gateway() -> bool:
    if any(m == "gateway.run" or m.startswith("gateway.run_") for m in sys.modules):
        return True
    argv = [a for a in sys.argv[1:] if not a.startswith("-")]
    return argv[:1] == ["gateway"]


__all__ = ["register"]
