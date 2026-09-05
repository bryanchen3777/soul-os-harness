"""
tests/social/test_relationship_store.py — SG-2 存储层验收（D1, schema 4.2）

覆盖: 4.2 字段 roundtrip / 4.1 旧数据兼容读取（缺省）/ 幂等键去重 /
0 触 SAGE/Submissions / update_impression tags additive。

运行: .\\.venv\\Scripts\\python.exe -m pytest tests/social/test_relationship_store.py -v
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Dict, Any

import pytest

from src.soul.relationships import (
    MultiAgentRelationshipsManager,
    RelationshipsStore,
)
from src.social.relational_bands import (
    BAND_CLOSE,
    BAND_FAMILIAR,
    BAND_KNOWN,
    BAND_STRANGER,
)


# ───────────────────────────────────────────────────────────
# helpers
# ───────────────────────────────────────────────────────────

def _store(tmp_path: Path, agent_id: str = "agent_test") -> RelationshipsStore:
    return RelationshipsStore(agent_id=agent_id, data_dir=tmp_path / "soul" / agent_id)


def _raw(tmp_path: Path, agent_id: str = "agent_test") -> Dict[str, Any]:
    return json.loads(
        (tmp_path / "soul" / agent_id / "relationships.json").read_text(encoding="utf-8")
    )


def _write_41_file(tmp_path: Path, agent_id: str = "agent_test") -> None:
    """写一份 4.1 旧格式文件（无 4.2 字段）。"""
    path = tmp_path / "soul" / agent_id / "relationships.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "agent_id": agent_id,
        "schema_version": "4.1",
        "created_at": "2026-09-01T00:00:00+00:00",
        "last_decay_at": "2026-09-01T00:00:00+00:00",
        "others": {
            "agent_rem": {
                "impression": "優しい笑顔が忘れられない",
                "feeling": "neutral",
                "confidence": 0.5,
                "interaction_count": 7,
                "last_interaction_at": "2026-09-05T10:00:00+00:00",
                "last_updated": "2026-09-05T10:00:00+00:00",
                "created_at": "2026-08-01T00:00:00+00:00",
            }
        },
    }
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


# ───────────────────────────────────────────────────────────
# 4.2 结构
# ───────────────────────────────────────────────────────────

class TestSchema42:
    def test_new_file_schema_version_42_and_8_fields(self, tmp_path):
        s = _store(tmp_path)
        raw = _raw(tmp_path)
        assert raw["schema_version"] == "4.2"
        entry = raw["others"]
        # 新 entry 经 ensure_relationship 才有 8 字段（others 初始为空）
        e = s.ensure_relationship("agent_rem")
        assert e["objective"] == {
            "reply_exchanges": 0,
            "co_presence_sessions": 0,
            "dream_exchanges": 0,
            "last_signal_at": None,
        }
        assert e["impression_tags"] == []
        assert e["relational_band"] == BAND_STRANGER
        assert e["band_updated_at"] is None
        assert e["last_relation_update_ref"] is None
        # 旧 7 字段保留
        for field in ("impression", "feeling", "confidence", "interaction_count",
                      "last_interaction_at", "last_updated", "created_at"):
            assert field in e, field

    def test_apply_relation_evaluation_roundtrip(self, tmp_path):
        s = _store(tmp_path)
        now = "2026-09-06T10:00:00+00:00"
        entry = s.apply_relation_evaluation(
            "agent_rem",
            reply_exchanges_delta=1,
            co_presence_sessions_delta=2,
            dream_exchanges_delta=1,
            ref="rel:agent_rem:2026-09-06T10:00:00+00:00",
            now_iso=now,
        )
        # 客观分区计数落地
        assert entry["objective"]["reply_exchanges"] == 1
        assert entry["objective"]["co_presence_sessions"] == 2
        assert entry["objective"]["dream_exchanges"] == 1
        assert entry["objective"]["last_signal_at"] == now
        # 状态机步进: stranger + reply1(or co2) → known
        assert entry["relational_band"] == BAND_KNOWN
        assert entry["band_updated_at"] == now
        # 幂等键
        assert entry["last_relation_update_ref"] == "rel:agent_rem:2026-09-06T10:00:00+00:00"
        # 旧字段仍在（legacy 保留读取）; confidence 未被本路径写入
        assert "confidence" in entry
        assert "impression" in entry
        # 整数类型断言（0 float 计数）
        assert isinstance(entry["objective"]["reply_exchanges"], int)

    def test_idempotent_ref_skips_duplicate(self, tmp_path):
        s = _store(tmp_path)
        now = "2026-09-06T10:00:00+00:00"
        ref = "rel:agent_rem:2026-09-06T10:00:00+00:00"
        s.apply_relation_evaluation("agent_rem", reply_exchanges_delta=1,
                                    ref=ref, now_iso=now)
        entry = s.apply_relation_evaluation("agent_rem", reply_exchanges_delta=1,
                                            ref=ref, now_iso=now)
        # 同引用重复写 → 0 变更（计数不重复累加）
        assert entry["objective"]["reply_exchanges"] == 1
        assert entry["relational_band"] == BAND_KNOWN
        # 磁盘也幂等
        raw_entry = _raw(tmp_path)["others"]["agent_rem"]
        assert raw_entry["objective"]["reply_exchanges"] == 1

    def test_new_ref_applies_again(self, tmp_path):
        """跨评估窗口（新 ts）→ 正常再沉淀。"""
        s = _store(tmp_path)
        s.apply_relation_evaluation("agent_rem", reply_exchanges_delta=1,
                                    ref="rel:agent_rem:day1", now_iso="2026-09-06T10:00:00+00:00")
        entry = s.apply_relation_evaluation("agent_rem", reply_exchanges_delta=2,
                                            ref="rel:agent_rem:day2", now_iso="2026-09-07T10:00:00+00:00")
        assert entry["objective"]["reply_exchanges"] == 3

    def test_climb_ladder_over_evaluations(self, tmp_path):
        """逐级爬带（每评估窗口至多升 1 级; 累计计数驱动）。"""
        s = _store(tmp_path)
        s.apply_relation_evaluation("agent_rem", reply_exchanges_delta=3,
                                    co_presence_sessions_delta=5,
                                    ref="w1", now_iso="2026-09-06T10:00:00+00:00")
        e = s.get("agent_rem")
        # stranger→known（第一级）
        assert e["relational_band"] == BAND_KNOWN
        # 下一评估窗口: 无信号 → 慢爬（计数已满足）→ known→familiar
        s.apply_relation_evaluation("agent_rem", ref="w2", now_iso="2026-09-07T10:00:00+00:00")
        assert s.get("agent_rem")["relational_band"] == BAND_FAMILIAR

    def test_demote_after_30_days_no_signal(self, tmp_path):
        s = _store(tmp_path)
        s.apply_relation_evaluation("agent_rem", reply_exchanges_delta=10,
                                    co_presence_sessions_delta=15,
                                    ref="w1", now_iso="2026-08-01T10:00:00+00:00")
        e = s.get("agent_rem")
        assert e["relational_band"] == BAND_KNOWN  # 每评估窗口至多升 1 级: stranger→known
        # 第二轮慢爬: 计数满足 familiar 门槛 → known→familiar
        s.apply_relation_evaluation("agent_rem", ref="w2", now_iso="2026-08-02T10:00:00+00:00")
        assert s.get("agent_rem")["relational_band"] == BAND_FAMILIAR
        # 45 天后无信号 → 降 1 带 → known
        s.apply_relation_evaluation("agent_rem", ref="w3", now_iso="2026-09-16T10:00:00+00:00")
        assert s.get("agent_rem")["relational_band"] == BAND_KNOWN

    def test_negative_delta_rejected(self, tmp_path):
        s = _store(tmp_path)
        entry = s.apply_relation_evaluation("agent_rem", reply_exchanges_delta=-5, ref="w1")
        assert entry["objective"]["reply_exchanges"] == 0

    def test_update_impression_with_tags_additive(self, tmp_path):
        s = _store(tmp_path)
        entry = s.update_impression("agent_rem", "優しい人", impression_tags=["warm", "quiet"])
        assert entry["impression"] == "優しい人"
        assert entry["impression_tags"] == ["warm", "quiet"]
        # 既有调用（无 tags 参数）→ tags 不动（缺省 0 破坏）
        s2 = _store(tmp_path, agent_id="agent_test2")
        e2 = s2.update_impression("agent_rem", "優しい人")
        assert e2.get("impression_tags") == []  # 新 entry 缺省空
        e2b = s2.update_impression("agent_rem", "優しい人", impression_tags=None)
        assert e2b.get("impression_tags") == []


# ───────────────────────────────────────────────────────────
# 4.1 旧数据兼容（0 迁移）
# ───────────────────────────────────────────────────────────

class TestLegacy41Compat:
    def test_read_41_file_with_defaults(self, tmp_path):
        _write_41_file(tmp_path)
        s = _store(tmp_path)
        entry = s.get("agent_rem")
        # 旧字段保留读取（confidence 数值会被既有 decay 衰减, 只断言类型存在）
        assert entry["impression"] == "優しい笑顔が忘れられない"
        assert isinstance(entry["confidence"], float)
        assert entry["interaction_count"] == 7
        # 新字段缺省（契约 §2.3: entry.get 缺省即兼容）
        assert entry.get("relational_band", BAND_STRANGER) == BAND_STRANGER
        assert entry.get("impression_tags", []) == []
        assert entry.get("objective", {}) == {}
        assert entry.get("last_relation_update_ref") is None

    def test_41_entry_can_be_evaluated_into_42(self, tmp_path):
        _write_41_file(tmp_path)
        s = _store(tmp_path)
        entry = s.apply_relation_evaluation("agent_rem", reply_exchanges_delta=2, ref="w1")
        # 4.1 数据无缝接入 4.2 评估（0 迁移成本）; 旧文件 version 保持 4.1（0 强制迁移）
        assert entry["objective"]["reply_exchanges"] == 2
        assert entry["relational_band"] == BAND_KNOWN
        assert _raw(tmp_path)["schema_version"] == "4.1"

    def test_file_level_schema_version_untouched_for_legacy(self, tmp_path):
        """旧文件 schema_version 保持 4.1（0 迁移, 读侧兼容）; 仅新文件写 4.2。"""
        _write_41_file(tmp_path)
        _store(tmp_path).get("agent_rem")
        assert _raw(tmp_path)["schema_version"] == "4.1"


# ───────────────────────────────────────────────────────────
# SG-2.1 慢爬回升要求窗口内有新信号 (无信号底带不回升)
# ───────────────────────────────────────────────────────────

class TestSG21SlowClimbRequiresSignal:
    """SG-2.1 修复 (TL-9 呈报主大脑拍板): 无信号时底带 stranger 不允许凭历史
    计数慢爬回升 (离散遗忘语义); 有信号时正常升级路径与带≥known 慢爬不变。"""

    # 时间链: 2026-08-01 有信号 → 2026-09-03 (33d)/10-05 (65d)/11-06 (97d)
    # 无信号结算各降 1 带, 11-07 新信号恢复
    BASE = "2026-08-01T10:00:00+00:00"

    def _demote_to_stranger(self, s: RelationshipsStore) -> None:
        """走真实评估链 familiar→known→stranger (计数 5/6 保留不清零)。"""
        s.apply_relation_evaluation(
            "agent_rem", reply_exchanges_delta=5, co_presence_sessions_delta=6,
            ref="w1", now_iso=self.BASE,
        )
        assert s.get("agent_rem")["relational_band"] == BAND_KNOWN
        # w2: 无信号慢爬 (带≥known 不受 SG-2.1 影响) → known→familiar
        s.apply_relation_evaluation(
            "agent_rem", ref="w2", now_iso="2026-08-02T10:00:00+00:00",
        )
        assert s.get("agent_rem")["relational_band"] == BAND_FAMILIAR
        # w3/w4: 33 天 / 65 天无信号 → 各降 1 带 → stranger
        s.apply_relation_evaluation(
            "agent_rem", ref="w3", now_iso="2026-09-03T10:00:00+00:00",
        )
        assert s.get("agent_rem")["relational_band"] == BAND_KNOWN
        s.apply_relation_evaluation(
            "agent_rem", ref="w4", now_iso="2026-10-05T10:00:00+00:00",
        )
        assert s.get("agent_rem")["relational_band"] == BAND_STRANGER

    def test_no_signal_stranger_no_slow_climb_rebound(self, tmp_path):
        """无信号 + 底带 stranger + 累计计数非零 → 保持 stranger (振荡消除)。"""
        s = _store(tmp_path)
        self._demote_to_stranger(s)
        # w5: 降带后再 30+ 天无信号 → 底带不降且不慢爬回升
        e = s.apply_relation_evaluation(
            "agent_rem", ref="w5", now_iso="2026-11-06T10:00:00+00:00",
        )
        assert e["relational_band"] == BAND_STRANGER
        # 计数保留 (不清零), last_signal_at 不更新 (无新信号)
        assert e["objective"]["reply_exchanges"] == 5
        assert e["objective"]["co_presence_sessions"] == 6
        assert e["objective"]["last_signal_at"] == self.BASE

    def test_new_signal_recovers_known_from_stranger(self, tmp_path):
        """窗口内新 reply 信号 → 底带 stranger 正常升回 known (门槛 reply≥1 照旧)。"""
        s = _store(tmp_path)
        self._demote_to_stranger(s)
        e = s.apply_relation_evaluation(
            "agent_rem", reply_exchanges_delta=1, ref="w6",
            now_iso="2026-11-07T10:00:00+00:00",
        )
        # 有信号 → 正常升级路径 (每窗至多升 1 级)
        assert e["relational_band"] == BAND_KNOWN
        # 计数累计 (不清零): 5+1=6; last_signal_at 刷新
        assert e["objective"]["reply_exchanges"] == 6
        assert e["objective"]["co_presence_sessions"] == 6
        assert e["objective"]["last_signal_at"] == "2026-11-07T10:00:00+00:00"


# ───────────────────────────────────────────────────────────
# SG-2.2 4.1 老数据 band 键兜底（无信号 stranger × 老 entry 组合缺口）
# ───────────────────────────────────────────────────────────

class TestSG22BandKeyCompat:
    """SG-2.2 修复（生产实证: settle_relations 每 30s KeyError 'relational_band'）:
    老 4.1 entry 无 relational_band 键 + 无信号 stranger 对子 → SG-2.1 跳过升级
    分支后写盘, 末尾 debug 日志直接索引炸 KeyError。修复 = 写盘前 setdefault
    'stranger'（语义 = get 默认值, 升级/降带/慢爬逻辑 0 变更）。本类覆盖
    4.1 老数据 × SG-2.1 跳过路径组合缺口 + 升级路径不受影响回归。"""

    NOW = "2026-09-06T10:00:00+00:00"

    def test_41_entry_no_signal_stranger_no_keyerror_and_backfill(self, tmp_path):
        """老 4.1 entry（无 relational_band 键）× 无信号 stranger 对子 → 不抛
        KeyError、补全 band=="stranger"、sidecar 幂等 ref / last_updated 正常落盘。"""
        _write_41_file(tmp_path)
        s = _store(tmp_path)
        entry = s.apply_relation_evaluation(
            "agent_rem",
            ref="rel:agent_rem:sg22-w1",
            now_iso=self.NOW,
        )
        # 不抛 KeyError（隐式）; band 补全 stranger
        assert entry["relational_band"] == BAND_STRANGER
        # sidecar 正常推进（修复前: 无信号 stranger 跳过分支不写 band,
        # objective/ref 落盘后 528 行日志直接索引炸 → 本行不达）
        assert entry["last_relation_update_ref"] == "rel:agent_rem:sg22-w1"
        assert entry["last_updated"] == self.NOW
        # 0 信号 → 0 增量（计数不凭空增长）
        assert entry["objective"]["reply_exchanges"] == 0
        # 磁盘也补全（setdefault 后重写, 半成品自愈）
        raw_entry = _raw(tmp_path)["others"]["agent_rem"]
        assert raw_entry["relational_band"] == "stranger"
        assert raw_entry["last_relation_update_ref"] == "rel:agent_rem:sg22-w1"
        assert raw_entry["last_updated"] == self.NOW

    def test_41_entry_no_signal_idempotent_ref_after_backfill(self, tmp_path):
        """补全后同 ref 重复结算 0 变更（幂等 ref 正常, 不重复累加 / 不重写 band）。"""
        _write_41_file(tmp_path)
        s = _store(tmp_path)
        s.apply_relation_evaluation("agent_rem", ref="rel:agent_rem:sg22-w2",
                                    now_iso=self.NOW)
        entry = s.apply_relation_evaluation("agent_rem", ref="rel:agent_rem:sg22-w2",
                                            now_iso=self.NOW)
        assert entry["relational_band"] == BAND_STRANGER
        assert entry["objective"]["reply_exchanges"] == 0
        raw_entry = _raw(tmp_path)["others"]["agent_rem"]
        assert raw_entry["objective"]["reply_exchanges"] == 0

    def test_41_entry_new_signal_upgrade_path_unchanged(self, tmp_path):
        """有信号时正常升级路径不受 SG-2.2 影响: 老 4.1 entry 带 reply 信号 →
        stranger→known 照旧（setdefault 兜底 no-op）。"""
        _write_41_file(tmp_path)
        s = _store(tmp_path)
        entry = s.apply_relation_evaluation(
            "agent_rem", reply_exchanges_delta=2,
            ref="rel:agent_rem:sg22-w3", now_iso=self.NOW,
        )
        assert entry["relational_band"] == BAND_KNOWN
        assert entry["band_updated_at"] == self.NOW
        assert entry["objective"]["reply_exchanges"] == 2
        assert entry["objective"]["last_signal_at"] == self.NOW


# ───────────────────────────────────────────────────────────
# 隔离防线（0 触 SAGE / Submissions / InnerLifeWriter）
# ───────────────────────────────────────────────────────────

class TestIsolation:
    def test_zero_sage_facts_written(self, tmp_path):
        s = _store(tmp_path)
        s.apply_relation_evaluation("agent_rem", reply_exchanges_delta=1, ref="w1")
        # graph.sqlite 根本不存在（写路径 0 触 graph_store）
        db = tmp_path / "memory" / "agent_test" / "graph.sqlite"
        assert not db.exists()
        # 唯一产物 = relationships.json（独立文件域）
        assert (tmp_path / "soul" / "agent_test" / "relationships.json").exists()

    def test_write_path_only_relationships_store(self, tmp_path):
        """manager 的 on_agent_speak / on_dream / on_event 仍走既有 touch（legacy）;
        SG-2 新写入面只有 apply_relation_evaluation（4.2 字段, 0 confidence 运算）。"""
        mgr = MultiAgentRelationshipsManager(data_dir=str(tmp_path / "soul"))
        mgr.on_agent_speak("agent_a", ["agent_a", "agent_b"])
        store = mgr.get_store("agent_a")
        entry = store.get("agent_b")
        # legacy touch 仍维护旧字段（回归 0 破坏）
        assert entry["interaction_count"] == 1
        # 4.2 客观分区仍为初始值（touch 不写 objective — 采集层 0 写关系文件新字段）
        assert entry["objective"]["reply_exchanges"] == 0
        assert entry["relational_band"] == BAND_STRANGER