"""Offline tests; run inside a Hermes checkout: ``python -m pytest tests`` (hermes-agent on sys.path)."""

import asyncio
import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location("rfp_turbofooter", ROOT / "turbofooter.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


fp = _load()
fp.apply()
import gateway.runtime_footer as rf  # noqa: E402


def _usage(pct=11, minutes=282):
    fp._usage.update(at=1e18, session=(pct, datetime.now(timezone.utc) + timedelta(minutes=minutes, seconds=30)))


def test_patches_applied():
    assert not fp._failed, fp._failed
    assert getattr(rf.format_runtime_footer, "_rfp", False)


def test_full_line():
    _usage()
    fp._gateway_config = lambda: {}
    line = rf.format_runtime_footer(model="gpt-x", context_tokens=72, context_length=100,
                                    fields=["model", "ctx", "quota_pct", "quota_left"])
    assert line == "gpt-x · 🅲 72% · ⏱ 5h: 11% · ↻ 4:42", line


def test_compact_line():
    _usage()
    token = fp._compact_fields.set(["ctx", "quota_pct", "quota_left"])
    try:
        line = rf.format_runtime_footer(model="gpt-x", context_tokens=72, context_length=100, fields=["model"])
    finally:
        fp._compact_fields.reset(token)
    assert line == "🅲72% ⏱11% ↻4:42", line


def test_ctx_tokens_and_quota_5h():
    _usage(pct=13, minutes=74)
    line = rf.format_runtime_footer(model="m", context_tokens=118_000, context_length=1_000_000,
                                    fields=["ctx_tokens", "quota_5h"])
    assert line == "🅲 118K/1M · ⏱ 13% (↻ 1:14 /5h)", line


def test_resolve_defaults_are_neutral():
    cfg = rf.resolve_footer_config({}, "telegram")
    assert cfg["style"] == "text" and cfg["menu_url"] == fp.DEFAULT_MENU_URL and cfg["timezone"] == ""
    cfg = rf.resolve_footer_config({"display": {"runtime_footer": {"style": "menu", "menu_url": "http://x"}}}, "telegram")
    assert cfg["style"] == "menu" and cfg["menu_url"] == fp.DEFAULT_MENU_URL  # non-https ignored


def test_menu_url_data_only_for_builtin_page():
    data = {"m": "gpt-x", "e": "med", "cu": 72, "ct": 100, "q": 11, "r": 1700000000, "ts": 1}
    url = fp._menu_url({"menu_url": fp.DEFAULT_MENU_URL, "data": data})
    assert url.startswith(fp.DEFAULT_MENU_URL + "?") and "m=gpt-x" in url and "#" not in url
    assert fp._menu_url({"menu_url": "https://example.com/", "data": data}) == "https://example.com/"
    assert "q=11" in fp._menu_url({"menu_url": "https://example.com/?a=1", "menu_data": True, "data": data})


def test_command_usage_and_validation():
    run = lambda a: asyncio.run(fp.turbofooter_command(a))  # noqa: E731
    saved = {}
    fp._save_leaf = lambda leaf, value: saved.__setitem__(leaf, value)
    assert "/turbofooter" in run("nonsense")
    assert run("fields model,bogus").startswith("Usage")
    assert "ctx" in run("fields model,ctx") and saved["fields"] == ["model", "ctx"]
    assert run("menu-fields ctx,quota_left") and saved["menu_fields"] == ["ctx", "quota_left"]
    assert "https" in run("url http://insecure")
    run("url https://example.com/x") ; assert saved["menu_url"] == "https://example.com/x"
    run("url default") ; assert saved["menu_url"] is None
    run("button") ; assert saved["style"] == "button"
    run("off") ; assert saved["enabled"] is False


class _Bot:
    def __init__(self):
        self.calls = []

    async def set_chat_menu_button(self, chat_id, menu_button):
        self.calls.append(menu_button)


def test_restore_leaves_untouched_button_alone(tmp_path=None):
    import tempfile
    from telegram import MenuButtonCommands
    path = Path(tempfile.mkdtemp()) / "state.json"
    fp._state_file = lambda: path
    bot = _Bot()
    path.write_text('{"menu_buttons": {}}')
    asyncio.run(fp._restore_menu_button(bot, 1, MenuButtonCommands))
    assert bot.calls == []
    path.write_text('{"menu_buttons": {"1": {"type": "web_app", "text": "Mine", "web_app": {"url": "https://e.x/"}}}}')
    asyncio.run(fp._restore_menu_button(bot, 1, MenuButtonCommands))
    assert bot.calls[-1].text == "Mine" and "1" not in fp._state()["menu_buttons"]
    path.write_text('{"menu_buttons": {"1": null}}')
    asyncio.run(fp._restore_menu_button(bot, 1, MenuButtonCommands))
    assert isinstance(bot.calls[-1], MenuButtonCommands)
