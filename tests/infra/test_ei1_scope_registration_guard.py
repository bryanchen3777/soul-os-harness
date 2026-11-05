"""EI-1-HG scope registration guard (AST-only, no import/subprocess, no repo write)."""
from __future__ import annotations

import ast
import copy
import hashlib
import json
from pathlib import Path

import pytest

# ── EI-1-HG fixed scope (from pre-modification authoritative baseline) ──────
# Transcribed from verification/scope-authoritative-baseline.json which was
# computed externally by main brain via repo .venv pure-AST; this worker only
# read it and did not execute or self-calculate any hash.
NEW_FILES = (
    "src/interfaces/__init__.py",
    "src/interfaces/schema.py",
    "src/interfaces/config.py",
    "src/interfaces/identity.py",
    "src/interfaces/classification.py",
    "src/interfaces/context_policy.py",
)

# method: sha256(json.dumps(sorted(allowed),ensure_ascii=True,separators=(',',':')).encode('utf-8'))
EXPECTED_ORIGINAL_ALLOWED_DIGEST = "e7689f3572cfd1b12ee3499170dc5866b324df773491645bda3147f514088929"
# method: sha256(json.dumps(frozen_ordered,ensure_ascii=True,separators=(',',':')).encode('utf-8')) in original tuple order
EXPECTED_FROZEN_DIGEST = "f0a47fcc450baa55c0a5fadeecba26753982903a445ca270603b6e03ea058739"

# Baseline original allowed (sorted) — constant copy for pure-set original-guard proof.
# Must NOT be derived from the modified file under test.
_BASELINE_SORTED_ORIGINAL_ALLOWED = [
    "src/agency/inner_life_gate.py",
    "src/agent/consciousness.py",
    "src/agent/emotion.py",
    "src/agent/registry.py",
    "src/agent/speaker_token.py",
    "src/fsio/__init__.py",
    "src/fsio/atomic_append.py",
    "src/inner_life/contradiction_accumulator_prototype.py",
    "src/inner_life/contradiction_backend_llm.py",
    "src/inner_life/contradiction_judge_prototype.py",
    "src/inner_life/contradiction_prefilter.py",
    "src/inner_life/contradiction_probe.py",
    "src/inner_life/drive_profile.py",
    "src/inner_life/elevation_adapter.py",
    "src/inner_life/epistemic_action.py",
    "src/inner_life/epistemic_tension_scheduler.py",
    "src/inner_life/piggyback_queue.py",
    "src/inner_life/recall_prompt.py",
    "src/inner_life/revision_chain.py",
    "src/inner_life/tension_closure_loop.py",
    "src/inner_life/tension_scoring.py",
    "src/inner_life/tension_thinking.py",
    "src/io/accept_resilience.py",
    "src/io/channels/router.py",
    "src/llm/proxy.py",
    "src/memory/sage/graph_store.py",
    "src/memory/sage/provider.py",
    "src/memory/store.py",
    "src/memory/v1/store.py",
    "src/paths.py",
    "src/soul/scheduler.py",
    "src/voice/fish_tts.py",
    "src/world/world_log.py",
]

_TARGET_REL = Path("tests/test_observability_completeness_1.py")


