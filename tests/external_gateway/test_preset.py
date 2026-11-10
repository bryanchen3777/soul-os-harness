"""Preset resolver tests. Pure offline: tmp files + monkeypatched env only.

No network, no ports, no real keys. Every key value in this file is an
uppercase hyphenated SENTINEL that matches no real credential shape.
"""

import json
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from src.external_gateway.preset import DEEPSEEK_KEY_REF_DEFAULT
from src.external_gateway.preset import OLLAMA_BASE_URL_DEFAULT
from src.external_gateway.preset import OLLAMA_BASE_URL_ENV
from src.external_gateway.preset import Preset
from src.external_gateway.preset import _fail
from src.external_gateway.preset import load_preset
from src.interfaces.schema import InterfaceError


REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_FILE = REPO_ROOT / "configs" / "models" / "example-do-not-deploy.json"

REF = "T7_PRESET_REF"
SENTINEL = "T7-PRESET-SENTINEL-KEY-9Z"
SENTINEL_FRAGS = (SENTINEL, SENTINEL[:8], SENTINEL[-4:])


def _preset(name="p1", provider="deepseek", model="m1", params=None, key_ref=REF):
    if params is None:
        params = {"temperature": 0.5, "max_tokens": 512}
    return {
        "name": name,
        "provider": provider,
        "model": model,
        "params": params,
        "key_ref": key_ref,
    }


def _write(path, presets):
    path.write_text(json.dumps({"presets": presets}), encoding="utf-8")
    return path


def _write_one(tmp_path, **over):
    return _write(tmp_path / "p.json", [_preset(**over)])


def _assert_no_leak(excinfo):
    text = str(excinfo.value)
    for frag in SENTINEL_FRAGS:
        assert frag and frag not in text, frag


# -- happy paths ----------------------------------------------------------

def test_load_deepseek_preset_ok(tmp_path, monkeypatch):
    monkeypatch.setenv(REF, SENTINEL)
    path = _write_one(tmp_path)
    out = load_preset(path, "p1")
    assert isinstance(out, Preset)
    assert out.name == "p1"
    assert out.provider == "deepseek"
    assert out.model == "m1"
    assert out.params["temperature"] == 0.5
    assert out.params["max_tokens"] == 512
    assert out.key_ref == REF
    assert out.api_key == SENTINEL
    assert out.base_url is None


def test_single_preset_file_loads_without_name(tmp_path, monkeypatch):
    monkeypatch.setenv(REF, SENTINEL)
    out = load_preset(_write_one(tmp_path))
    assert out.name == "p1"


def test_two_preset_file_selects_by_name(tmp_path, monkeypatch):
    monkeypatch.setenv(REF, SENTINEL)
    path = _write(
        tmp_path / "p.json",
        [
            _preset(name="first", provider="ollama"),
            _preset(name="second", provider="deepseek"),
        ],
    )
    out = load_preset(path, "second")
    assert out.name == "second"
    assert out.provider == "deepseek"


def test_ollama_base_url_defaults_without_dialing(tmp_path, monkeypatch):
    monkeypatch.setenv(REF, SENTINEL)
    monkeypatch.delenv(OLLAMA_BASE_URL_ENV, raising=False)
    out = load_preset(_write_one(tmp_path, provider="ollama"), "p1")
    assert out.base_url == OLLAMA_BASE_URL_DEFAULT
    assert out.base_url == "http://localhost:11434"


def test_ollama_base_url_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv(REF, SENTINEL)
    monkeypatch.setenv(OLLAMA_BASE_URL_ENV, "http://example-do-not-deploy.invalid:11434")
    out = load_preset(_write_one(tmp_path, provider="ollama"), "p1")
    assert out.base_url == "http://example-do-not-deploy.invalid:11434"


def test_params_base_url_wins_over_env(tmp_path, monkeypatch):
    monkeypatch.setenv(REF, SENTINEL)
    monkeypatch.setenv(OLLAMA_BASE_URL_ENV, "http://example-do-not-deploy.invalid:11434")
    params = {"base_url": "http://params-wins.invalid:11434"}
    out = load_preset(_write_one(tmp_path, provider="ollama", params=params), "p1")
    assert out.base_url == "http://params-wins.invalid:11434"


