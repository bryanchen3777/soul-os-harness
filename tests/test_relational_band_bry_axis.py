"""
tests/test_relational_band_bry_axis.py — RELATIONAL-BAND-FIX-1 验收测试

覆盖（工单 §測試）:
  (i)   Bry 窗口内 inbound ⇒ co+1（读侧折抵, 走真实 collect_window_signals /
        settle_relations 全链）
  (ii)  窗口外 ⇒ 0（含 24h 独占边界: 恰好 window_start 视为**窗内**）
  (iii) **单窗上限 1（幂等）**: 同窗口重复 settle（force=True）不得累加超过 1
  (iv)  `stranger→known` 变成可達（纯函数 evaluate_band 验 + settle 全链验）
  (v)   **peer 轴（agent↔agent）逐位元不变**（对照组 vs 实验组深度相等）
  (vi)  `relational_bands.py` 门槛未被改动（字面断言: 常量 + 源码文本）
  (vii) `scripts/relband_reeval.py`: dry-run **0 写入**（全树 sha256 不变）;
        `--apply` 走**正规入口** apply_relation_evaluation（spy 断言 + 落盘带位
        变更证据 + `[BAND_MIGRATION] other=user_bryan direction=promote`）

运行（本文件, 全新 basetemp）:
  .\\.venv\\Scripts\\python.exe -m pytest tests/test_relational_band_bry_axis.py -q --basetemp=<全新目录>

红線: 全部测试在 tmp data root 上; 0 生产 data/** 写入; 0 服务重启。
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from src.goals.motive_provider import reset_goal_providers
from src.goals.seed_provider import reset_seed_providers
from src.paths import reset_data_root
import src.social.relational_bands as bands_mod
from src.social.relation_settlement import collect_window_signals, settle_relations

AGENT = "agent_bryaxis"
PEER = "agent_akane"
BRYAN = "user_bryan"
NOW = datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc)  # 窗口 = [09-13T12:00Z, 09-14T12:00Z]
REPO_ROOT = Path(__file__).resolve().parents[1]
SETTLE_LOGGER = "soul_os.social.relation_settlement"

# scripts/relband_reeval.py 以檔案路徑載入（scripts 不是 package）; 只載入一次,
# 其 module-level `sys.path.insert(repo_root)` 因此只執行一次。
_spec = importlib.util.spec_from_file_location(
    "relband_reeval_under_test", REPO_ROOT / "scripts" / "relband_reeval.py"
)
reeval = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(reeval)  # type: ignore[union-attr]


# ───────────────────────────────────────────────────────────
# fixtures / helpers（对齐 tests/social/test_sg3_band_recalibration.py 隔离纪律）
# ───────────────────────────────────────────────────────────

@pytest.fixture()
def iso_env(tmp_path, monkeypatch):
    monkeypatch.setenv("SOUL_OS_DATA_DIR", str(tmp_path))
    reset_data_root()
    reset_goal_providers()
    reset_seed_providers()
    import src.soul.relationships as rel_mod
    monkeypatch.setattr(rel_mod, "_manager_singleton", None)
    yield tmp_path
    reset_seed_providers()
    reset_goal_providers()
    reset_data_root()


def _entry(
    band: str = "stranger",
    reply: int = 0,
    co: int = 0,
    dream: int = 0,
    *,
    last_interaction_at: Optional[str] = None,
    last_signal_at: Optional[str] = None,
    interaction_count: int = 0,
) -> Dict[str, Any]:
    """4.2 schema entry（confidence 固定 0.0: 让 store 载入时的既存 decay 成为
    数值 no-op, 对照实验才可比; decay 本身不是本票范围）。"""
    return {
        "impression": "静かな人", "feeling": "neutral", "confidence": 0.0,
        "interaction_count": interaction_count,
        "last_interaction_at": last_interaction_at,
        "last_updated": "2026-09-01T00:00:00+00:00",
        "created_at": "2026-08-01T00:00:00+00:00",
        "objective": {
            "reply_exchanges": reply, "co_presence_sessions": co,
            "dream_exchanges": dream, "last_signal_at": last_signal_at,
        },
        "impression_tags": [], "relational_band": band,
        "band_updated_at": None, "last_relation_update_ref": None,
    }


def _write_relationships(tmp: Path, others: Dict[str, Dict[str, Any]]) -> None:
    path = tmp / "soul" / AGENT / "relationships.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "agent_id": AGENT, "schema_version": "4.2",
        "created_at": "2026-09-01T00:00:00+00:00",
        "last_decay_at": "2026-09-01T00:00:00+00:00",
        "others": others,
    }, ensure_ascii=False), encoding="utf-8")


def _read_others(tmp: Path, agent_id: str = AGENT) -> Dict[str, Any]:
    path = tmp / "soul" / agent_id / "relationships.json"
    return json.loads(path.read_text(encoding="utf-8"))["others"]


def _read_entry(tmp: Path, other: str = BRYAN, agent_id: str = AGENT) -> Dict[str, Any]:
    return _read_others(tmp, agent_id).get(other) or {}


def _write_interactions(tmp: Path, recs: List[Dict[str, Any]]) -> None:
    path = tmp / "soul" / "interactions.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for rec in recs:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _co_rec(ts: str, agents: List[str]) -> Dict[str, Any]:
    return {"ts": ts, "type": "cross_chat", "agents": agents, "content": "聊了音乐"}


def _write_reply(tmp: Path, ts: str, actor: str, n: int = 1) -> None:
    path = tmp / "world" / "perception_trace.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for i in range(n):
            fh.write(json.dumps({
                "event_id": f"ev-{actor}-{i}", "timestamp": ts,
                "event_type": "reply",
                "extra": {"event_kind": "social", "actor_id": actor},
            }, ensure_ascii=False) + "\n")


def _tree_snapshot(root: Path) -> Dict[str, str]:
    """全树内容快照（相对路径 → sha256）, 用于 dry-run「0 写入」硬断言。"""
    out: Dict[str, str] = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            rel = str(p.relative_to(root)).replace("\\", "/")
            out[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def _migrations(caplog) -> List[Dict[str, Any]]:
    out = []
    for rec in caplog.records:
        msg = rec.getMessage()
        marker = "[RelSettle][BAND_MIGRATION] "
        if marker in msg:
            out.append(json.loads(msg.split(marker, 1)[1]))
    return out


def _reset_rel_singleton(monkeypatch) -> None:
    """直接改寫 relationships.json 後必須重置 manager 單例 —— `_cache` 只在
    建構時載入一次（同 harness/tl9.py 的隔離紀律）。"""
    import src.soul.relationships as rel_mod
    monkeypatch.setattr(rel_mod, "_manager_singleton", None)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


# ───────────────────────────────────────────────────────────
# (i) / (ii) 读侧折抵口径
# ───────────────────────────────────────────────────────────

class TestBryAxisCredit:
    def test_inbound_inside_window_credits_one_co_presence(self, iso_env):
        """(i) Bry 的既有互动紀錄落在同一 24h 窗口内 ⇒ 折抵 1 次 co(+1)。"""
        tmp = iso_env
        _write_relationships(tmp, {BRYAN: _entry(
            interaction_count=243,
            last_interaction_at=_iso(NOW - timedelta(hours=1)),
        )})
        signals = collect_window_signals(AGENT, NOW, base_dir=tmp)
        assert signals[BRYAN]["co_presence"] == 1
        # 0 新事件类型: interactions.jsonl / perception_trace.jsonl 都不存在
        assert not (tmp / "soul" / "interactions.jsonl").exists()

    def test_outside_window_credits_zero(self, iso_env):
        """(ii) 窗口外（25h 前）⇒ 0（且不出现在信号结果内）。"""
        tmp = iso_env
        _write_relationships(tmp, {BRYAN: _entry(
            interaction_count=243,
            last_interaction_at=_iso(NOW - timedelta(hours=25)),
        )})
        signals = collect_window_signals(AGENT, NOW, base_dir=tmp)
        assert BRYAN not in signals

    def test_boundary_at_window_start_is_inside(self, iso_env):
        """窗口边界与既有 co_presence 口径一致: 恰好 == window_start 视为窗内。"""
        tmp = iso_env
        _write_relationships(tmp, {BRYAN: _entry(
            last_interaction_at=_iso(NOW - timedelta(hours=24)),
        )})
        assert collect_window_signals(AGENT, NOW, base_dir=tmp)[BRYAN]["co_presence"] == 1

    def test_future_timestamp_excluded(self, iso_env):
        """未来时间戳（坏数据）不计入（与既有信号读取口径一致 fail-closed）。"""
        tmp = iso_env
        _write_relationships(tmp, {BRYAN: _entry(
            last_interaction_at=_iso(NOW + timedelta(hours=1)),
        )})
        assert BRYAN not in collect_window_signals(AGENT, NOW, base_dir=tmp)

    def test_missing_bry_entry_and_missing_file_are_zero(self, iso_env):
        """无 Bry entry / 无 relationships.json → 0（不 ensure、不新建 entry）。"""
        tmp = iso_env
        assert BRYAN not in collect_window_signals(AGENT, NOW, base_dir=tmp)
        _write_relationships(tmp, {PEER: _entry()})
        assert BRYAN not in collect_window_signals(AGENT, NOW, base_dir=tmp)

    def test_bad_json_fail_closed(self, iso_env):
        """坏 relationships.json → 0, 不 crash（fail-closed）。"""
        tmp = iso_env
        path = tmp / "soul" / AGENT / "relationships.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json", encoding="utf-8")
        assert BRYAN not in collect_window_signals(AGENT, NOW, base_dir=tmp)

    def test_full_chain_promotes_and_folds_reply(self, iso_env):
        """全链: Bry 窗内 inbound → co delta 1 + SG-3 折抵带起 reply delta 1
        → stranger→known、band_updated_at 首次写入（0 新写入路径）。"""
        tmp = iso_env
        _write_relationships(tmp, {BRYAN: _entry(
            interaction_count=243,
            last_interaction_at=_iso(NOW - timedelta(hours=2)),
        )})
        res = settle_relations(AGENT, now=NOW, base_dir=tmp)
        assert res["skipped"] is None
        assert res["updated"] == 1
        entry = _read_entry(tmp)
        assert entry["objective"]["co_presence_sessions"] == 1
        assert entry["objective"]["reply_exchanges"] == 1  # SG-3 折抵自动带起
        assert entry["relational_band"] == "known"
        assert entry["band_updated_at"] == _iso(NOW)
        assert entry["objective"]["last_signal_at"] == _iso(NOW)


# ───────────────────────────────────────────────────────────
# (iii) 单窗上限 1 / 幂等
# ───────────────────────────────────────────────────────────

class TestSingleWindowCapIdempotent:
    def test_repeated_forced_settle_in_same_window_does_not_accumulate(self, iso_env):
        """(iii) 同一窗口内重复 settle（force=True 绕过节流）→ 折抵不得再累加。"""
        tmp = iso_env
        _write_relationships(tmp, {BRYAN: _entry(
            interaction_count=243,
            last_interaction_at=_iso(NOW - timedelta(hours=1)),
        )})
        settle_relations(AGENT, now=NOW, base_dir=tmp, force=True)
        e1 = _read_entry(tmp)
        assert (e1["objective"]["co_presence_sessions"],
                e1["objective"]["reply_exchanges"]) == (1, 1)

        # 同窗第 2 / 第 3 次 settle（+1min / +2min, 仍落在 [NOW-24h, NOW+2min] 内）
        for minutes in (1, 2):
            res = settle_relations(
                AGENT, now=NOW + timedelta(minutes=minutes), base_dir=tmp, force=True,
            )
            assert res["updated"] == 0  # 0 增量
            e = _read_entry(tmp)
            assert (e["objective"]["co_presence_sessions"],
                    e["objective"]["reply_exchanges"]) == (1, 1)
            assert e["relational_band"] == "known"

    def test_collect_is_zero_after_credit_in_same_window(self, iso_env):
        """读侧幂等载体 = objective.last_signal_at: 折抵落盘后同窗再读 → 0。"""
        tmp = iso_env
        _write_relationships(tmp, {BRYAN: _entry(
            last_interaction_at=_iso(NOW - timedelta(hours=1)),
        )})
        assert collect_window_signals(AGENT, NOW, base_dir=tmp)[BRYAN]["co_presence"] == 1
        settle_relations(AGENT, now=NOW, base_dir=tmp)
        assert BRYAN not in collect_window_signals(
            AGENT, NOW + timedelta(minutes=5), base_dir=tmp
        )

    def test_next_window_credits_again(self, iso_env, monkeypatch):
        """跨窗（>24h 节流窗）且 Bry 又有窗内互动 → 正常再折抵 1（累计制）。"""
        tmp = iso_env
        _write_relationships(tmp, {BRYAN: _entry(
            last_interaction_at=_iso(NOW - timedelta(hours=1)),
        )})
        settle_relations(AGENT, now=NOW, base_dir=tmp)
        e1 = _read_entry(tmp)
        assert (e1["objective"]["co_presence_sessions"],
                e1["objective"]["reply_exchanges"]) == (1, 1)
        # D+1: Bry 又互动（落在 [NOW, NOW+24h] 窗内）; 0 新事件类型（只改既有字段）
        _write_relationships(tmp, {BRYAN: {
            **e1,
            "last_interaction_at": _iso(NOW + timedelta(hours=20)),
        }})
        _reset_rel_singleton(monkeypatch)
        settle_relations(AGENT, now=NOW + timedelta(hours=24), base_dir=tmp)
        e = _read_entry(tmp)
        assert (e["objective"]["co_presence_sessions"],
                e["objective"]["reply_exchanges"]) == (2, 2)
        assert e["relational_band"] == "familiar"  # known→familiar 需 co≥2 且 reply≥2


# ───────────────────────────────────────────────────────────
# (iv) 可達性
# ───────────────────────────────────────────────────────────

class TestReachability:
    def test_pure_function_threshold_pins(self):
        """(iv) 纯函数: 1 次 co 即達 known（门檻值本身未被本票改动）。"""
        assert bands_mod.evaluate_band("stranger", co_presence_sessions=1) == "known"
        assert bands_mod.evaluate_band("stranger", reply_exchanges=1) == "known"
        assert bands_mod.evaluate_band("stranger") == "stranger"  # 0 信号仍不可達

    def test_bry_axis_makes_stranger_to_known_reachable(self, iso_env, monkeypatch):
        """修法前后对照的本票主张: Bry 軸三计数器由「结构性恒 0」变为可累加
        → stranger→known 经**正常路径**可達（此处用生产形状数据: 15 檔 schema
        半迁移、Bry interaction_count 数百、objective 全 0）。"""
        tmp = iso_env
        # 修法前等價: Bry 的互動紀錄落在窗口外（48h 前）→ 折抵 0 →
        # 三计数器恒 0, 底带 stranger 不可達（SG-2.1: 0 信号窗不回升）
        _write_relationships(tmp, {BRYAN: _entry(
            band="stranger", reply=0, co=0, dream=0,
            interaction_count=243,
            last_interaction_at=_iso(NOW - timedelta(hours=48)),
        )})
        settle_relations(AGENT, now=NOW, base_dir=tmp)
        assert _read_entry(tmp)["relational_band"] == "stranger"
        assert _read_entry(tmp)["objective"]["co_presence_sessions"] == 0

        # 修法后: 窗内 inbound → 正常路径晋升
        _write_relationships(tmp, {BRYAN: {
            **_read_entry(tmp),
            "last_interaction_at": _iso(NOW - timedelta(hours=3)),
        }})
        _reset_rel_singleton(monkeypatch)
        res = settle_relations(
            AGENT, now=NOW + timedelta(hours=2), base_dir=tmp, force=True,
        )
        assert res["skipped"] is None
        entry = _read_entry(tmp)
        assert entry["objective"]["co_presence_sessions"] == 1
        assert entry["relational_band"] == "known"
        assert entry["band_updated_at"] is not None


# ───────────────────────────────────────────────────────────
# (v) peer 軸逐位元不变
# ───────────────────────────────────────────────────────────

class TestPeerAxisBitForBitUnchanged:
    def _build(self, tmp: Path, *, with_bry: bool) -> None:
        others: Dict[str, Any] = {PEER: _entry("known", reply=1, co=1)}
        if with_bry:
            others[BRYAN] = _entry(
                interaction_count=243,
                last_interaction_at=_iso(NOW - timedelta(hours=1)),
            )
        _write_relationships(tmp, others)
        _write_interactions(tmp, [_co_rec(_iso(NOW - timedelta(hours=2)), [AGENT, PEER])])
        _write_reply(tmp, _iso(NOW - timedelta(hours=3)), PEER, 2)
        _write_reply(tmp, _iso(NOW - timedelta(hours=3)), AGENT, 2)

    def test_peer_counters_and_band_identical_with_and_without_bry(
        self, iso_env, monkeypatch
    ):
        """对同一份 peer 信号, 「有 Bry entry」与「无 Bry entry」两次 settle 的
        peer entry **深度相等** → Bry 折抵对 peer 軸 0 位移。"""
        base = iso_env
        control = base / "control"
        treated = base / "treated"
        self._build(control, with_bry=False)
        self._build(treated, with_bry=True)

        settle_relations(AGENT, now=NOW, base_dir=control, force=True)
        _reset_rel_singleton(monkeypatch)  # manager 單例按 data_dir 首呼鎖定
        settle_relations(AGENT, now=NOW, base_dir=treated, force=True)

        peer_control = _read_entry(control, PEER)
        peer_treated = _read_entry(treated, PEER)
        # 唯一的差異欄位 = `last_updated`，且它是**既有** `_decay_locked` 的
        # 記帳副作用（每次 store 存取都把所有 entry 的 last_updated 重寫成 wall
        # clock）—— 與 Bry 折抵無關: 任何**額外的 other** 排在 PEER 之後都會造成
        # 同樣的 churn（此處是既有的 Bry entry，非本票新增）。訊號/計數/帶位/
        # 審計欄位全部逐位元相同。
        assert peer_treated.pop("last_updated")  # 存在即可
        peer_control.pop("last_updated")
        assert peer_treated == peer_control
        assert peer_treated["objective"] == peer_control["objective"]
        assert peer_treated["relational_band"] == peer_control["relational_band"]
        assert peer_treated["band_updated_at"] == peer_control["band_updated_at"]
        # 只允许 Bry 軸本人不同
        assert BRYAN not in _read_others(control)
        assert _read_entry(treated, BRYAN)["objective"]["co_presence_sessions"] == 1

    def test_last_updated_churn_is_ordering_not_bry(self, iso_env):
        """證明上面排除 last_updated 的正當性: **完全無 Bry entry** 的環境下,
        只要 PEER 之後還排著另一個 other, PEER.last_updated 同樣被既有
        `_decay_locked` 掃描重寫 → 該欄位的位移純屬既有排序副作用, 與 Bry 軸無關。"""
        root = iso_env / "ordering"
        _write_relationships(root, {
            PEER: _entry("known", reply=1, co=1),
            "agent_other": _entry("known", reply=1, co=1),
        })
        settle_relations(AGENT, now=NOW, base_dir=root)
        assert BRYAN not in _read_others(root)
        assert _read_entry(root, PEER)["last_updated"] != _iso(NOW)
        assert _read_entry(root, "agent_other")["last_updated"] == _iso(NOW)

    def test_collect_window_signals_peer_slice_identical(self, iso_env):
        """读侧: 两次 collect 的 **Bry 以外的 key/值** 完全一致。"""
        base = iso_env
        control = base / "control2"
        treated = base / "treated2"
        self._build(control, with_bry=False)
        self._build(treated, with_bry=True)
        sig_c = collect_window_signals(AGENT, NOW, base_dir=control)
        sig_t = collect_window_signals(AGENT, NOW, base_dir=treated)
        assert {k: v for k, v in sig_t.items() if k != BRYAN} == sig_c
        assert BRYAN not in sig_c
        assert sig_t[BRYAN] == {"co_presence": 1}

    def test_no_bry_axis_write_when_agent_only_has_peers(self, iso_env):
        """纯 peer 环境（agent↔agent 的 10 笔搁浅计数器形状）→ 本票 0 位移。"""
        tmp = iso_env
        _write_relationships(tmp, {PEER: _entry("stranger", reply=0, co=1)})
        before = _read_entry(tmp, PEER)
        settle_relations(AGENT, now=NOW, base_dir=tmp)
        after = _read_entry(tmp, PEER)
        # SG-2.1: 无新信号 → 底带不回升, 计数不变（既有行为逐位元保留）
        assert after["objective"] == before["objective"]
        assert after["relational_band"] == "stranger"


# ───────────────────────────────────────────────────────────
# (vi) relational_bands.py 门槛未被改动（字面断言）
# ───────────────────────────────────────────────────────────

class TestFrozenThresholdsLiteral:
    def test_threshold_constants_literal(self):
        assert bands_mod.RELATIONAL_BANDS == ("stranger", "known", "familiar", "close")
        assert bands_mod._KNOWN_THRESHOLDS == {
            "reply_exchanges": 1, "co_presence_sessions": 1,
        }
        assert bands_mod._KNOWN_MODE == "or"
        assert bands_mod._FAMILIAR_THRESHOLDS == {
            "reply_exchanges": 2, "co_presence_sessions": 2,
        }
        assert bands_mod._FAMILIAR_MODE == "and"
        assert bands_mod._CLOSE_THRESHOLDS == (
            ({"reply_exchanges": 4, "co_presence_sessions": 4}, "and"),
        )
        assert bands_mod.DEMOTE_DAYS == 90
        assert bands_mod.DEMOTE_DAYS * 86400 == 7776000

    def test_threshold_source_text_literals(self):
        """源码文本级字面断言（防止本票顺手改门檻表）。"""
        src = (REPO_ROOT / "src" / "social" / "relational_bands.py").read_text(
            encoding="utf-8"
        ).replace("\r\n", "\n")
        for literal in (
            '_KNOWN_THRESHOLDS = {\n    "reply_exchanges": 1,\n'
            '    "co_presence_sessions": 1,\n}',
            '_KNOWN_MODE = "or"',
            '_FAMILIAR_THRESHOLDS = {\n    "reply_exchanges": 2,\n'
            '    "co_presence_sessions": 2,\n}',
            '_FAMILIAR_MODE = "and"',
            '_CLOSE_THRESHOLDS = (\n'
            '    ({"reply_exchanges": 4, "co_presence_sessions": 4}, "and"),\n)',
            'DEMOTE_DAYS = 90',
        ):
            assert literal in src, literal

    def test_settlement_module_is_the_only_write_axis(self):
        """PD-1: 本票不在 relation_settlement 引入 LLM / 新事件 / 定时器。"""
        src = (REPO_ROOT / "src" / "social" / "relation_settlement.py").read_text(
            encoding="utf-8"
        )
        for forbidden in ("import asyncio", "threading.Timer", "openai",
                          "ollama", "anthropic"):
            assert forbidden not in src, forbidden


# ───────────────────────────────────────────────────────────
# (vii) scripts/relband_reeval.py
# ───────────────────────────────────────────────────────────

class TestRelbandReevalTool:
    def _seed(self, tmp: Path, *, peer: bool = False,
              last_interaction_at: Optional[str] = None) -> None:
        others: Dict[str, Any] = {}
        if peer:
            others[PEER] = _entry("known", reply=1, co=1)
        others[BRYAN] = _entry(
            interaction_count=243,
            last_interaction_at=(
                last_interaction_at
                if last_interaction_at is not None
                else _iso(NOW - timedelta(hours=2))
            ),
        )
        _write_relationships(tmp, others)

    def test_dry_run_is_zero_write(self, iso_env, capsys):
        """dry-run: 全树内容 sha256 不变（0 写入, 连 store 都不实例化）。"""
        tmp = iso_env
        self._seed(tmp)
        before = _tree_snapshot(tmp)
        rc = reeval.main(["--data-root", str(tmp), "--now", _iso(NOW)])
        assert rc == 0
        assert _tree_snapshot(tmp) == before
        out = capsys.readouterr().out
        summary = json.loads(
            out.split("[Bry軸重評][SUMMARY] ", 1)[1].splitlines()[0]
        )
        assert summary["mode"] == "dry-run"
        assert summary["writes"] == 0
        assert summary["credited"] == 1
        assert summary["results"][0]["band_before"] == "stranger"
        assert summary["results"][0]["band_after"] == "known"
        assert summary["results"][0]["ref"] == f"rel:{BRYAN}:{_iso(NOW)}"

    def test_dry_run_no_signal_zero_write_and_no_change(self, iso_env, capsys):
        """窗口外 → dry-run 0 写入且报告 no_signal（不推进 ref）。"""
        tmp = iso_env
        self._seed(tmp, last_interaction_at=_iso(NOW - timedelta(hours=30)))
        before = _tree_snapshot(tmp)
        reeval.main(["--data-root", str(tmp), "--now", _iso(NOW)])
        assert _tree_snapshot(tmp) == before
        assert '"status": "no_signal"' in capsys.readouterr().out

    def test_apply_uses_official_write_entry_and_promotes(self, iso_env, monkeypatch, caplog):
        """--apply: 经**正规入口** apply_relation_evaluation（spy 断言）, 落盘带位
        变更 + [BAND_MIGRATION] promote 审计行; 0 硬写 band 值。"""
        tmp = iso_env
        self._seed(tmp, peer=True)
        from src.soul.relationships import RelationshipsStore
        calls: List[Dict[str, Any]] = []
        orig = RelationshipsStore.apply_relation_evaluation

        def spy(self, other_id, **kwargs):
            calls.append({"agent": self.agent_id, "other": other_id, **kwargs})
            return orig(self, other_id, **kwargs)

        monkeypatch.setattr(RelationshipsStore, "apply_relation_evaluation", spy)
        peer_before = _read_entry(tmp, PEER)

        with caplog.at_level(logging.INFO, logger=SETTLE_LOGGER):
            rc = reeval.main(["--data-root", str(tmp), "--now", _iso(NOW), "--apply"])
        assert rc == 0

        # 正规入口被调用, 且只对 Bry 軸
        assert [c["other"] for c in calls] == [BRYAN]
        c = calls[0]
        assert c["ref"] == f"rel:{BRYAN}:{_iso(NOW)}"
        assert c["co_presence_sessions_delta"] == 1
        assert c["reply_exchanges_delta"] == 1
        assert c["dream_exchanges_delta"] == 0

        entry = _read_entry(tmp)
        assert entry["relational_band"] == "known"          # band 由纯函数算出
        assert entry["band_updated_at"] == _iso(NOW)        # 首次写入
        assert entry["objective"]["co_presence_sessions"] == 1
        assert entry["objective"]["reply_exchanges"] == 1
        assert entry["last_relation_update_ref"] == f"rel:{BRYAN}:{_iso(NOW)}"

        ms = _migrations(caplog)
        assert len(ms) == 1
        assert ms[0]["other"] == BRYAN
        assert ms[0]["direction"] == "promote"
        assert (ms[0]["from_band"], ms[0]["to_band"]) == ("stranger", "known")

        # peer 軸: 客观计数器 / 带位 / 审计欄位 0 位移
        peer_after = _read_entry(tmp, PEER)
        assert peer_after["objective"] == peer_before["objective"]
        assert peer_after["relational_band"] == peer_before["relational_band"]
        assert peer_after["band_updated_at"] == peer_before["band_updated_at"]
        assert peer_after["last_relation_update_ref"] == peer_before["last_relation_update_ref"]
        assert peer_after["interaction_count"] == peer_before["interaction_count"]
        assert peer_after["last_interaction_at"] == peer_before["last_interaction_at"]

    def test_apply_is_idempotent_for_same_now(self, iso_env, caplog):
        """同 --now 重跑 --apply → 0 追加变更（幂等 ref + 读侧幂等双保险）。"""
        tmp = iso_env
        self._seed(tmp)
        reeval.main(["--data-root", str(tmp), "--now", _iso(NOW), "--apply"])
        after_first = _read_entry(tmp)
        with caplog.at_level(logging.INFO, logger=SETTLE_LOGGER):
            reeval.main(["--data-root", str(tmp), "--now", _iso(NOW), "--apply"])
        assert _read_entry(tmp) == after_first
        assert _migrations(caplog) == []

    def test_agent_filter_and_missing_bry_entry_skipped(self, iso_env, capsys):
        """--agent 限定; 無 Bry entry 的 agent 直接跳過（不 ensure / 不新建）。"""
        tmp = iso_env
        self._seed(tmp)
        other = tmp / "soul" / "agent_nobry" / "relationships.json"
        other.parent.mkdir(parents=True, exist_ok=True)
        other.write_text(json.dumps({
            "agent_id": "agent_nobry", "schema_version": "4.2",
            "others": {PEER: _entry()},
        }, ensure_ascii=False), encoding="utf-8")
        before = _tree_snapshot(tmp)
        reeval.main(["--data-root", str(tmp), "--now", _iso(NOW),
                     "--agent", "agent_nobry"])
        assert _tree_snapshot(tmp) == before
        assert '"status": "no_bry_entry"' in capsys.readouterr().out

    def test_discover_only_agents_with_bry_entry(self, iso_env):
        tmp = iso_env
        self._seed(tmp)
        assert reeval.discover_agents_with_bry(tmp) == [AGENT]


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
