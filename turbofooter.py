"""TurboFooter — richer runtime footer for the Hermes gateway (Telegram-first).

Adds to the stock runtime footer (``display.runtime_footer``):
  * fields: ``effort`` ("med"), ``ctx`` ("🅲 72%"), ``ctx_tokens`` ("🅲 118K/272K"), ``quota_pct`` ("⏱ 5h: 7%"),
    ``quota_left`` ("↻ 4:51", countdown to the 5h reset), ``quota_5h`` ("⏱ 7% (↻ 4:51 /5h)"),
    ``session_pct`` ("5h 57%"), ``session_reset`` ("↻14:01"); 5h quota = Codex (openai-codex) account usage;
  * ``style`` — where the line goes on Telegram: text (stock) | button (inline button under the reply, tap hides
    it) | menu (chat menu button left of the input) | keyboard (persistent reply keyboard) | placeholder
    (input-field placeholder);
  * ``/turbofooter`` to switch all of it from the chat (the stock ``/footer`` stays untouched).

Hermes internals are monkeypatched in the gateway process only. Fail-open: if internals changed, the affected
patch is skipped with a WARNING in the gateway log and stock behaviour stays.
"""

from __future__ import annotations

import contextlib
import contextvars
import inspect
import json
import logging
import re
import sys
import threading
import time
import weakref
from pathlib import Path
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger("plugins.turbofooter")

PLUGIN_NAME = "turbofooter"

FOOTER_STYLES = ("text", "button", "menu", "keyboard", "placeholder")
EXTRA_FIELDS = ("session_pct", "session_reset", "effort", "ctx", "ctx_tokens", "quota_pct", "quota_left", "quota_5h")
KNOWN_FIELDS = ("model", "effort", "context_pct", "ctx", "ctx_tokens", "session_pct", "session_reset", "quota_pct",
                "quota_left", "quota_5h", "latency", "served_model", "cwd")
_EFFORT_SHORT = {"minimal": "min", "medium": "med", "xhigh": "xhigh", "none": "off"}
# Static, backend-less page that renders the footer numbers passed in its query string (see docs/index.html).
# Point display.runtime_footer.menu_url at your own https page to open that instead.
DEFAULT_MENU_URL = "https://alexivanovvv.github.io/hermes-turbofooter/"
KEY_PREFIX = "📊 "  # reply-keyboard taps arrive as text with this prefix and are swallowed
SEP = " · "
USAGE = ("`/turbofooter` — runtime footer settings\n"
         "`/turbofooter status` · `on` · `off`\n"
         "Style: `/turbofooter text|button|menu|keyboard|placeholder`\n"
         "Fields: `/turbofooter fields model,effort,ctx,quota_pct,quota_left`\n"
         "Menu button fields: `/turbofooter menu-fields ctx,quota_pct,quota_left`\n"
         "Menu button link: `/turbofooter url https://…` or `/turbofooter url default`")

_applied: list[str] = []
_failed: list[str] = []
_reply_markup: contextvars.ContextVar = contextvars.ContextVar("rfp_reply_markup", default=None)
# Effective reasoning config of the turn being footered (set around the stock footer builder).
_turn_reasoning: contextvars.ContextVar = contextvars.ContextVar("rfp_turn_reasoning", default=None)
# Compact render (menu style: the chat menu button shows only ~20 chars): fields override, tight glyphs.
_compact_fields: contextvars.ContextVar = contextvars.ContextVar("rfp_compact_fields", default=None)
# Raw numbers of the turn being footered (filled by the formatter, read for the menu details page).
_turn_data: contextvars.ContextVar = contextvars.ContextVar("rfp_turn_data", default=None)
DEFAULT_MENU_FIELDS = ("ctx", "quota_pct", "quota_left")


_steps: list = []


def _patch(name: str):
    """Register a patch step for :func:`apply`; a failing step is logged and leaves stock behaviour."""
    def deco(fn):
        _steps.append((name, fn))
        return fn
    return deco


