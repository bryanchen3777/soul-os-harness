# tests/inner_life/test_drive_profile.py
"""CAMPAIGN-B-P1 §4.1 T1/T2/T3 — persona 驅動解析（唯讀、無 fallback）。

工單：logs/CAMPAIGN-B-D1-D3-TENSION-DRIVER-WORK-ORDER.md

覆蓋：
  - **T1** 驅動解析正確（`agent_akane` ⇒ 恰好 3 筆，code 序 D1,D2,D3；每筆
    label／trigger_phrase／effect_phrase 與 persona 原文**逐字相等**）。
  - **T2** 解析**唯讀**（解析前後 persona 的 bytes 與 sha256 恆等；目錄不被新增檔案）。
  - **T3** 無 D 結構者**誠實為空**（`agent_rem`／`agent_ruka` ⇒ `[]`／`None`；
    **且**不得回退到 akane 的內容）。

🔴 本檔不使用 subprocess／os.spawn*／os.system／os.popen（R5 護欄）。
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from src.inner_life import drive_profile as dp

REPO_ROOT = Path(__file__).resolve().parents[2]
PERSONAS_DIR = REPO_ROOT / "personas"

#: 期望值**內嵌**（不重新讀檔比對；逐字取自 ``personas/agent_akane.md:66-76``）。
#: 格式：code → (label, trigger_phrase, effect_phrase, source_line_span)
EXPECTED_AKANE = {
    "D1": (
        "理解驅動",
        "不理解他人 = 存在焦慮上升",
        "→「如果我無法理解你，我就沒有資格待在你身邊。」",
        (66, 68),
    ),
    "D2": (
        "被需要驅動",
        "被忽略（而非被拒絕）= 自我價值下降",
        "→ 對方沒有來找她，比對方直接拒絕她更讓她動搖。",
        (70, 72),
    ),
    "D3": (
        "邊界修復驅動",
        "崩潰後自動尋找「錨點」",
        "→ 她不等別人把她撈起來；她會自己找一件確定的事，然後繼續走。",
        (74, 76),
    ),
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _persona_files() -> set:
    return {p.name for p in PERSONAS_DIR.iterdir() if p.is_file()}


# ── T1：驅動解析正確 ─────────────────────────────────────────────────────
class TestT1DriveParsing:
    def test_t1_akane_has_exactly_three_drives_in_fixed_order(self):
        drives = dp.parse_drives("agent_akane")
        assert len(drives) == 3, f"應恰好 3 筆，實得 {len(drives)}"
        assert [d.code for d in drives] == ["D1", "D2", "D3"]

    def test_t1_each_drive_is_verbatim_against_embedded_expectation(self):
        for drive in dp.parse_drives("agent_akane"):
            expected = EXPECTED_AKANE[drive.code]
            assert drive.label == expected[0], f"{drive.code} label 不符"
            assert drive.trigger_phrase == expected[1], f"{drive.code} 觸發行不符"
            assert drive.effect_phrase == expected[2], f"{drive.code} 效應行不符"
            assert tuple(drive.source_line_span) == expected[3], f"{drive.code} 行號區間不符"

    def test_t1_profile_carries_agent_id_and_source_persona(self):
        profile = dp.load_drive_profile("agent_akane")
        assert profile is not None
        assert profile.agent_id == "agent_akane"
        assert profile.codes() == ("D1", "D2", "D3")
        for drive in profile.drives:
            assert drive.source_persona == "personas/agent_akane.md"

    def test_t1_effect_phrase_keeps_arrow_prefix_verbatim(self):
        """效應行含「→」前綴 ⇒ 解析**不得**吃掉它（逐字語義）。"""
        by_code = {d.code: d for d in dp.parse_drives("agent_akane")}
        assert by_code["D1"].effect_phrase.startswith("→「")
        assert by_code["D2"].effect_phrase.startswith("→ ")
        assert by_code["D3"].effect_phrase.startswith("→ ")


# ── T2：解析唯讀 ─────────────────────────────────────────────────────────
class TestT2ParseIsReadOnly:
    def test_t2_persona_bytes_and_sha256_are_identical_after_parse(self):
        path = PERSONAS_DIR / "agent_akane.md"
        before_bytes = path.read_bytes()
        before_sha = _sha256(path)
        dp.parse_drives("agent_akane")
        dp.load_drive_profile("agent_akane")
        assert path.read_bytes() == before_bytes, "persona bytes 不得被改動"
        assert _sha256(path) == before_sha, "persona sha256 不得變動"

    def test_t2_persona_directory_gains_no_file(self):
        before = _persona_files()
        dp.parse_drives("agent_akane")
        assert _persona_files() == before, "personas/ 目錄不得被新增檔案"


# ── T3：無 D 結構者誠實為空 ─────────────────────────────────────────────
class TestT3NoFallbackForOtherPersonas:
    def test_t3_personas_without_drive_structure_return_empty(self):
        for agent_id in ("agent_rem", "agent_ruka"):
            assert dp.parse_drives(agent_id) == [], f"{agent_id} 應回 []"
            assert dp.load_drive_profile(agent_id) is None, f"{agent_id} 應回 None"

    def test_t3_empty_result_is_not_akane_content(self):
        """**不得**回退到 akane 的內容（fallback／模板／預設值一律禁止）。"""
        akane = dp.parse_drives("agent_akane")
        assert akane, "前提：akane 應解析出驅動"
        for agent_id in ("agent_rem", "agent_ruka"):
            other = dp.parse_drives(agent_id)
            assert other != akane, f"{agent_id} 不得回退到 akane 的驅動"
            for drive in other:
                assert drive not in akane

    def test_t3_unknown_agent_is_empty_not_error(self):
        assert dp.parse_drives("agent_does_not_exist") == []
        assert dp.load_drive_profile("agent_does_not_exist") is None

    def test_t3_blank_agent_id_is_empty(self):
        assert dp.parse_drives("") == []
        assert dp.load_drive_profile("   ") is None
