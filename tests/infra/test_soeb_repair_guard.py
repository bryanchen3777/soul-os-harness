# -*- coding: utf-8 -*-
"""tests/infra/test_soeb_repair_guard.py — 單筆修復工具的結構性守衛。

為什麼要有這道守衛
==================
`runner.py` 以 `"w"` 開 `observations.jsonl`，所以「只補跑一題」不可能寫回既有
run dir（整檔會被截斷）。實務上只能跑一個 `--only` 探針目錄，再把那一筆換回去。
沒有工具時，換回去＝**手動改 JSONL**，也就是「靜默改結果」——它會讓報表看起來
乾淨，而事後沒有人能判斷哪一筆被換過、為什麼換。

`scripts/soeb/repair.py` 把這個動作收斂成**只能做對的事**。本檔釘住四件事：

- **R1（正當修復可行）**：失敗 → 成功可以完成，其餘紀錄逐位元不變，
  替換事實寫進紀錄本身（`repaired_from`）與 append-only 稽核檔（`repairs.jsonl`），
  且不留下 `.bak` / `.repairtmp`。
- **R2（不可覆蓋成功結果）**：目標那一筆若本來就成功，必須拒絕（exit 1）。
  並以 monkeypatch 把 `_repair_blockers` 整個拿掉做**變異自證**——證明該斷言有牙齒。
- **R3（不可跨預算／跨規格替換，但不可過度嚴格）**：
  * 全域層（`suite` / `spec_sha256` / `system_prompt_sha256`）任一不同 ⇒ 拒絕。
  * 當題層（`forced_max_tokens` / `original[task]` / 該題 `tasks` 條目）任一不同 ⇒ 拒絕。
  * **但 `--only` 探針的 manifest 只會記錄被跑的那一題**，這是合法的部分集，
    必須**接受**。否則工具會被自己的過度嚴格廢掉，逼人回去手改 JSONL ——
    那比沒有工具更糟。這一條是本檔最重要的回歸（R3-b）。
- **R4**：沒有逃生旗標；找不到目標／重複紀錄／同目錄 一律拒絕。

全部使用 `tmp_path` 與合成紀錄，**不讀真產物、不發任何請求**。
"""
from __future__ import annotations

import inspect
import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.soeb import repair  # noqa: E402

# ─────────────────────────────────────────────────────────────────────────────
# 測試替身
# ─────────────────────────────────────────────────────────────────────────────

SPEC = "915a04eb35894e72228453b1f89a3f9a0ffebee9e2c1a7aa61cd1f26b2cf2695"
PROMPT = "b96c6b2944e8b096eb9cdfcd2269fafde9d27f66bb734d3429f1206709ad03b9"
MODEL = "mimo-v2.6-flash"

ORIGINAL_BUDGET = {
    "T1": 2048,
    "T2": 3072,
    "T3": 3072,
    "T4": 3072,
    "T5": 4096,
    "T6": 6144,
    "T7": 2048,
}


def _task(task_id: str, max_tokens: int = 32768) -> dict:
    return {"id": task_id, "max_tokens": max_tokens, "temperature": 0.0, "effort": None}


TASKS_FULL = [_task(t) for t in ORIGINAL_BUDGET]


def _manifest(**overrides: object) -> dict:
    """正式 run dir 的 manifest 形狀：七題全記。"""
    manifest = {
        "runner": "soeb",
        "suite": "soeb-v1",
        "spec_sha256": SPEC,
        "system_prompt_sha256": PROMPT,
        "budget_override": {
            "forced_max_tokens": 32768,
            "original": dict(ORIGINAL_BUDGET),
        },
        "tasks": [dict(t) for t in TASKS_FULL],
        "models": [MODEL],
        "total_calls": 2,
        "ok_calls": 1,
        "failed_calls": 1,
    }
    manifest.update(overrides)
    return manifest


def _probe_manifest(**overrides: object) -> dict:
    """`--only T6` 探針的 manifest 形狀：只記 T6（合法的部分集）。"""
    manifest = _manifest(
        budget_override={"forced_max_tokens": 32768, "original": {"T6": 6144}},
        tasks=[_task("T6")],
    )
    manifest.update(overrides)
    return manifest


def _ok_record(task: str, run: int, text: str = "好的正文") -> dict:
    return {
        "model_id": MODEL,
        "task": task,
        "run": run,
        "ok": True,
        "status_code": 200,
        "finish_reason": "stop",
        "latency_ms": 1000,
        "text": text,
    }


