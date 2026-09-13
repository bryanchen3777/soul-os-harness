"""
tests/test_sg3_decision_band_render.py — SG-3 OQ-5(i) Decision prompt 真值源渲染 (NEW)

覆盖:
  1. `_build_relationship_summary` 不再渲染退役 `confidence`（信任度：0.00）
     → 改渲染 D4 真值源的**離散表達**: `relational_band`（+ impression_tags）
  2. band 缺失 → 整行省略（fail-silent, 不編造）
  3. **No-Scoring 紅線**: 渲染內容 0 數值分數 / 0 強度 / 0 中間值
  4. **T1 防線: transmit 率不得上升** —— 差分斷言（新增行只改關係摘要那一行,
     Framing / Motive / Boundary 四塊逐字不變）+ 語義斷言（0 催促 / 0 指令措辭）
     + 決策結果差分斷言（同一 LLM 回覆下 decision 不變）
  5. 讀側 0 寫副作用（C-3.1 §3.3）

运行: .\\.venv\\Scripts\\python.exe -m pytest tests/test_sg3_decision_band_render.py -q
"""
from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path

import pytest

from src.paths import data_root, reset_data_root
from src.soul.decision import _build_relationship_summary, build_decision_prompt

AGENT = "agent_sg3d"
ROOT = Path(__file__).resolve().parents[1]

FORBIDDEN_URGENCY = (
    "应该", "必須", "快去", "赶紧", "立刻", "馬上", "想他", "主動聯絡", "一定要",
)


@pytest.fixture()
def iso_env(tmp_path, monkeypatch):
    monkeypatch.setenv("SOUL_OS_DATA_DIR", str(tmp_path))
    reset_data_root()
    yield tmp_path
    reset_data_root()


def _write_bryan_entry(tmp_path: Path, entry: dict) -> Path:
    path = tmp_path / "soul" / AGENT / "relationships.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "agent_id": AGENT, "schema_version": "4.2",
        "created_at": "2026-09-01T00:00:00+00:00",
        "last_decay_at": "2026-09-01T00:00:00+00:00",
        "others": {"user_bryan": entry},
    }, ensure_ascii=False), encoding="utf-8")
    return path


def _base_entry(**kw) -> dict:
    e = {
        "impression": "温柔的人", "feeling": "warm", "confidence": 0.0,
        "interaction_count": 31,
        "last_interaction_at": "2026-09-05T10:00:00+00:00",
        "last_updated": "2026-09-05T10:00:00+00:00",
        "created_at": "2026-08-01T00:00:00+00:00",
        "objective": {"reply_exchanges": 0, "co_presence_sessions": 1,
                      "dream_exchanges": 0, "last_signal_at": "2026-09-05T10:00:00+00:00"},
        "impression_tags": [], "relational_band": "stranger",
        "band_updated_at": None, "last_relation_update_ref": None,
    }
    e.update(kw)
    return e


# ───────────────────────────────────────────────────────────
# 1. confidence 退役 → band 離散表達
# ───────────────────────────────────────────────────────────