def apply() -> None:
    """Apply every patch step once (gateway process)."""
    if _applied or _failed:
        return
    for name, fn in _steps:
        try:
            fn()
            _applied.append(name)
        except Exception as e:  # noqa: BLE001
            _failed.append(name)
            logger.warning("turbofooter: patch %s skipped (%s: %s)", name, type(e).__name__, e)
    logger.info("turbofooter loaded: applied=%s failed=%s", _applied, _failed or "none")


def _require_params(func, *names):
    params = inspect.signature(func).parameters
    missing = [n for n in names if n not in params]
    if missing:
        raise RuntimeError(f"{getattr(func, '__qualname__', func)} lacks params {missing}")


def _gateway_config() -> dict:
    from gateway.run import _load_gateway_config
    return _load_gateway_config() or {}


# --------------------------------------------------------------------------- Codex 5h usage
_USAGE_TTL = 60.0
_usage: Dict[str, Any] = {"at": 0.0, "session": None, "thread": None}


def _refresh_usage() -> None:
    try:
        from agent.account_usage import _fetch_codex_account_usage
        snap = _fetch_codex_account_usage()
        session = next((w for w in (snap.windows if snap else []) if w.label == "Session"), None)
        _usage["session"] = (session.used_percent, session.reset_at) if session else None
    except Exception as e:  # noqa: BLE001
        logger.debug("turbofooter: usage fetch failed: %s", e)
    _usage["at"] = time.monotonic()


def _session_usage(wait: float = 3.0):
    """``(used_percent, reset_at)`` of the Codex 5h window, or None. Stale cache refreshes in the
    background; only the very first fetch is awaited (bounded)."""
    th = _usage.get("thread")
    if time.monotonic() - _usage["at"] > _USAGE_TTL and not (th and th.is_alive()):
        th = threading.Thread(target=_refresh_usage, daemon=True, name="footer-codex-usage")
        _usage["thread"] = th
        th.start()
    if _usage["at"] == 0.0 and th is not None:
        th.join(wait)
    return _usage["session"]


def _fmt_reset(reset_at, tz_name: str) -> str:
    if reset_at is None:
        return ""
    try:
        local = reset_at.astimezone(ZoneInfo(tz_name)) if tz_name else reset_at.astimezone()
        return "↻" + local.strftime("%H:%M")
    except Exception:  # noqa: BLE001
        return ""


def _fmt_tokens(n) -> str:
    n = int(n or 0)
    if n >= 1_000_000:
        return f"{n / 1_000_000:.2f}".rstrip("0").rstrip(".") + "M"
    if n >= 1000:
        return f"{round(n / 1000)}K"
    return str(n)


def _fmt_left(reset_at) -> str:
    """Time until the 5h window resets, ``1:14``."""
    if reset_at is None:
        return ""
    from datetime import datetime, timezone
    secs = max(0, int((reset_at - datetime.now(timezone.utc)).total_seconds()))
    return f"{secs // 3600}:{secs % 3600 // 60:02d}"


def _effort_label() -> str:
    rc = _turn_reasoning.get()
    if rc is None:
        with contextlib.suppress(Exception):
            from hermes_constants import resolve_reasoning_config
            rc = resolve_reasoning_config(_gateway_config())
    if rc is None:
        return "med"  # Hermes default
    if isinstance(rc, dict):
        if rc.get("enabled") is False:
            return "off"
        effort = str(rc.get("effort") or "medium").lower()
        return _EFFORT_SHORT.get(effort, effort)
    return ""