def _digest_sorted(items) -> str:
    canon = json.dumps(sorted(items), ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def _digest_ordered(items) -> str:
    canon = json.dumps(list(items), ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def _extract_str_list(node: ast.AST):
    if not isinstance(node, (ast.Set, ast.List, ast.Tuple)):
        return None
    out = []
    for elt in node.elts:
        if not isinstance(elt, ast.Constant) or not isinstance(elt.value, str):
            return None
        out.append(elt.value)
    return out


def _targets_name(node: ast.AST, name: str) -> bool:
    if isinstance(node, ast.Assign):
        for t in node.targets:
            if isinstance(t, ast.Name) and t.id == name:
                return True
            if isinstance(t, ast.Starred) and isinstance(t.value, ast.Name) and t.value.id == name:
                return True
        return False
    if isinstance(node, (ast.AnnAssign, ast.AugAssign, ast.NamedExpr)):
        tgt = getattr(node, "target", getattr(node, "targets", None))
        targets = tgt if isinstance(tgt, list) else [tgt]
        for t in targets:
            if isinstance(t, ast.Name) and t.id == name:
                return True
        return False
    return False


def validate_scope(source_text: str) -> bool:
    """Pure AST check of TestBoundedAndFrozenDiscipline.test_no_frozen_contract_file_touched.

    Returns False on syntax/literal/structure error. True only if:
    - exactly one class TestBoundedAndFrozenDiscipline in module,
    - exactly one method test_no_frozen_contract_file_touched in that class,
    - exactly one allowed assignment and exactly one frozen_dirs assignment
      inside that method (a second assignment bypass is rejected even when
      the first literal is correct),
    - allowed/frozen_dirs literals exist and are all-strings,
    - no duplicate literal entries, no wildcard/dir entry,
    - NEW_FILES (exactly these 6) subset of allowed,
    - digest(sorted(allowed - NEW_FILES)) == EXPECTED_ORIGINAL_ALLOWED_DIGEST,
    - digest(frozen in source order) == EXPECTED_FROZEN_DIGEST.
    Exact-set digest pins unknown wrapper/other-runtime/dir/wildcard/extra-scope.
    """
    try:
        tree = ast.parse(source_text)
        # Exactly one specified class in the whole module.
        class_nodes = [
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.ClassDef) and n.name == "TestBoundedAndFrozenDiscipline"
        ]
        if len(class_nodes) != 1:
            return False
        cls = None
        for node in tree.body:
            if isinstance(node, ast.ClassDef) and node.name == "TestBoundedAndFrozenDiscipline":
                cls = node
                break
        if cls is None:
            return False
        # Exactly one specified method as a direct child of that class.
        method_nodes = [
            n
            for n in cls.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            and n.name == "test_no_frozen_contract_file_touched"
        ]
        if len(method_nodes) != 1:
            return False
        fn = method_nodes[0]
        # Exactly one assignment per name inside the method (no second-assign bypass).
        allowed_assign_count = 0
        frozen_assign_count = 0
        for node in ast.walk(fn):
            if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.NamedExpr)):
                if _targets_name(node, "allowed"):
                    allowed_assign_count += 1
                if _targets_name(node, "frozen_dirs"):
                    frozen_assign_count += 1
        if allowed_assign_count != 1 or frozen_assign_count != 1:
            return False
        allowed_list = None
        frozen_list = None
        for node in ast.walk(fn):
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                nid = node.targets[0].id
                if nid == "allowed" and allowed_list is None:
                    allowed_list = _extract_str_list(node.value)
                elif nid == "frozen_dirs" and frozen_list is None:
                    if not isinstance(node.value, (ast.Tuple, ast.List)):
                        return False
                    frozen_list = _extract_str_list(node.value)
        if allowed_list is None or frozen_list is None:
            return False
        # literal hygiene: no duplicates
        if len(allowed_list) != len(set(allowed_list)):
            return False
        if len(frozen_list) != len(set(frozen_list)):
            return False
        # no wildcard / no directory entry (exact files only)
        for s in allowed_list:
            if ("*" in s) or ("?" in s) or ("[" in s) or s.endswith("/"):
                return False
            if s in ("src/interfaces", "src/interfaces/"):
                return False
        for s in frozen_list:
            if ("*" in s) or ("?" in s) or ("[" in s):
                return False
        # NEW_FILES fixed six subset
        if len(NEW_FILES) != 6 or len(set(NEW_FILES)) != 6:
            return False
        allowed_set = set(allowed_list)
        if not set(NEW_FILES) <= allowed_set:
            return False
        # old-scope digest must be unchanged (exact set, order-insensitive via sorted)
        remainder = allowed_set - set(NEW_FILES)
        if _digest_sorted(remainder) != EXPECTED_ORIGINAL_ALLOWED_DIGEST:
            return False
        # frozen full-sequence digest must be unchanged (order-sensitive)
        if _digest_ordered(frozen_list) != EXPECTED_FROZEN_DIGEST:
            return False
        return True
    except SyntaxError:
        return False
    except Exception:
        return False


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _read_target_source() -> str:
    return (_repo_root() / _TARGET_REL).read_text(encoding="utf-8")


def _with_modified_scope(source_text: str, *, new_allowed=None, new_frozen=None) -> str:
    """In-memory AST mutation only; never writes back to repo. Returns unparsed source."""
    tree = ast.parse(source_text)
    tree2 = copy.deepcopy(tree)
    cls = next(
        n for n in tree2.body if isinstance(n, ast.ClassDef) and n.name == "TestBoundedAndFrozenDiscipline"
    )
    fn = next(
        n
        for n in cls.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        and n.name == "test_no_frozen_contract_file_touched"
    )
    for node in ast.walk(fn):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            nid = node.targets[0].id
            if nid == "allowed" and new_allowed is not None:
                node.value = ast.Set(elts=[ast.Constant(value=s) for s in list(new_allowed)])
                ast.fix_missing_locations(node.value)
            if nid == "frozen_dirs" and new_frozen is not None:
                node.value = ast.Tuple(elts=[ast.Constant(value=s) for s in list(new_frozen)], ctx=ast.Load())
                ast.fix_missing_locations(node.value)
    return ast.unparse(tree2)