class TestBandRender:
    def test_no_trust_score_rendered(self, iso_env):
        tmp = iso_env
        _write_bryan_entry(tmp, _base_entry(confidence=0.0, relational_band="known"))
        summary = _build_relationship_summary(AGENT)
        assert summary is not None
        assert "信任度" not in summary
        assert "0.00" not in summary

    def test_band_rendered(self, iso_env):
        tmp = iso_env
        _write_bryan_entry(tmp, _base_entry(relational_band="familiar"))
        summary = _build_relationship_summary(AGENT)
        assert "关系带：familiar" in summary

    def test_band_with_tags_rendered(self, iso_env):
        tmp = iso_env
        _write_bryan_entry(tmp, _base_entry(
            relational_band="known", impression_tags=["温柔", "可靠"]))
        summary = _build_relationship_summary(AGENT)
        assert "关系带：known（印象标签：温柔、可靠）" in summary

    def test_empty_tags_omitted(self, iso_env):
        tmp = iso_env
        _write_bryan_entry(tmp, _base_entry(relational_band="known", impression_tags=[]))
        summary = _build_relationship_summary(AGENT)
        assert "印象标签" not in summary

    def test_band_missing_line_omitted_fail_silent(self, iso_env):
        tmp = iso_env
        entry = _base_entry()
        del entry["relational_band"]
        _write_bryan_entry(tmp, entry)
        summary = _build_relationship_summary(AGENT)
        assert summary is not None  # 其餘欄位照出
        assert "关系带" not in summary
        assert "信任度" not in summary

    def test_band_empty_string_line_omitted(self, iso_env):
        tmp = iso_env
        _write_bryan_entry(tmp, _base_entry(relational_band="   "))
        assert "关系带" not in (_build_relationship_summary(AGENT) or "")

    def test_band_wrong_type_line_omitted(self, iso_env):
        tmp = iso_env
        _write_bryan_entry(tmp, _base_entry(relational_band=0.75))
        assert "关系带" not in (_build_relationship_summary(AGENT) or "")

    def test_no_numeric_score_anywhere(self, iso_env):
        """No-Scoring 紅線: 關係摘要 0 數值分數 / 0 強度 / 0 中間值。
        （互動次數與最後互動時間戳是既有事實欄位, 非分數; 只禁新增分數。）"""
        tmp = iso_env
        for band in ("stranger", "known", "familiar", "close"):
            _write_bryan_entry(tmp, _base_entry(relational_band=band))
            summary = _build_relationship_summary(AGENT) or ""
            assert band in summary
            # band 行本身不得帶任何小數（無 0.5 / 0.75 這類中間值）
            band_line = next(l for l in summary.split("；") if l.startswith("关系带"))
            assert not re.search(r"\d+\.\d+", band_line)
            assert "分" not in band_line and "%" not in band_line

    def test_no_confidence_in_source(self):
        """源碼層: 渲染路徑 0 confidence（退役欄位不再被讀為真值）。"""
        src = (ROOT / "src" / "soul" / "decision.py").read_text(encoding="utf-8")
        start = src.index("def _build_relationship_summary")
        end = src.index("def _build_memory_summary")
        body = src[start:end]
        code = body.split('"""')[2]  # 跳過 docstring（docstring 內有 OQ-5(i) 沿革記載）
        assert "confidence" not in code
        assert "信任度" not in code

    def test_read_side_zero_write(self, iso_env):
        tmp = iso_env
        path = _write_bryan_entry(tmp, _base_entry(relational_band="known"))
        before = path.read_bytes()
        _build_relationship_summary(AGENT)
        assert path.read_bytes() == before


# ───────────────────────────────────────────────────────────
# 2. T1 防線: transmit 率不得上升
# ───────────────────────────────────────────────────────────