# --------------------------------------------------------------------------- gateway.runtime_footer
@_patch("runtime_footer")
def _patch_runtime_footer():
    import gateway.runtime_footer as rf
    orig_resolve, orig_format = rf.resolve_footer_config, rf.format_runtime_footer
    _require_params(orig_format, "fields")
    if getattr(orig_format, "_rfp", False):
        return

    def resolve_footer_config(user_config=None, platform_key=None):
        resolved = orig_resolve(user_config, platform_key)
        resolved.update({"style": "text", "timezone": "", "menu_url": DEFAULT_MENU_URL, "separator": SEP,
                         "menu_fields": list(DEFAULT_MENU_FIELDS), "menu_data": None})
        cfg = (user_config or {}).get("display") or {}
        plat = (cfg.get("platforms") or {}).get(platform_key) if platform_key else None
        for section in (cfg.get("runtime_footer"), plat.get("runtime_footer") if isinstance(plat, dict) else None):
            if not isinstance(section, dict):
                continue
            if section.get("style") in FOOTER_STYLES:
                resolved["style"] = section["style"]
            if isinstance(section.get("menu_url"), str) and section["menu_url"].startswith("https://"):
                resolved["menu_url"] = section["menu_url"]
            if isinstance(section.get("timezone"), str) and section["timezone"]:
                resolved["timezone"] = section["timezone"]
            if isinstance(section.get("menu_fields"), list) and section["menu_fields"]:
                resolved["menu_fields"] = [str(f) for f in section["menu_fields"]]
            if isinstance(section.get("menu_data"), bool):
                resolved["menu_data"] = section["menu_data"]
            if isinstance(section.get("separator"), str) and section["separator"]:
                resolved["separator"] = section["separator"]
        if not resolved["timezone"] and isinstance((user_config or {}).get("timezone"), str):
            resolved["timezone"] = user_config["timezone"]
        return resolved

    def format_runtime_footer(*args, fields=None, **kwargs):
        data = _turn_data.get()
        if data is not None:
            data.update(model=kwargs.get("model") or data.get("model") or "",
                        ctx_used=kwargs.get("context_tokens") or 0, ctx_total=kwargs.get("context_length") or 0)
        compact = _compact_fields.get()
        if compact:
            fields = list(compact)
        fields = list(fields) if fields is not None else None
        if not fields:
            return orig_format(*args, **kwargs) if fields is None else orig_format(*args, fields=fields, **kwargs)
        tz, sep = "", SEP
        with contextlib.suppress(Exception):
            cfg = resolve_footer_config(_gateway_config())
            tz, sep = cfg.get("timezone") or "", cfg.get("separator") or SEP
        if compact:
            sep = " "
        if not compact and not set(fields) & set(EXTRA_FIELDS) and sep == SEP:
            return orig_format(*args, fields=fields, **kwargs)
        parts = []
        for f in fields:
            if f == "effort":
                val = _effort_label()
            elif f == "ctx_tokens":
                used, total = kwargs.get("context_tokens") or 0, kwargs.get("context_length")
                val = f"🅲 {_fmt_tokens(used)}/{_fmt_tokens(total)}" if total else ""
            elif f == "ctx":
                used, total = kwargs.get("context_tokens") or 0, kwargs.get("context_length")
                pct = max(0, min(100, round(used / total * 100))) if total else None
                val = "" if pct is None else (f"🅲{pct}%" if compact else f"🅲 {pct}%")
            elif f == "quota_pct":
                u = _session_usage()
                pct = round(u[0]) if u and u[0] is not None else None
                val = "" if pct is None else (f"⏱{pct}%" if compact else f"⏱ 5h: {pct}%")
            elif f == "quota_left":
                u = _session_usage()
                left = _fmt_left(u[1]) if u else ""
                val = "" if not left else (f"↻{left}" if compact else f"↻ {left}")
            elif f == "quota_5h":
                u = _session_usage()
                if u and u[0] is not None:
                    left = _fmt_left(u[1])
                    val = f"⏱ {round(u[0])}%" + (f" (↻ {left} /5h)" if left else "")
                else:
                    val = ""
            elif f == "session_pct":
                u = _session_usage()
                val = f"5h {round(u[0])}%" if u and u[0] is not None else ""
            elif f == "session_reset":
                u = _session_usage()
                val = _fmt_reset(u[1], tz) if u else ""
            else:
                val = orig_format(*args, fields=[f], **kwargs)
                # With the 5h quota shown too, a bare "67%" is ambiguous -> label the context share.
                if f == "context_pct" and val and "session_pct" in fields:
                    val = "ctx " + val
            if val:
                parts.append(val)
        return sep.join(parts)

    format_runtime_footer._rfp = True
    rf.resolve_footer_config, rf.format_runtime_footer = resolve_footer_config, format_runtime_footer
    rf.FOOTER_STYLES = FOOTER_STYLES