def _failed_record(task: str, run: int, error: str = "Unknown Error") -> dict:
    return {
        "model_id": MODEL,
        "task": task,
        "run": run,
        "ok": False,
        "status_code": 500,
        "finish_reason": "",
        "latency_ms": 579095,
        "text": "",
        "error": error,
    }


def _write_run(
    root: Path,
    name: str,
    records: list[dict],
    *,
    manifest: dict | None = None,
) -> Path:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "manifest.json").write_text(
        json.dumps(manifest or _manifest(), ensure_ascii=False, indent=2),
        encoding="utf-8",
        newline="\n",
    )
    body = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records)
    (d / "observations.jsonl").write_text(body, encoding="utf-8", newline="\n")
    return d


def _read_records(run_dir: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in (run_dir / "observations.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _leftovers(root: Path) -> list[Path]:
    return sorted(
        p
        for p in root.rglob("*")
        if p.is_file() and (p.name.endswith(".bak") or p.name.endswith(".repairtmp"))
    )


def _invoke(target: Path, source: Path, task: str = "T6", run_index: int = 1) -> int:
    return repair.main(
        [
            "--run-dir",
            str(target),
            "--replacement",
            str(source),
            "--model",
            MODEL,
            "--task",
            task,
            "--run-index",
            str(run_index),
        ]
    )


# ─────────────────────────────────────────────────────────────────────────────
# R1 — 正當修復可行，且留下可稽核痕跡
# ─────────────────────────────────────────────────────────────────────────────


def test_r1_repairs_failed_record_and_preserves_others(tmp_path: Path) -> None:
    """R1：失敗 → 成功；其他紀錄逐位元不變；稽核檔與 `repaired_from` 都在。"""
    other = _ok_record("T5", 1, text="不該被動到的正文")
    target = _write_run(tmp_path, "target", [_failed_record("T6", 1), other])
    source = _write_run(
        tmp_path, "probe", [_ok_record("T6", 1, text="重跑後的正文")], manifest=_probe_manifest()
    )

    before_other = json.dumps(other, ensure_ascii=False)
    assert _invoke(target, source) == 0

    recs = _read_records(target)
    assert len(recs) == 2
    t6 = next(r for r in recs if r["task"] == "T6")
    assert t6["ok"] is True
    assert t6["text"] == "重跑後的正文"
    assert t6["repaired_from"]["replaced_error"] == "Unknown Error"
    assert t6["repaired_from"]["replaced_status_code"] == 500

    untouched = next(r for r in recs if r["task"] == "T5")
    assert json.dumps(untouched, ensure_ascii=False) == before_other

    log = target / repair.REPAIR_LOG
    assert log.is_file(), "缺少 append-only 稽核檔，事後無法判斷哪一筆被換過"
    entry = json.loads(log.read_text(encoding="utf-8").splitlines()[0])
    assert entry["task"] == "T6"
    assert entry["replaced"]["ok"] is False
    assert entry["replacement"]["ok"] is True

    assert not _leftovers(tmp_path), "修復後留下了備份或暫存檔"


def test_r1_repeated_repair_is_refused(tmp_path: Path) -> None:
    """R1：同一筆修第二次必須被拒絕（第一次已成功 ⇒ 不可再覆蓋）。"""
    target = _write_run(tmp_path, "target", [_failed_record("T6", 1)])
    source = _write_run(tmp_path, "probe", [_ok_record("T6", 1)], manifest=_probe_manifest())

    assert _invoke(target, source) == 0
    assert _invoke(target, source) == 1


# ─────────────────────────────────────────────────────────────────────────────
# R2 — 不可覆蓋成功結果（＋變異自證）
# ─────────────────────────────────────────────────────────────────────────────


def test_r2_refuses_to_overwrite_success(tmp_path: Path) -> None:
    """R2：目標本來就成功 ⇒ 拒絕，且檔案內容不得改變。"""
    target = _write_run(tmp_path, "target", [_ok_record("T6", 1, text="原本的好答案")])
    source = _write_run(
        tmp_path, "probe", [_ok_record("T6", 1, text="更好看的答案")], manifest=_probe_manifest()
    )

    raw_before = (target / "observations.jsonl").read_text(encoding="utf-8")
    assert _invoke(target, source) == 1
    assert (target / "observations.jsonl").read_text(encoding="utf-8") == raw_before
    assert not (target / repair.REPAIR_LOG).exists(), "被拒絕時不得寫稽核檔"


def test_r2_mutation_removing_guard_turns_the_assertion_red(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R2 變異自證：拿掉 `_repair_blockers` 後，成功結果真的會被覆蓋。

    這證明 R2 的斷言不是裝飾品：沒有那道閘門，`main()` 會照樣把成功結果換掉。
    """
    target = _write_run(tmp_path, "target", [_ok_record("T6", 1, text="原本的好答案")])
    source = _write_run(
        tmp_path, "probe", [_ok_record("T6", 1, text="更好看的答案")], manifest=_probe_manifest()
    )

    monkeypatch.setattr(repair, "_repair_blockers", lambda old, new: None)
    assert _invoke(target, source) == 0, "變異版本竟然還是拒絕 ⇒ 這個變異沒有真的解除閘門"

    recs = _read_records(target)
    assert recs[0]["text"] == "更好看的答案", "變異版本沒有覆蓋成功結果 ⇒ 斷言無牙齒"


def test_r2_refuses_empty_replacement_text(tmp_path: Path) -> None:
    """R2：替換來源 ok=true 但正文空白 ⇒ 拒絕（換進去也不能評分）。"""
    target = _write_run(tmp_path, "target", [_failed_record("T6", 1)])
    source = _write_run(
        tmp_path, "probe", [_ok_record("T6", 1, text="   ")], manifest=_probe_manifest()
    )

    assert _invoke(target, source) == 1


def test_r2_refuses_failed_replacement(tmp_path: Path) -> None:
    """R2：替換來源本身仍失敗 ⇒ 拒絕（不是「換個失敗的樣子」）。"""
    target = _write_run(tmp_path, "target", [_failed_record("T6", 1)])
    source = _write_run(
        tmp_path,
        "probe",
        [_failed_record("T6", 1, error="timeout")],
        manifest=_probe_manifest(),
    )

    assert _invoke(target, source) == 1


# ─────────────────────────────────────────────────────────────────────────────
# R3 — 不可跨預算／跨規格替換（＋變異自證）／但不可過度嚴格
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("label", "overrides"),
    [
        (
            "forced_max_tokens 不同（用 65536 的答案補 32768 的洞）",
            {"budget_override": {"forced_max_tokens": 65536, "original": dict(ORIGINAL_BUDGET)}},
        ),
        (
            "原始預算 original[T6] 不同",
            {"budget_override": {"forced_max_tokens": 32768, "original": {**ORIGINAL_BUDGET, "T6": 8192}}},
        ),
        ("spec_sha256 不同", {"spec_sha256": "0" * 64}),
        ("system_prompt_sha256 不同", {"system_prompt_sha256": "1" * 64}),
        ("suite 不同", {"suite": "soeb-v0"}),
        (
            "該題題目設定不同（max_tokens）",
            {"tasks": [_task(t, 65536 if t == "T6" else 32768) for t in ORIGINAL_BUDGET]},
        ),
        ("該題 missing", {"tasks": [_task(t) for t in ORIGINAL_BUDGET if t != "T6"]}),
    ],
)
def test_r3_refuses_cross_spec_replacement(
    tmp_path: Path, label: str, overrides: dict
) -> None:
    """R3：全域層或當題層任一不同 ⇒ 拒絕，且目標檔不得被改。"""
    target = _write_run(tmp_path, "target", [_failed_record("T6", 1)])
    source = _write_run(
        tmp_path,
        "probe",
        [_ok_record("T6", 1, text="不該被接受的答案")],
        manifest=_manifest(**overrides),
    )

    raw_before = (target / "observations.jsonl").read_text(encoding="utf-8")
    assert _invoke(target, source) == 1, f"{label} 卻允許替換 ⇒ 結果不可比"
    assert (target / "observations.jsonl").read_text(encoding="utf-8") == raw_before
    assert not (target / repair.REPAIR_LOG).exists()


def test_r3b_accepts_partial_manifest_from_only_probe(tmp_path: Path) -> None:
    """R3-b：`--only T6` 探針只記 T6 ⇒ 必須**接受**（合法的部分集）。

    這是本檔最重要的回歸：把 `budget_override` / `tasks` 整包比對，會讓
    `original` 只有 `{"T6": ...}` 的探針**恆不相等**，工具等於廢掉，
    逼人回去手改 JSONL。當題層比對必須 task-scoped。
    """
    target = _write_run(tmp_path, "target", [_failed_record("T6", 1)])
    source = _write_run(
        tmp_path, "probe", [_ok_record("T6", 1, text="探針答案")], manifest=_probe_manifest()
    )

    probe = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    assert set(probe["budget_override"]["original"]) == {"T6"}, "測試前提：探針只記 T6"
    assert len(probe["tasks"]) == 1, "測試前提：探針只記 T6"

    assert _invoke(target, source) == 0, "合法的 --only 探針被拒絕 ⇒ 工具會被繞過"
    assert _read_records(target)[0]["text"] == "探針答案"


def test_r3_mutation_removing_invariant_check_turns_the_assertion_red(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R3 變異自證：拿掉不變量比對後，跨預算替換確實會成功。

    這正是「用 65536 的答案補 32768 的洞」的形狀 —— 報表看起來正常，
    但該模型的 T6 從此與其他模型不可比。守衛必須能抓到它。
    """
    target = _write_run(tmp_path, "target", [_failed_record("T6", 1)])
    source = _write_run(
        tmp_path,
        "probe",
        [_ok_record("T6", 1, text="65536 才生得出來的答案")],
        manifest=_manifest(
            budget_override={
                "forced_max_tokens": 65536,
                "original": dict(ORIGINAL_BUDGET),
            }
        ),
    )

    monkeypatch.setattr(repair, "_check_invariants", lambda base, other, task_id: None)
    assert _invoke(target, source) == 0, "變異版本還是拒絕 ⇒ 這個變異沒有真的解除閘門"

    recs = _read_records(target)
    assert recs[0]["ok"] is True, "變異版本沒有跨預算替換成功 ⇒ 斷言無牙齒"


# ─────────────────────────────────────────────────────────────────────────────
# R4 — 沒有逃生旗標、找不到目標時的行為
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "flag",
    [
        "--force",
        "--overwrite",
        "--allow-success",
        "--allow-cross-budget",
        "--allow-spec-mismatch",
    ],
)
def test_r4_main_has_no_escape_hatch(flag: str) -> None:
    """R4：`main()` 不得提供任何解除閘門的旗標（與 scrub.py 同一原則）。"""
    assert flag not in inspect.getsource(repair.main)


def test_r4_refuses_unknown_model(tmp_path: Path) -> None:
    """R4：模型不在目標 manifest 的 models ⇒ 參數錯誤（exit 2）。"""
    target = _write_run(tmp_path, "target", [_failed_record("T6", 1)])
    source = _write_run(tmp_path, "probe", [_ok_record("T6", 1)], manifest=_probe_manifest())

    rc = repair.main(
        [
            "--run-dir",
            str(target),
            "--replacement",
            str(source),
            "--model",
            "not-in-manifest",
            "--task",
            "T6",
        ]
    )
    assert rc == 2


def test_r4_refuses_when_record_missing(tmp_path: Path) -> None:
    """R4：目標找不到該筆 ⇒ exit 2（不猜、不新建）。

    這裡刻意讓兩份 manifest 都是**完整七題**（`_manifest()`），把不變量閘門
    排除在外，單獨驗「找不到紀錄」這條路徑。
    """
    target = _write_run(tmp_path, "target", [_failed_record("T6", 1)])
    source = _write_run(tmp_path, "probe", [_ok_record("T6", 1)], manifest=_manifest())

    assert _invoke(target, source, task="T7") == 2


def test_r4_refuses_duplicate_records(tmp_path: Path) -> None:
    """R4：目標同一 (task, run) 出現兩筆 ⇒ exit 2（來源本身有問題，不挑一個改）。"""
    target = _write_run(
        tmp_path, "target", [_failed_record("T6", 1), _failed_record("T6", 1)]
    )
    source = _write_run(tmp_path, "probe", [_ok_record("T6", 1)], manifest=_probe_manifest())

    assert _invoke(target, source) == 2


def test_r4_refuses_same_dir(tmp_path: Path) -> None:
    """R4：`--replacement` 等於 `--run-dir` ⇒ exit 2。"""
    target = _write_run(tmp_path, "target", [_failed_record("T6", 1)])
    assert _invoke(target, target) == 2