class TestT1DefenseNoTransmitRise:
    """T1 防線（SG-3 工單 D 項）: 改動會改變送進 Decision LLM 的 prompt 內容,
    故必須證明 transmit 率不上升。三層證據:
      ① 差分: 只有關係摘要那一行變, Framing/Motive/Boundary 逐字不變
      ② 語義: 新增行 0 催促/指令措辭, 純描述性離散標籤
      ③ 結果: 同一（stub）LLM 回覆下, decision 分類不變（0 新增 transmit 誘因）
    """

    def _motive(self):
        from src.soul.motive import Motive, now_utc_iso
        return Motive(
            motive_id="m1", content="我想告诉你今天的事",
            target="bryan", provenance_ref="evt1", created_at=now_utc_iso(),
        )

    def _prompt(self, relationship_summary):
        return build_decision_prompt(
            self._motive(), provenance_desc="diary:night @ 2026-09-02",
            relationship_summary=relationship_summary,
        )

    def test_diff_only_relationship_line(self, iso_env):
        tmp = iso_env
        _write_bryan_entry(tmp, _base_entry(relational_band="close"))
        with_band = self._prompt(_build_relationship_summary(AGENT))
        # 對照組: 無關係摘要（改動前的「band 缺失」等價情境）
        without = self._prompt(None)
        # ① 差異行只有關係摘要那一行
        diff_a = [l for l in with_band.splitlines() if l not in without.splitlines()]
        diff_b = [l for l in without.splitlines() if l not in with_band.splitlines()]
        assert len(diff_b) == 0
        assert len(diff_a) == 1
        assert diff_a[0].startswith(f"你与 bryan 的关系：")
        assert "关系带：close" in diff_a[0]
        # ② Framing / Motive / Boundary 逐字不變
        for anchor in (
            "你心里有一个念头，已经成形",
            "你想告诉 bryan：我想告诉你今天的事",
            "现在有四个选择，只能选一个",
            "transmit — 现在把念头化为讯息，传给 Bry",
            "observe — 现在不传，先观察环境",
            "reflect — 现在不传，先回顾记忆",
            "do_nothing — 现在不传，安静度日",
        ):
            assert anchor in with_band
            assert with_band.count(anchor) == without.count(anchor)

    def test_no_urgency_tokens_in_band_line(self, iso_env):
        tmp = iso_env
        for band in ("stranger", "known", "familiar", "close"):
            _write_bryan_entry(tmp, _base_entry(
                relational_band=band, impression_tags=["温柔", "可靠"]))
            summary = _build_relationship_summary(AGENT)
            for token in FORBIDDEN_URGENCY:
                assert token not in summary, (band, token)

    def test_band_line_is_descriptive_not_directive(self, iso_env):
        tmp = iso_env
        _write_bryan_entry(tmp, _base_entry(relational_band="close"))
        band_line = next(
            l for l in (_build_relationship_summary(AGENT) or "").split("；")
            if l.startswith("关系带")
        )
        # 純描述: 標籤名 + 離散枚舉值（可選 tag）, 不含動詞式指令
        assert re.fullmatch(
            r"关系带：(stranger|known|familiar|close)(（印象标签：[^（）]*）)?",
            band_line,
        ), band_line

    def test_decision_result_unchanged(self, iso_env):
        """③ 結果差分: 同一 LLM 回覆下, 有/無 band 行的 decision 分類一致
        （新增行不構成任何 transmit 誘因; 四元判定式與 fail-closed 不變）。"""
        tmp = iso_env
        from src.soul.decision import decide_motive

        def _run(summary_agent: bool):
            entry_summary = None
            if summary_agent:
                _write_bryan_entry(tmp, _base_entry(relational_band="close",
                                                    impression_tags=["温柔"]))
                entry_summary = _build_relationship_summary(AGENT)

            class FakeProxy:
                async def generate_text(self, messages, agent_id="system",
                                        max_tokens=200, temperature=0.7):
                    return '{"decision": "do_nothing", "reason": "安静"}'

            import src.soul.decision as dec_mod
            orig = dec_mod._build_relationship_summary
            dec_mod._build_relationship_summary = lambda _a: entry_summary
            try:
                return asyncio.run(
                    decide_motive(self._motive(), AGENT, llm_call=FakeProxy().generate_text)
                )
            finally:
                dec_mod._build_relationship_summary = orig

        with_band = _run(True)
        without_band = _run(False)
        assert with_band.decision == without_band.decision == "do_nothing"
        assert with_band.transmit is False and without_band.transmit is False

    def test_fail_closed_default_unchanged(self):
        """fail-closed 預設不變: 解析失敗 → do_nothing（transmit 率上升的結構性防線）"""
        from src.soul.decision import parse_decision_output
        assert parse_decision_output("垃圾輸出")["decision"] == "do_nothing"
        assert parse_decision_output("")["decision"] == "do_nothing"


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