def _surface(source) -> Optional[dict]:
    """``{style, menu_url}`` when the footer goes outside the text (Telegram, style != text)."""
    from gateway.run import _platform_config_key
    from gateway.runtime_footer import resolve_footer_config
    key = _platform_config_key(source.platform)
    cfg = resolve_footer_config(_gateway_config(), key)
    if key != "telegram" or cfg.get("style", "text") == "text":
        return None
    return {"style": cfg["style"], "menu_url": cfg["menu_url"], "menu_fields": cfg.get("menu_fields"),
            "menu_data": cfg.get("menu_data")}


def _menu_url(footer: dict) -> str:
    """Menu web-app URL; the built-in details page (or ``menu_data: true``) gets the numbers in the query.
    Query, not fragment: Telegram appends its launch data to the fragment."""
    from urllib.parse import urlencode
    url = footer.get("menu_url") or DEFAULT_MENU_URL
    send = footer.get("menu_data")
    if send is None:
        send = url == DEFAULT_MENU_URL
    data = footer.get("data") or {}
    if not send or not data:
        return url
    q = {k: data[k] for k in ("m", "e", "cu", "ct", "q", "r", "ts") if data.get(k) not in (None, "")}
    return url + ("&" if "?" in url else "?") + urlencode(q) if q else url


def _details(data: dict, rc) -> dict:
    """Compact query payload for the details page."""
    token = _turn_reasoning.set(rc)
    try:
        effort = _effort_label()
    finally:
        _turn_reasoning.reset(token)
    u = _session_usage(wait=0)
    out = {"m": data.get("model") or "", "e": effort, "cu": int(data.get("ctx_used") or 0) or None,
           "ct": int(data.get("ctx_total") or 0) or None, "ts": int(time.time())}
    if u and u[0] is not None:
        out["q"] = round(u[0])
        with contextlib.suppress(Exception):
            out["r"] = int(u[1].timestamp())
    return out


# --------------------------------------------------------------------------- turn: move line off the text
@_patch("turn_footer_line")
def _patch_turn():
    from gateway.run_turn import GatewayTurnMixin
    orig = GatewayTurnMixin._hmwa_runtime_footer_line
    _require_params(orig, "agent_result", "source")
    if getattr(orig, "_rfp", False):
        return

    def _hmwa_runtime_footer_line(self, agent_result, source, *args, **kwargs):
        rc = None
        with contextlib.suppress(Exception):
            model = agent_result.get("model") if isinstance(agent_result, dict) else ""
            rc = self._resolve_session_reasoning_config(source=source, model=model or "")
        data: dict = {}
        token, dtoken = _turn_reasoning.set(rc), _turn_data.set(data)
        try:
            line = orig(self, agent_result, source, *args, **kwargs)
        finally:
            _turn_reasoning.reset(token)
            _turn_data.reset(dtoken)
        # Streamed turns already delivered the body: keep the stock trailing-text footer.
        if not line or not isinstance(agent_result, dict) or agent_result.get("already_sent"):
            return line
        try:
            surface = _surface(source)
            event = sys._getframe(1).f_locals.get("event")
            if not surface or event is None or not hasattr(event, "source"):
                return line
            footer = {**surface, "line": line, "data": _details(data, rc)}
            if surface["style"] == "menu":
                token = _turn_reasoning.set(rc)
                ctoken = _compact_fields.set(surface.get("menu_fields") or list(DEFAULT_MENU_FIELDS))
                try:
                    footer["line"] = orig(self, agent_result, source, *args, **kwargs) or line
                finally:
                    _compact_fields.reset(ctoken)
                    _turn_reasoning.reset(token)
            event._runtime_footer = footer
            return ""
        except Exception as e:  # noqa: BLE001
            logger.debug("turbofooter: surface failed: %s", e)
            return line

    _hmwa_runtime_footer_line._rfp = True
    GatewayTurnMixin._hmwa_runtime_footer_line = _hmwa_runtime_footer_line


