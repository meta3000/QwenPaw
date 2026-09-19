# -*- coding: utf-8 -*-
"""Optional ``--deep`` network reachability checks for enabled channels.

Failures are reported as non-fatal notes (firewalls and offline use are
common). Built-in probes live here; custom channels can override
:class:`~qwenpaw.app.channels.base.BaseChannel`.doctor_connectivity_notes.
"""
from __future__ import annotations

from typing import Any, Callable

from ..app.channels.registry import get_channel_registry
from ..config.config import (
    ChannelConfig,
    Config,
)
from .doctor_checks import _effective_channels_mcp, _read_workspace_agent_json

ChannelProbe = Callable[[str, Any, float], list[str]]

# No built-in probes — only the console channel is supported.
_BUILTIN_PROBES: dict[str, ChannelProbe] = {}


def _channel_enabled(sub: Any) -> bool:
    return bool(getattr(sub, "enabled", False))


def collect_deep_channel_connectivity_notes(
    cfg: Config,
    timeout: float,
) -> list[str]:
    """Run reachability probes for each enabled channel (per agent profile)."""
    notes: list[str] = []
    reg = get_channel_registry()

    for agent_id, ref in cfg.agents.profiles.items():
        raw = _read_workspace_agent_json(ref)
        ch, _ = _effective_channels_mcp(cfg, raw)

        for name in ChannelConfig.model_fields:
            sub = getattr(ch, name, None)
            if sub is None or not _channel_enabled(sub):
                continue
            if name == "console":
                continue

            cls = reg.get(name)
            custom_lines: list[str] = []
            if cls is not None:
                try:
                    custom_lines = cls.doctor_connectivity_notes(
                        agent_id,
                        sub,
                        timeout=timeout,
                    )
                except (
                    Exception
                ) as exc:  # pylint: disable=broad-exception-caught
                    custom_lines = [
                        f"{agent_id}: {name}: "
                        f"doctor_connectivity_notes error: {exc}",
                    ]
            if custom_lines:
                notes.extend(custom_lines)
                continue

            probe = _BUILTIN_PROBES.get(name)
            if probe is None:
                continue
            try:
                notes.extend(probe(agent_id, sub, timeout))
            except Exception as exc:  # pylint: disable=broad-exception-caught
                notes.append(
                    f"{agent_id}: {name}: connectivity probe error: {exc}",
                )

        extra = getattr(ch, "__pydantic_extra__", None) or {}
        for key, val in extra.items():
            en = False
            if isinstance(val, dict):
                en = bool(val.get("enabled"))
            elif hasattr(val, "enabled"):
                en = bool(getattr(val, "enabled"))
            if not en:
                continue
            cls = reg.get(key)
            if cls is None:
                notes.append(
                    f"{agent_id}: plugin channel {key!r} enabled — "
                    "not in registry; add a BaseChannel subclass "
                    "or skip --deep",
                )
                continue
            try:
                notes.extend(
                    cls.doctor_connectivity_notes(
                        agent_id,
                        val,
                        timeout=timeout,
                    ),
                )
            except Exception as exc:  # pylint: disable=broad-exception-caught
                notes.append(
                    f"{agent_id}: {key}: doctor_connectivity_notes error: "
                    f"{exc}",
                )

    return notes