def _append_second_assignment(source_text: str, *, second_allowed=None, second_frozen=None) -> str:
    """Append a second allowed/frozen_dirs assignment to the method body (in-memory only)."""
    tree = ast.parse(source_text)
    tree2 = copy.deepcopy(tree)
    cls = next(
        n for n in tree2.body if isinstance(n, ast.ClassDef) and n.name == "TestBoundedAndFrozenDiscipline"
    )
    fn = next(
        n
        for n in cls.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        and n.name == "test_no_frozen_contract_file_touched"
    )
    if second_allowed is not None:
        stmt = ast.Assign(
            targets=[ast.Name(id="allowed", ctx=ast.Store())],
            value=ast.Set(elts=[ast.Constant(value=s) for s in list(second_allowed)]),
        )
        ast.fix_missing_locations(stmt)
        fn.body.append(stmt)
    if second_frozen is not None:
        stmt = ast.Assign(
            targets=[ast.Name(id="frozen_dirs", ctx=ast.Store())],
            value=ast.Tuple(elts=[ast.Constant(value=s) for s in list(second_frozen)], ctx=ast.Load()),
        )
        ast.fix_missing_locations(stmt)
        fn.body.append(stmt)
    return ast.unparse(tree2)


def _duplicate_class(source_text: str) -> str:
    """Duplicate the whole guard class (in-memory only)."""
    tree = ast.parse(source_text)
    tree2 = copy.deepcopy(tree)
    cls = next(
        n for n in tree2.body if isinstance(n, ast.ClassDef) and n.name == "TestBoundedAndFrozenDiscipline"
    )
    tree2.body.append(copy.deepcopy(cls))
    ast.fix_missing_locations(tree2)
    return ast.unparse(tree2)


def _duplicate_method(source_text: str) -> str:
    """Duplicate the guard method inside its class (in-memory only)."""
    tree = ast.parse(source_text)
    tree2 = copy.deepcopy(tree)
    cls = next(
        n for n in tree2.body if isinstance(n, ast.ClassDef) and n.name == "TestBoundedAndFrozenDiscipline"
    )
    fn = next(
        n
        for n in cls.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        and n.name == "test_no_frozen_contract_file_touched"
    )
    cls.body.append(copy.deepcopy(fn))
    ast.fix_missing_locations(tree2)
    return ast.unparse(tree2)


def _current_allowed_list(source_text: str):
    tree = ast.parse(source_text)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "TestBoundedAndFrozenDiscipline")
    fn = next(
        n
        for n in cls.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        and n.name == "test_no_frozen_contract_file_touched"
    )
    for node in ast.walk(fn):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            if node.targets[0].id == "allowed":
                vals = _extract_str_list(node.value)
                assert vals is not None
                return vals
    raise AssertionError("allowed literal not found")


def _current_frozen_list(source_text: str):
    tree = ast.parse(source_text)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "TestBoundedAndFrozenDiscipline")
    fn = next(
        n
        for n in cls.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        and n.name == "test_no_frozen_contract_file_touched"
    )
    for node in ast.walk(fn):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            if node.targets[0].id == "frozen_dirs":
                vals = _extract_str_list(node.value)
                assert vals is not None
                return vals
    raise AssertionError("frozen_dirs literal not found")


# ── positive ─────────────────────────────────────────────────────────────


def test_new_files_constant_is_exactly_six():
    assert tuple(NEW_FILES) == (
        "src/interfaces/__init__.py",
        "src/interfaces/schema.py",
        "src/interfaces/config.py",
        "src/interfaces/identity.py",
        "src/interfaces/classification.py",
        "src/interfaces/context_policy.py",
    )


def test_real_updated_file_positive():
    src = _read_target_source()
    assert validate_scope(src) is True


def test_only_registering_six_is_legal_positive():
    # Reordered allowed with exactly original+6 still passes (order-insensitive, no extra).
    src = _read_target_source()
    allowed = _current_allowed_list(src)
    assert set(NEW_FILES) <= set(allowed)
    reordered = list(reversed(allowed))
    mutated = _with_modified_scope(src, new_allowed=reordered)
    assert validate_scope(mutated) is True


# ── negative: unknown / wildcard / missing / old-deleted ──────────────────


def test_unknown_interfaces_wrapper_rejected():
    src = _read_target_source()
    allowed = _current_allowed_list(src)
    mutated = _with_modified_scope(src, new_allowed=allowed + ["src/interfaces/unreviewed_bridge.py"])
    assert validate_scope(mutated) is False


def test_unknown_other_runtime_rejected():
    src = _read_target_source()
    allowed = _current_allowed_list(src)
    mutated = _with_modified_scope(src, new_allowed=allowed + ["src/llm/unreviewed.py"])
    assert validate_scope(mutated) is False


def test_wildcard_rejected():
    src = _read_target_source()
    allowed = _current_allowed_list(src)
    mutated = _with_modified_scope(src, new_allowed=allowed + ["src/interfaces/*"])
    assert validate_scope(mutated) is False