# --------------------------------------------------------------------------- final send: attach the surface
@_patch("send_final_ledgered")
def _patch_send_final():
    from gateway.platforms.base import BasePlatformAdapter
    orig = BasePlatformAdapter.send_final_ledgered
    _require_params(orig, "event", "metadata")
    if getattr(orig, "_rfp", False):
        return

    async def send_final_ledgered(self, event, *args, **kwargs):
        footer = getattr(event, "_runtime_footer", None)
        if not footer:
            return await orig(self, event, *args, **kwargs)
        event._runtime_footer = None
        markup = None
        with contextlib.suppress(Exception):
            adapter = self._final_delivery_adapter(event.source)
            if callable(getattr(adapter, "footer_reply_markup", None)):
                markup = adapter.footer_reply_markup(footer)
        token = _reply_markup.set(markup)
        try:
            result, delivery_adapter = await orig(self, event, *args, **kwargs)
        finally:
            _reply_markup.reset(token)
        if getattr(result, "success", False) and callable(getattr(delivery_adapter, "after_footer_send", None)):
            try:
                await delivery_adapter.after_footer_send(event.source.chat_id, result, footer)
            except Exception as e:  # noqa: BLE001
                logger.warning("turbofooter: after_footer_send failed: %s", e)
        return result, delivery_adapter

    send_final_ledgered._rfp = True
    BasePlatformAdapter.send_final_ledgered = send_final_ledgered


# --------------------------------------------------------------------------- Telegram adapter
# The chat's own menu button (e.g. a "Dashboard" web app) is saved before the menu style first takes it
# over and restored when switching away.
def _state_file() -> Path:
    try:
        from plugins.plugin_storage import plugin_data_dir
        return plugin_data_dir(PLUGIN_NAME) / "state.json"
    except Exception:  # noqa: BLE001
        from hermes_constants import get_hermes_home
        root = get_hermes_home() / "plugin-data" / PLUGIN_NAME
        root.mkdir(parents=True, exist_ok=True)
        return root / "state.json"


def _state() -> dict:
    try:
        return json.loads(_state_file().read_text())
    except Exception:  # noqa: BLE001
        return {}


def _save_state(state: dict) -> None:
    _state_file().write_text(json.dumps(state, ensure_ascii=False, indent=1))


async def _remember_menu_button(bot, chat_id) -> None:
    state = _state()
    saved = state.setdefault("menu_buttons", {})
    if str(chat_id) in saved:
        return
    with contextlib.suppress(Exception):
        current = await bot.get_chat_menu_button(chat_id=int(chat_id))
        raw = current.to_dict()
        # Never take our own leftover footer button for the chat's original one.
        if raw.get("text") and raw.get("text") == (state.get("footer_text") or {}).get(str(chat_id)):
            raw = None
        saved[str(chat_id)] = raw
        _save_state(state)


async def _restore_menu_button(bot, chat_id, fallback_cls) -> None:
    from telegram import MenuButton
    state = _state()
    saved = state.get("menu_buttons") or {}
    if str(chat_id) not in saved:
        return  # the footer never took this chat's button over: leave it alone
    raw = saved.pop(str(chat_id))
    button = MenuButton.de_json(raw, bot) if raw else fallback_cls()
    await bot.set_chat_menu_button(chat_id=int(chat_id), menu_button=button)
    _save_state(state)
    logger.info("turbofooter: chat %s menu button restored (%s)", chat_id, type(button).__name__)