def test_deepseek_default_key_ref_name_is_frozen():
    assert DEEPSEEK_KEY_REF_DEFAULT == "DEEPSEEK_API_KEY"


def test_preset_is_immutable_snapshot(tmp_path, monkeypatch):
    monkeypatch.setenv(REF, SENTINEL)
    out = load_preset(_write_one(tmp_path), "p1")
    with pytest.raises(FrozenInstanceError):
        out.name = "q"
    with pytest.raises(TypeError):
        out.params["temperature"] = 0.0


# -- fail-closed: every bad input raises, no partial object returns --------

def test_missing_key_fail_closed(tmp_path, monkeypatch):
    monkeypatch.delenv(REF, raising=False)
    path = _write_one(tmp_path)
    with pytest.raises(InterfaceError) as ei:
        load_preset(path, "p1")
    assert ei.value.status == 500
    assert ei.value.code == "invalid_preset"
    assert REF in str(ei.value)


def test_empty_key_fail_closed(tmp_path, monkeypatch):
    monkeypatch.setenv(REF, "")
    with pytest.raises(InterfaceError):
        load_preset(_write_one(tmp_path), "p1")


@pytest.mark.parametrize("provider", ["anthropic", "DEEPSEEK", "", "ollama2", 123, None])
def test_unknown_provider_refused(tmp_path, monkeypatch, provider):
    monkeypatch.setenv(REF, SENTINEL)
    with pytest.raises(InterfaceError) as ei:
        load_preset(_write_one(tmp_path, provider=provider), "p1")
    assert ei.value.code == "invalid_preset"
    _assert_no_leak(ei)


def test_extra_top_level_key_refused(tmp_path, monkeypatch):
    monkeypatch.setenv(REF, SENTINEL)
    bad = _preset()
    bad["extra"] = 1
    with pytest.raises(InterfaceError) as ei:
        load_preset(_write(tmp_path / "p.json", [bad]), "p1")
    _assert_no_leak(ei)


def test_missing_top_level_key_refused(tmp_path, monkeypatch):
    monkeypatch.setenv(REF, SENTINEL)
    bad = _preset()
    del bad["model"]
    with pytest.raises(InterfaceError) as ei:
        load_preset(_write(tmp_path / "p.json", [bad]), "p1")
    _assert_no_leak(ei)


def test_extra_params_key_refused(tmp_path, monkeypatch):
    monkeypatch.setenv(REF, SENTINEL)
    params = {"temperature": 0.5, "top_p": 0.9}
    with pytest.raises(InterfaceError) as ei:
        load_preset(_write_one(tmp_path, params=params), "p1")
    _assert_no_leak(ei)


@pytest.mark.parametrize(
    "params",
    [
        {"temperature": True},
        {"temperature": "hot"},
        {"max_tokens": 0},
        {"max_tokens": -5},
        {"max_tokens": True},
        {"max_tokens": "512"},
        {"base_url": ""},
        {"base_url": 11434},
        "not-a-mapping",
    ],
)
def test_bad_params_refused(tmp_path, monkeypatch, params):
    monkeypatch.setenv(REF, SENTINEL)
    with pytest.raises(InterfaceError) as ei:
        load_preset(_write_one(tmp_path, params=params), "p1")
    _assert_no_leak(ei)


@pytest.mark.parametrize("key_ref", ["lowercase_ref", "has-hyphen", "", "has space", 123, None])
def test_bad_key_ref_format_refused(tmp_path, monkeypatch, key_ref):
    with pytest.raises(InterfaceError):
        load_preset(_write_one(tmp_path, key_ref=key_ref), "p1")


def test_key_ref_suspected_plaintext_refused(tmp_path, monkeypatch):
    # 25 chars: the ticket's own example name is over the 24-char line,
    # so the loader must treat it as pasted plaintext, not a variable name.
    long_ref = "EXAMPLE_DO_NOT_DEPLOY_KEY"
    assert len(long_ref) > 24
    monkeypatch.setenv(long_ref, SENTINEL)
    with pytest.raises(InterfaceError) as ei:
        load_preset(_write_one(tmp_path, key_ref=long_ref), "p1")
    _assert_no_leak(ei)


