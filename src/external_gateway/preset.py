"""Model-preset file resolver: {preset} -> (provider + model + params + key_ref).

Pure offline: reads one JSON file, takes the key from the environment, holds
everything in memory. No writes, no network, no ports, no logging of secrets.

Fail-closed: missing key / bad schema / unknown provider all raise
InterfaceError(500, "invalid_preset", ...) and return no partial object.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import field
from types import MappingProxyType

from src.interfaces.schema import InterfaceError


_CODE = "invalid_preset"

# Strict key whitelist: a preset object holds exactly these five keys.
_PRESET_KEYS = frozenset({"name", "provider", "model", "params", "key_ref"})

# S4 first batch: these four providers only. Anything else is refused at load.
_PROVIDERS = frozenset({"deepseek", "openai", "gemini", "ollama"})

# Only these model params may appear inside "params". Anything else is refused.
_PARAM_KEYS = frozenset({"temperature", "max_tokens", "base_url"})

# key_ref names an environment variable, nothing else.
_KEY_REF_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
# Longer than this looks like pasted plaintext, not a variable name. Refuse.
_KEY_REF_MAX_LEN = 24

# S2-frozen env names (S4-W2 pins them; values are still injected locally).
DEEPSEEK_KEY_REF_DEFAULT = "DEEPSEEK_API_KEY"
OLLAMA_BASE_URL_ENV = "OLLAMA_BASE_URL"
# Default string only. This module never dials it: no sockets, no network.
OLLAMA_BASE_URL_DEFAULT = "http://localhost:11434"


def _fail(param, message="Invalid preset configuration.", *, key=None):
    """Refuse to load. Never lets key material reach the error message.

    Every loader error routes through here. When the key is already loaded,
    the message is checked against the key and its first-8 / last-4
    characters first: a leaking message raises AssertionError instead of
    being built (fail loudly in dev, never emit in prod).
    """
    if isinstance(key, str) and key:
        frags = [key]
        if len(key) >= 8:
            frags.append(key[:8])
        if len(key) >= 4:
            frags.append(key[-4:])
        for frag in frags:
            if frag and frag in message:
                raise AssertionError("preset error message would leak key material")
    raise InterfaceError(500, _CODE, param, message)


@dataclass(frozen=True)
class Preset:
    """One resolved preset, fully held in memory. The key never leaves it."""

    name: str
    provider: str
    model: str
    params: Mapping = field(repr=False)
    key_ref: str = field()
    api_key: str = field(repr=False)
    base_url: str | None = None

    def __repr__(self):
        # Shows the key_ref NAME only. Never the key, never params.
        return f"Preset(key_ref={self.key_ref!r})"


def _check_name(value, *, key=None):
    if not isinstance(value, str) or not value:
        _fail("name", "Invalid preset configuration.", key=key)


def _check_model(value, *, key=None):
    if not isinstance(value, str) or not value:
        _fail("model", "Invalid preset configuration.", key=key)


def _check_provider(value, *, key=None):
    if not isinstance(value, str) or value not in _PROVIDERS:
        _fail("provider", "Invalid preset configuration.", key=key)


def _check_params(value, *, key=None):
    if not isinstance(value, Mapping):
        _fail("params", "Invalid preset configuration.", key=key)
    for k in value:
        if k not in _PARAM_KEYS:
            _fail("params", "Invalid preset configuration.", key=key)
    if "temperature" in value:
        t = value["temperature"]
        if isinstance(t, bool) or not isinstance(t, (int, float)):
            _fail("params", "Invalid preset configuration.", key=key)
    if "max_tokens" in value:
        m = value["max_tokens"]
        if type(m) is not int or m <= 0:
            _fail("params", "Invalid preset configuration.", key=key)
    if "base_url" in value:
        b = value["base_url"]
        if not isinstance(b, str) or not b:
            _fail("params", "Invalid preset configuration.", key=key)


def _check_key_ref(value, *, key=None):
    if not isinstance(value, str):
        _fail("key_ref", "Invalid preset configuration.", key=key)
    if not _KEY_REF_RE.match(value):
        _fail("key_ref", "Invalid preset configuration.", key=key)
    if len(value) > _KEY_REF_MAX_LEN:
        _fail("key_ref", "Invalid preset configuration.", key=key)


def _validate_preset_dict(obj, *, key=None):
    """Validate one preset object. Returns normalized (name, provider, model,
    params-dict, key_ref). Raises on anything else; returns nothing partial."""
    if not isinstance(obj, Mapping):
        _fail("preset", "Invalid preset configuration.", key=key)
    keys = list(obj.keys())
    if len(keys) != len(_PRESET_KEYS):
        _fail("preset", "Invalid preset configuration.", key=key)
    for k in _PRESET_KEYS:
        if k not in obj:
            _fail("preset", "Invalid preset configuration.", key=key)
    for k in keys:
        if k not in _PRESET_KEYS:
            _fail("preset", "Invalid preset configuration.", key=key)
    _check_name(obj["name"], key=key)
    _check_provider(obj["provider"], key=key)
    _check_model(obj["model"], key=key)
    _check_params(obj["params"], key=key)
    _check_key_ref(obj["key_ref"], key=key)
    return (obj["name"], obj["provider"], obj["model"], dict(obj["params"]), obj["key_ref"])


def _parse_file(path):
    """Read + schema-check the preset file. No env access, no key involved."""
    try:
        text = open(path, encoding="utf-8").read()
    except (OSError, ValueError):
        _fail("preset", "Invalid preset configuration.")
    try:
        raw = json.loads(text)
    except (ValueError, TypeError):
        _fail("preset", "Invalid preset configuration.")
    if not isinstance(raw, Mapping):
        _fail("preset", "Invalid preset configuration.")
    if list(raw.keys()) != ["presets"]:
        _fail("preset", "Invalid preset configuration.")
    entries = raw["presets"]
    if not isinstance(entries, list) or not entries:
        _fail("preset", "Invalid preset configuration.")
    validated = [_validate_preset_dict(e) for e in entries]
    names = [v[0] for v in validated]
    if len(set(names)) != len(names):
        _fail("preset", "Invalid preset configuration.")
    return validated


def load_preset(path, name=None):
    """Load one preset from file and resolve its key from the environment.

    ``path`` is a JSON file shaped like ``{"presets": [<preset>, ...]}``,
    each preset holding exactly {name, provider, model, params, key_ref}.
    ``name`` selects which preset to load; it may be omitted only when the
    file holds exactly one preset (otherwise the choice is ambiguous and we
    refuse). The key is taken from ``os.environ[key_ref]`` and held only in
    the returned Preset. Anything off -- bad schema, unknown provider,
    key_ref that is not a plain env-var name (or longer than 24 chars, i.e.
    suspected pasted plaintext), missing/empty key -- raises InterfaceError
    and returns no partial object.
    """
    validated = _parse_file(path)
    if name is None:
        if len(validated) != 1:
            _fail("preset", "Invalid preset configuration.")
        picked = validated[0]
    else:
        if not isinstance(name, str):
            _fail("name", "Invalid preset configuration.")
        picked = None
        for v in validated:
            if v[0] == name:
                picked = v
                break
        if picked is None:
            _fail("name", "Invalid preset configuration.")
    pname, provider, model, params, key_ref = picked
    key = os.environ.get(key_ref)
    if not isinstance(key, str) or not key:
        _fail(
            "key_ref",
            f"Invalid preset configuration: environment variable {key_ref!r} is not set.",
            key=key,
        )
    base = params.get("base_url")
    if provider == "ollama" and base is None:
        base = os.environ.get(OLLAMA_BASE_URL_ENV) or OLLAMA_BASE_URL_DEFAULT
    return Preset(
        name=pname,
        provider=provider,
        model=model,
        params=MappingProxyType(params),
        key_ref=key_ref,
        api_key=key,
        base_url=base,
    )