_adapters: "weakref.WeakSet" = weakref.WeakSet()  # live TelegramAdapter instances (for /turbofooter resets)


def _patch_telegram_class(cls) -> None:
    if cls.__dict__.get("_rfp_patched"):
        return
    try:
        from telegram import (InlineKeyboardButton, InlineKeyboardMarkup, MenuButtonCommands, MenuButtonWebApp,
                              ReplyKeyboardMarkup, ReplyKeyboardRemove, WebAppInfo)
        for name in ("_handle_callback_query", "_handle_text_message", "_send_chunk_markdown_or_plain",
                     "_rich_payload_base", "_effective_update_message", "_is_user_authorized_from_message",
                     "_callback_authorized"):
            if not callable(getattr(cls, name, None)):
                raise RuntimeError(f"TelegramAdapter.{name} missing")
        _require_params(cls._send_chunk_markdown_or_plain, "chunk", "send_kwargs")
        mod = sys.modules.get(cls.__module__)
        unauthorized = getattr(mod, "_unauthorized", lambda: "⛔ Not authorized")

        def footer_reply_markup(self, footer):
            style, line = footer.get("style"), footer.get("line") or ""
            if style not in ("keyboard", "placeholder"):
                return None
            markup = {"keyboard": [[{"text": KEY_PREFIX + line}]], "resize_keyboard": True,
                      "is_persistent": style == "keyboard"}
            if style == "placeholder":
                markup["input_field_placeholder"] = line[:64]
            return markup

        async def after_footer_send(self, chat_id, result, footer):
            style, line = footer.get("style"), footer.get("line") or ""
            if not self._bot or not line:
                return
            if style == "button":
                raw = result.raw_response if isinstance(result.raw_response, dict) else {}
                ids = raw.get("message_ids")
                message_id = (ids[-1] if ids else None) or result.message_id
                if message_id:
                    await self._bot.edit_message_reply_markup(
                        chat_id=int(chat_id), message_id=int(message_id),
                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(line[:64], callback_data="rf:hide")]]))
            elif style == "menu":
                # A reply that started before a style switch must not re-take the menu button.
                with contextlib.suppress(Exception):
                    from gateway.runtime_footer import resolve_footer_config
                    cfg = resolve_footer_config(_gateway_config(), "telegram")
                    if not cfg.get("enabled") or cfg.get("style") != "menu":
                        return
                await _remember_menu_button(self._bot, chat_id)
                state = _state()
                state.setdefault("footer_text", {})[str(chat_id)] = line
                _save_state(state)
                await self._bot.set_chat_menu_button(
                    chat_id=int(chat_id),
                    menu_button=MenuButtonWebApp(text=line, web_app=WebAppInfo(url=_menu_url(footer))))

        async def reset_footer_surface(self, chat_id, style, notice):
            """Undo what a style left in the chat UI; True when ``notice`` was delivered here."""
            if not self._bot:
                return False
            if style == "menu":
                try:
                    await _restore_menu_button(self._bot, chat_id, MenuButtonCommands)
                except Exception as e:  # noqa: BLE001
                    logger.warning("turbofooter: menu button restore failed: %s", e)
            elif style in ("keyboard", "placeholder"):
                await self._bot.send_message(chat_id=int(chat_id), text=notice, reply_markup=ReplyKeyboardRemove())
                return True
            return False

        orig_cbq = cls._handle_callback_query

        async def _handle_callback_query(self, update, context):
            query = getattr(update, "callback_query", None)
            if query is not None and (query.data or "").startswith("rf:"):
                with contextlib.suppress(Exception):
                    self._accept_update()
                if await self._callback_authorized(query, self._callback_ctx(query), unauthorized()):
                    with contextlib.suppress(Exception):
                        await query.edit_message_reply_markup(reply_markup=None)
                    with contextlib.suppress(Exception):
                        await query.answer()
                return
            return await orig_cbq(self, update, context)

        orig_text = cls._handle_text_message

        async def _handle_text_message(self, update, context):
            msg = self._effective_update_message(update)
            if msg and (msg.text or "").startswith(KEY_PREFIX) and self._is_user_authorized_from_message(msg):
                with contextlib.suppress(Exception):
                    await msg.delete()  # tap on the footer reply keyboard
                return
            return await orig_text(self, update, context)

        orig_chunk = cls._send_chunk_markdown_or_plain

        async def _send_chunk_markdown_or_plain(self, chunk, send_kwargs):
            markup = _reply_markup.get()
            if markup and "reply_markup" not in send_kwargs:
                send_kwargs = {**send_kwargs, "reply_markup": ReplyKeyboardMarkup.de_json(markup, self._bot)}
            return await orig_chunk(self, chunk, send_kwargs)

        orig_rich = cls._rich_payload_base

        def _rich_payload_base(self, *args, **kwargs):
            payload = orig_rich(self, *args, **kwargs)
            markup = _reply_markup.get()
            if markup and isinstance(payload, dict):
                payload["reply_markup"] = markup
            return payload

        orig_init = cls.__init__

        def __init__(self, *args, **kwargs):
            orig_init(self, *args, **kwargs)
            _adapters.add(self)

        for fn in (footer_reply_markup, after_footer_send, reset_footer_surface, _handle_callback_query,
                   _handle_text_message, _send_chunk_markdown_or_plain, _rich_payload_base, __init__):
            setattr(cls, fn.__name__, fn)
        cls._rfp_patched = True
        _applied.append(f"telegram({cls.__module__})")
        logger.info("turbofooter: patched %s.TelegramAdapter", cls.__module__)
    except Exception as e:  # noqa: BLE001
        _failed.append("telegram")
        logger.warning("turbofooter: Telegram patch skipped (%s: %s)", type(e).__name__, e)