def test_key_ref_24_chars_is_the_boundary(tmp_path, monkeypatch):
    ok_ref = "A" * 24
    monkeypatch.setenv(ok_ref, SENTINEL)
    out = load_preset(_write_one(tmp_path, key_ref=ok_ref), "p1")
    assert out.key_ref == ok_ref
    bad_ref = "A" * 25
    monkeypatch.setenv(bad_ref, SENTINEL)
    with pytest.raises(InterfaceError):
        load_preset(_write_one(tmp_path, key_ref=bad_ref), "p1")


def test_ambiguous_without_name_refused(tmp_path, monkeypatch):
    monkeypatch.setenv(REF, SENTINEL)
    path = _write(tmp_path / "p.json", [_preset(name="a"), _preset(name="b")])
    with pytest.raises(InterfaceError) as ei:
        load_preset(path)
    _assert_no_leak(ei)


def test_unknown_name_refused(tmp_path, monkeypatch):
    monkeypatch.setenv(REF, SENTINEL)
    with pytest.raises(InterfaceError) as ei:
        load_preset(_write_one(tmp_path), "nope")
    _assert_no_leak(ei)


def test_duplicate_names_refused(tmp_path, monkeypatch):
    monkeypatch.setenv(REF, SENTINEL)
    path = _write(tmp_path / "p.json", [_preset(name="dup"), _preset(name="dup")])
    with pytest.raises(InterfaceError):
        load_preset(path, "dup")


def test_bad_json_refused(tmp_path):
    path = tmp_path / "p.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(InterfaceError):
        load_preset(path, "p1")


def test_missing_file_refused(tmp_path):
    with pytest.raises(InterfaceError):
        load_preset(tmp_path / "absent.json", "p1")


@pytest.mark.parametrize(
    "raw",
    [
        [],
        {},
        {"presets": []},
        {"presets": {}, },
        {"presets": [_preset()], "extra": 1},
        {"other": [_preset()]},
    ],
)
def test_bad_envelope_refused(tmp_path, monkeypatch, raw):
    monkeypatch.setenv(REF, SENTINEL)
    path = tmp_path / "p.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(InterfaceError):
        load_preset(path, "p1")


# -- key zero-leak ----------------------------------------------------------

def test_fail_refuses_leaking_message():
    """Teeth: _fail itself rejects any message carrying key material."""
    with pytest.raises(AssertionError):
        _fail("p", "boom " + SENTINEL, key=SENTINEL)
    with pytest.raises(AssertionError):
        _fail("p", "boom " + SENTINEL[:8], key=SENTINEL)
    with pytest.raises(AssertionError):
        _fail("p", "boom " + SENTINEL[-4:], key=SENTINEL)


def test_fail_clean_message_still_raises_preset_error():
    with pytest.raises(InterfaceError) as ei:
        _fail("key_ref", "clean message")
    assert ei.value.status == 500
    assert ei.value.code == "invalid_preset"


def test_repr_shows_only_key_ref_name(tmp_path, monkeypatch):
    monkeypatch.setenv(REF, SENTINEL)
    out = load_preset(_write_one(tmp_path), "p1")
    text = repr(out)
    assert text == f"Preset(key_ref={REF!r})"
    assert SENTINEL not in text
    assert str(out) == text


# -- example file is genuinely not deployable --------------------------------

def test_example_file_is_not_deployable(monkeypatch):
    assert EXAMPLE_FILE.is_file()
    raw = json.loads(EXAMPLE_FILE.read_text(encoding="utf-8"))
    names = sorted(e["name"] for e in raw["presets"])
    assert names == ["example-deepseek-do-not-deploy", "example-ollama-do-not-deploy"]
    for entry in raw["presets"]:
        assert "example-" in entry["name"]
        assert "do-not-deploy" in entry["name"]
        assert "do-not-deploy" in entry["model"]
        assert entry["key_ref"].startswith("EXAMPLE_")
        monkeypatch.delenv(entry["key_ref"], raising=False)
    with pytest.raises(InterfaceError):
        load_preset(EXAMPLE_FILE, "example-ollama-do-not-deploy")
    with pytest.raises(InterfaceError):
        load_preset(EXAMPLE_FILE, "example-deepseek-do-not-deploy")
    with pytest.raises(InterfaceError):
        load_preset(EXAMPLE_FILE)
