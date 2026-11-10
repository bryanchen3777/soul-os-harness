"""Soul/preset offline routing layer (S5): whitelist + registry route + preset.

Pure offline: reads one registry object (already in memory) plus one preset
JSON file, takes the key from the environment, holds everything in memory.
No HTTP, no sockets, no ports, no server, no logging of secrets.

S6 assembles HTTP on top of ``resolve``; S7 needs Owner-injected keys and an
explicit launch authorization. This module must never grow either.

Whitelist evidence (sample-level, NOT a deployment confirmation):
  - ``src/llm/proxy.py`` ``AGENT_SHORT_NAMES`` maps ``agent_<slug>`` to the
    short soul name for all five of rem / mai / ruka / akane / mahiru
    (read once via AST during design; this module never imports proxy).
  - Registry route samples bind ``soul_alias="rem"`` to
    ``agent_id="agent_rem"`` (see ``tests/interfaces/test_real_context_slice.py``).
  The five ``<soul> -> agent_<soul>`` pairs below are pinned from those two
  samples. Whether production actually deploys them is a separate, later
  confirmation owned by S6/S7 -- sample evidence must not be presented as one.

Resolution order (each step fail-closed):
  1. Whitelist: ``soul`` must be one of the five, ``preset`` a non-empty
     string. Anything else is 404 ``route_not_found`` -- before any env read.
  2. ``resolve_route(registry, soul_alias=soul, backend_alias=preset)``:
     exact match only, no guessing, no fallback. Missing route stays the
     existing 404 ``route_not_found``.
  3. Mapping check: the matched route's ``agent_id`` must equal
     ``agent_<soul>``. A wrong mapping is refused with 500.
  4. ``load_preset(preset_path, name=preset)``: an unknown preset name inside
     an otherwise well-formed config is 404 ``route_not_found``; a malformed
     config or a missing/empty key keeps load_preset's own 500
     ``invalid_preset`` untouched (fixed safe copy, never echoes secrets).
     No schema rule is duplicated here: step 4 only probes name membership,
     all validation stays inside ``preset.load_preset``.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from types import MappingProxyType

from src.external_gateway.preset import Preset
from src.external_gateway.preset import load_preset
from src.interfaces.config import resolve_route
from src.interfaces.schema import InterfaceError
from src.interfaces.schema import Registry
from src.interfaces.schema import RouteBinding


# Fixed five-soul whitelist (see module docstring for evidence + limits).
_SOUL_AGENT_IDS = MappingProxyType(
    {
        "rem": "agent_rem",
        "mai": "agent_mai",
        "ruka": "agent_ruka",
        "akane": "agent_akane",
        "mahiru": "agent_mahiru",
    }
)

#: Souls this layer serves. Frozen at five; S6/S7 widen it only by Owner order.
SOUL_WHITELIST = frozenset(_SOUL_AGENT_IDS)

#: soul -> agent_id. Read-only view of the pinned mapping.
SOUL_AGENT_MAP = _SOUL_AGENT_IDS

_FIXED_404_MESSAGE = "Unknown route."
_FIXED_500_MESSAGE = "Invalid gateway route configuration."


def _not_found(param="route"):
    raise InterfaceError(404, "route_not_found", param, _FIXED_404_MESSAGE)


def _broken(param="route"):
    raise InterfaceError(500, "invalid_route", param, _FIXED_500_MESSAGE)


def _preset_names_in_file(path):
    """Best-effort preset-name membership probe. Returns a set or None.

    Returns None when the file is not a well-formed ``{"presets": [...]}``
    envelope; the caller then delegates to ``load_preset``, which raises the
    authoritative 500. Membership only -- no validation, no env access.
    """
    try:
        text = open(path, encoding="utf-8").read()
    except (OSError, ValueError):
        return None
    try:
        raw = json.loads(text)
    except (ValueError, TypeError):
        return None
    if not isinstance(raw, Mapping):
        return None
    entries = raw.get("presets")
    if not isinstance(entries, list):
        return None
    names = set()
    for entry in entries:
        if not isinstance(entry, Mapping):
            return None
        name = entry.get("name")
        if not isinstance(name, str):
            return None
        names.add(name)
    return names


def resolve(registry: Registry, preset_path, soul: str, preset: str) -> tuple[RouteBinding, Preset]:
    """Bind (soul, preset) to its (RouteBinding, Preset). Pure offline.

    ``preset`` doubles as the registry ``backend_alias`` and the preset file
    entry ``name``: routing matches ``soul_alias == soul`` and
    ``backend_alias == preset`` exactly, then loads preset ``name == preset``.
    Unknown soul / missing route / unknown preset-in-valid-config all raise
    404 ``route_not_found``; wrong agent mapping, malformed config, or a
    missing/empty key raise 500 with a fixed safe message. Illegal routes
    return before any environment read, so no key material is ever touched
    on a 404 path.
    """
    if not isinstance(soul, str) or soul not in SOUL_WHITELIST:
        _not_found()
    if not isinstance(preset, str) or not preset:
        _not_found()
    try:
        route = resolve_route(registry, soul, preset)
    except InterfaceError:
        _not_found()
    if route.agent_id != _SOUL_AGENT_IDS[soul]:
        _broken()
    names = _preset_names_in_file(preset_path)
    if names is not None and preset not in names:
        _not_found()
    preset_obj = load_preset(preset_path, name=preset)
    return (route, preset_obj)