@_patch("telegram_hook")
def _hook_telegram():
    """The Telegram adapter is a lazily loaded plugin (``hermes_plugins.*``): patch it when its class is
    created, plus any copy already imported."""
    from gateway.platforms.base import BasePlatformAdapter
    if not BasePlatformAdapter.__dict__.get("_rfp_subclass_hook"):
        prev = BasePlatformAdapter.__dict__.get("__init_subclass__")

        def __init_subclass__(cls, **kw):
            if prev is not None:
                prev.__func__(cls, **kw)
            else:
                super(BasePlatformAdapter, cls).__init_subclass__(**kw)
            if cls.__name__ == "TelegramAdapter":
                _patch_telegram_class(cls)

        BasePlatformAdapter.__init_subclass__ = classmethod(__init_subclass__)
        BasePlatformAdapter._rfp_subclass_hook = True
    for mod in list(sys.modules.values()):
        cls = getattr(mod, "TelegramAdapter", None)
        if isinstance(cls, type) and issubclass(cls, BasePlatformAdapter):
            _patch_telegram_class(cls)




# --------------------------------------------------------------------------- /turbofooter command
def _session_target():
    """``(platform_key, chat_id)`` of the chat that sent the command ("" outside a gateway session)."""
    with contextlib.suppress(Exception):
        from gateway.session_context import get_session_env
        return (get_session_env("HERMES_SESSION_PLATFORM") or "").lower(), get_session_env("HERMES_SESSION_CHAT_ID")
    return "", ""


def _config_path() -> Path:
    with contextlib.suppress(Exception):
        from gateway.run import _gateway_config_home
        return _gateway_config_home() / "config.yaml"
    from hermes_constants import get_hermes_home
    return get_hermes_home() / "config.yaml"


def _save_leaf(leaf: str, value) -> None:
    from gateway.slash_commands import _write_raw_config_leaf
    _write_raw_config_leaf(_config_path(), ("display", "runtime_footer", leaf), value)