@pytest.mark.parametrize("missing", list(NEW_FILES))
def test_missing_any_new_file_rejected(missing):
    src = _read_target_source()
    allowed = _current_allowed_list(src)
    trimmed = [s for s in allowed if s != missing]
    assert missing not in trimmed
    mutated = _with_modified_scope(src, new_allowed=trimmed)
    assert validate_scope(mutated) is False


def test_deleting_old_allowed_rejected():
    src = _read_target_source()
    allowed = _current_allowed_list(src)
    assert "src/paths.py" in allowed
    trimmed = [s for s in allowed if s != "src/paths.py"]
    mutated = _with_modified_scope(src, new_allowed=trimmed)
    assert validate_scope(mutated) is False


def test_extra_scope_file_rejected():
    src = _read_target_source()
    allowed = _current_allowed_list(src)
    mutated = _with_modified_scope(src, new_allowed=allowed + ["src/soul/extra_probe.py"])
    assert validate_scope(mutated) is False


# ── negative: second assignment / duplicate definition bypass ─────────────


def test_second_allowed_assignment_rejected_even_when_first_is_correct():
    # First literal stays correct; a later reassignment must still fail.
    src = _read_target_source()
    assert validate_scope(src) is True
    allowed = _current_allowed_list(src)
    mutated = _append_second_assignment(src, second_allowed=list(allowed))
    assert validate_scope(mutated) is False


def test_second_allowed_assignment_with_extra_rejected():
    src = _read_target_source()
    allowed = _current_allowed_list(src)
    mutated = _append_second_assignment(src, second_allowed=allowed + ["src/llm/unreviewed.py"])
    assert validate_scope(mutated) is False


def test_second_frozen_assignment_rejected_even_when_first_is_correct():
    src = _read_target_source()
    assert validate_scope(src) is True
    frozen = _current_frozen_list(src)
    mutated = _append_second_assignment(src, second_frozen=list(frozen))
    assert validate_scope(mutated) is False


def test_second_frozen_assignment_with_change_rejected():
    src = _read_target_source()
    frozen = _current_frozen_list(src)
    changed = list(frozen)
    changed[0] = "src/agency/stages_v2.py"
    mutated = _append_second_assignment(src, second_frozen=changed)
    assert validate_scope(mutated) is False


def test_duplicate_class_rejected():
    src = _read_target_source()
    assert validate_scope(src) is True
    mutated = _duplicate_class(src)
    assert validate_scope(mutated) is False


def test_duplicate_method_rejected():
    src = _read_target_source()
    assert validate_scope(src) is True
    mutated = _duplicate_method(src)
    assert validate_scope(mutated) is False


# ── negative: frozen ─────────────────────────────────────────────────────


def test_frozen_delete_rejected():
    src = _read_target_source()
    frozen = _current_frozen_list(src)
    mutated = _with_modified_scope(src, new_frozen=frozen[1:])
    assert validate_scope(mutated) is False


def test_frozen_change_rejected():
    src = _read_target_source()
    frozen = _current_frozen_list(src)
    changed = list(frozen)
    changed[0] = "src/agency/stages_v2.py"
    mutated = _with_modified_scope(src, new_frozen=changed)
    assert validate_scope(mutated) is False


def test_frozen_reorder_rejected():
    src = _read_target_source()
    frozen = _current_frozen_list(src)
    reordered = list(reversed(frozen))
    assert reordered != frozen
    mutated = _with_modified_scope(src, new_frozen=reordered)
    assert validate_scope(mutated) is False


def test_malformed_source_rejected():
    assert validate_scope("def broken(:\n") is False
    assert validate_scope("# empty") is False


# ── original guard would still reject unregistered new path (pure set logic) ──


def test_truly_missing_new_path_rejected_by_original_guard():
    # Pure set judgement mirroring the original assertion
    # `src_changes <= allowed_original`; never executes the original pytest function.
    baseline_original = set(_BASELINE_SORTED_ORIGINAL_ALLOWED)
    # Baseline digest constant pins this exact set (recomputed here from baseline constant,
    # never from the modified file under test).
    assert _digest_sorted(baseline_original) == EXPECTED_ORIGINAL_ALLOWED_DIGEST
    # A new EI-1 file is NOT in the pre-ticket allowed set => original guard rejects.
    assert ({"src/interfaces/schema.py"} <= baseline_original) is False
    assert ({"src/interfaces/schema.py"} - baseline_original) == {"src/interfaces/schema.py"}
    # And the current file does register it (positive control via pure set, no pytest run).
    current_allowed = set(_current_allowed_list(_read_target_source()))
    assert {"src/interfaces/schema.py"} <= current_allowed