async def _reset_surface(platform: str, chat_id, style: str, notice: str) -> bool:
    """Undo what ``style`` left in the chat UI; True when ``notice`` was delivered as part of it."""
    if platform != "telegram" or not chat_id or style not in ("menu", "keyboard", "placeholder"):
        return False
    for adapter in list(_adapters):
        if not getattr(adapter, "_bot", None):
            continue
        try:
            return bool(await adapter.reset_footer_surface(chat_id, style, notice.replace("**", "").replace("`", "")))
        except Exception as e:  # noqa: BLE001
            logger.warning("turbofooter: surface reset failed: %s", e)
    logger.info("turbofooter: no live Telegram adapter to reset %s for chat %s", style, chat_id)
    return False


def _parse_fields(text: str):
    fields = [f for f in re.split(r"[\s,]+", text) if f]
    return fields if fields and not set(fields) - set(KNOWN_FIELDS) else None


async def turbofooter_command(raw_args: str = "") -> Optional[str]:
    """``/turbofooter`` handler (plugin slash command: raw args in, reply text out)."""
    arg = (raw_args or "").strip()
    low = arg.lower()
    from gateway.runtime_footer import resolve_footer_config
    platform, chat_id = _session_target()
    try:
        effective = resolve_footer_config(_gateway_config(), platform or None)
    except Exception as e:  # noqa: BLE001
        return f"⚠️ Could not read config: {e}"
    style = effective.get("style", "text")

    def save(leaf, value) -> Optional[str]:
        try:
            _save_leaf(leaf, value)
        except Exception as e:  # noqa: BLE001
            return f"⚠️ Could not save config: {e}"
        return None

    if low in ("", "status", "?", "help"):
        url = effective.get("menu_url") or DEFAULT_MENU_URL
        status = (f"📎 Runtime footer: **{'on' if effective.get('enabled') else 'off'}** · style `{style}`\n"
                  f"Fields: `{', '.join(effective.get('fields') or [])}`\n"
                  f"Menu button fields: `{', '.join(effective.get('menu_fields') or DEFAULT_MENU_FIELDS)}`\n"
                  f"Menu button opens: {'built-in details page' if url == DEFAULT_MENU_URL else f'`{url}`'}")
        return status if low in ("status", "?") else status + "\n\n" + USAGE
    if low in ("on", "off"):
        err = save("enabled", low == "on")
        if err:
            return err
        reply = f"📎 Runtime footer: **{low}** · style `{style}`"
        if low == "off" and await _reset_surface(platform, chat_id, style, reply):
            return None
        return reply
    if low in FOOTER_STYLES:
        err = save("style", low)
        if err:
            return err
        notice = f"📎 Runtime footer style: **{low}** — shows up with the next reply" + (
            "" if effective.get("enabled") else " (footer is off — `/turbofooter on`)")
        if low != "text" and platform and platform != "telegram":
            notice += f"\n(only Telegram supports `{low}`; {platform} keeps the text footer)"
        if style != low and await _reset_surface(platform, chat_id, style, notice):
            return None
        return notice
    head, _, rest = low.partition(" ")
    if head in ("fields", "menu-fields", "menu_fields"):
        fields = _parse_fields(rest)
        if not fields:
            return f"Usage: `/turbofooter {head} a,b,c`\nKnown: `" + ", ".join(KNOWN_FIELDS) + "`"
        leaf = "fields" if head == "fields" else "menu_fields"
        return save(leaf, fields) or f"📎 Runtime footer {leaf.replace('_', ' ')}: `{', '.join(fields)}`"
    if head == "url":
        value = arg.partition(" ")[2].strip()
        if value.lower() in ("default", "reset", ""):
            return save("menu_url", None) or "📎 Menu button opens the built-in details page"
        if not value.startswith("https://"):
            return "Menu button link must be an `https://` URL (Telegram web-app rule)"
        return save("menu_url", value) or f"📎 Menu button opens `{value}`"
    return USAGE
