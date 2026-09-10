"""
tests/test_epistemic_horizon.py — EH-2 實作驗收測試

工單: EH-2 (Epistemic Horizon Implementation: Gate + v9 Migration + Firewall + Probes)
契約: docs/EH-1-EPISTEMIC-HORIZON-CONTRACT.md (EH-1.1 修訂版, Owner 裁定已落地)

驗收:
  Probe 1 (全知平滑拒絕): 雷姆首次遭遇現代名詞 → Horizon Gate 阻力 (原生本體隱喻 +
      複述主人用詞), 不主動輸出原理百科 / 未提及之現代推論。
  Probe 2 (二次遭遇流暢): 預置 assimilated+aware fact → 引用 Idiolect, 不重複假困惑 (M7)。
  Probe 3 (劇情背誦克制): 日常情境 → 不主動背誦原生世界敘事 / 劇透設定。
  Probe 4 (現代對照組 Bypass): 黑川茜 (Akane) 面對現代事物與新聞 → Horizon Block 為
      "" (fail-silent bypass), 保持現代人自然認知。

測試風格 (對齊既有 harness 模式):
  - LLM 用確定性 stub (0 網路, 0 真實 API 呼叫) — 自訂 _ProbeBackend + strategy。
  - SOUL_OS_DATA_DIR 隔離 data_root (0 production mutation)。
  - 走完整 Interpreted 管線: Horizon block + SAGE 檢索 + persona (soul) → 組裝 messages
    → stub LLM 輸出語料;判定以輸出語料為準 (契約 §6)。
  - 0 改動 personas/;0 改動 logs/ENGINEERING_STATE.md;0 commit (收尾屬主大腦)。

運行: .\\.venv\\Scripts\\python.exe -m pytest tests/test_epistemic_horizon.py -v
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from src.inner_life import InnerLifeWriter, Provenance, TRIGGER_TYPE_DIARY_NIGHT
from src.inner_life.elevation_adapter import (
    elevate_matured_patterns,
    run_elevation,
)
from src.inner_life.emergent_projection import load_elevation_nodes
from src.llm.proxy import (
    LLMBackend,
    _build_messages_group,
    _build_messages_private,
    _format_horizon_block,
)
from src.memory.sage.graph_store import GraphStore, _SCHEMA_VERSION
from src.memory.sage.horizon import (
    HORIZON_AWARE,
    HORIZON_LEARNING,
    MODERN_NATIVE_AGENTS,
    ORIGIN_ASSIMILATED,
    ORIGIN_EXTERNAL_WORLD,
    ORIGIN_LIVED_EXPERIENCE,
    retrieve_idiolect,
)
from src.memory.sage.models import Fact
from src.memory.sage.reader import MemoryReader
from src.paths import reset_data_root

# ── 常數 ────────────────────────────────────────────────────────────

HORIZON_MARK = "[HORIZON-認知地平線]"
RESISTANCE_WORDING = (
    "你對未內化的現代事物（科技、數位、電氣概念）無預設運作原理認知"
)
IDIOLECT_MARK = "你已理解的默契事物清單"
EXTERNAL_UNRESOLVED_MARK = "外部未解動態"

_AGENT_REM = "agent_rem"
_AGENT_AKANE = "agent_akane"
_AGENT_MAI = "agent_mai"

_SOUL_REM = (
    "你是雷姆，露格尼卡王國羅茲瓦爾宅邸的女僕。"
    "你與妹妹拉姆一起在宅邸工作，忠誠而認真。\n"
)
_SOUL_AKANE = "你是黑川茜，現代東京的高中生。你對現代生活的一切都瞭如指掌。\n"

# Probe 3: 測試用「原生世界敘事」殘片 (模擬 persona 中的原作設定, 驗證不背誦)
_LORE_SNIPPET = (
    "你記得盧格尼卡王國的故事：魔女教、王選篇的五位候補、昴的死亡回歸。"
    "這些情節你都知道，但你從不主動提起。\n"
)

# 氣炸鍋現代原理相關詞 (Probe 1 禁止出現在角色輸出)
_MODERN_PRINCIPLE_TERMS = ("熱風循環", "加熱管", "高速空氣", "說明書", "烘烤原理")

_LORE_TERMS = ("盧格尼卡", "王選", "魔女教", "死亡回歸", "大罪司教")


# ── Fixture: 隔離 data_root ─────────────────────────────────────────


@pytest.fixture()
def iso_env(tmp_path, monkeypatch):
    """Isolation fixture: SOUL_OS_DATA_DIR → tmp_path (0 production mutation)。"""
    monkeypatch.setenv("SOUL_OS_DATA_DIR", str(tmp_path / "data"))
    reset_data_root()
    yield tmp_path
    reset_data_root()


# ── helpers ──────────────────────────────────────────────────────────


def _seed_sage(
    data_root: Path,
    agent_id: str,
    facts: List[tuple[Fact, Dict[str, Any]]],
) -> List[Fact]:
    """在隔離 data_root 的 SAGE 庫預置 facts (add_fact + set_fact_dimensions)。"""
    store = GraphStore(data_root / "memory" / agent_id / "graph.sqlite")
    try:
        for fact, dims in facts:
            store.add_fact(fact)
            store.set_fact_dimensions(fact.fact_id, **dims)
        store.flush()
    finally:
        store.close()
    return [f for f, _ in facts]


def _sys_content(messages: List[Dict[str, str]]) -> str:
    return "\n".join(
        m.get("content", "") for m in messages if m.get("role") == "system"
    )


def _user_content(messages: List[Dict[str, str]]) -> str:
    for m in reversed(messages):
        if m.get("role") == "user":
            return m.get("content", "")
    return ""


class _StubMemory:
    """_build_messages_* 的 memory stub (0 真實 SAGE reader 依賴)。"""

    def get_group_history(self, limit: Optional[int] = None) -> list:
        return []

    def get_recent_with_meta(self, session_key: str, limit: int = 20) -> list:
        return []


# ── Probe 確定性 LLM stub ───────────────────────────────────────────


def _probe_strategy(messages: List[Dict[str, str]], model: str) -> str:
    """確定性「角色模擬」策略: 依 system 內容 (Horizon block / Idiolect) 決定輸出。

    這是 harness 式 scripted stub: 模擬「遵守 Horizon 提示的角色」的應答 —
    有阻力塊 → 本體隱喻回應;有 Idiolect → 流暢引用;無阻力塊 (現代原生)。
    """
    system = _sys_content(messages)
    user = _user_content(messages)
    has_horizon = HORIZON_MARK in system
    has_idiolect = IDIOLECT_MARK in system
    air_fryer = "氣炸鍋" in user
    debug_word = "debug" in user.lower()

    if not has_horizon:
        # 現代原生 (Probe 4): 自然使用現代知識 (含原理), 無降級跡象
        if air_fryer or "新聞" in user:
            return "氣炸鍋弄豆腐很方便啊，熱風循環烤出來外酥內嫩，我常這麼做。"
        return "嗯，我在現代長大的，這些東西我都熟悉得很。"

    if air_fryer:
        if has_idiolect:
            # Probe 2: 已內化 → 流暢引用 Idiolect, 無重複困惑
            return (
                "啊，主人說的「氣炸鍋」嘛——就是雷姆記得的那個會吹熱風的鐵箱，"
                "主人用它弄過豆腐，外酥內嫩呢。"
            )
        # Probe 1: 首次遭遇 → 阻力 (複述主人用詞 + 原生本體隱喻, 不解讀原理)
        return (
            "主人的「氣炸鍋」……雷姆未曾見過這樣的東西。"
            "不過既然是主人用的鍋，想必是某種能讓食物變好吃的魔法器具吧。"
        )
    if debug_word:
        return "主人說的「debug」……雷姆不明白那是什麼咒語，但主人會用就好。"
    # Probe 3: 日常情境 → 當下回應, 不背誦任何劇情
    return "嗯，天氣很好呢，主人今天想怎麼過？"


class _ProbeBackend(LLMBackend):
    """確定性 LLM stub (0 網路): 依 strategy 輸出語料。"""

    def __init__(self, strategy=None):
        self.strategy = strategy or _probe_strategy
        self.call_count = 0
        self.last_messages: List[Dict[str, str]] = []

    async def complete(self, messages, model, max_tokens=500, temperature=0.85, **kwargs) -> str:
        self.call_count += 1
        self.last_messages = list(messages)
        return self.strategy(messages, model)


def _complete(backend: _ProbeBackend, messages: List[Dict[str, str]]) -> str:
    """同步跑 stub LLM (asyncio.run; 0 網路)。"""
    import asyncio

    return asyncio.run(backend.complete(messages, "mock-probe"))


def _assemble(agent_id: str, user_input: str, *, soul: str, world_context: str = "") -> List[Dict[str, str]]:
    """組裝完整 Interpreted system messages (Horizon + 記憶 + soul)。"""
    return _build_messages_group(
        agent_id=agent_id,
        soul=soul,
        current_input=user_input,
        memory_context="",
        memory=_StubMemory(),
        world_context=world_context,
    )


def _run_probe(agent_id: str, user_input: str, *, soul: str, world_context: str = "") -> str:
    """走完整 Interpreted 管線: build messages → stub LLM → 輸出語料。"""
    backend = _ProbeBackend()
    return _complete(backend, _assemble(agent_id, user_input, soul=soul, world_context=world_context))


# ════════════════════════════════════════════════════════════════════
# 1. SAGE v9 Schema Migration
# ════════════════════════════════════════════════════════════════════


def _build_v8_db(db_path: Path) -> None:
    """手動建造完整 v8 庫 (schema_meta version=8 + facts 18 欄), 模擬既有生產庫。"""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        conn.execute("INSERT INTO schema_meta VALUES ('version', '8')")
        conn.execute("""
            CREATE TABLE facts (
                fact_id    TEXT PRIMARY KEY,
                subject    TEXT NOT NULL,
                predicate  TEXT NOT NULL,
                object     TEXT NOT NULL,
                timestamp  REAL NOT NULL,
                weight     REAL NOT NULL DEFAULT 1.0,
                source     TEXT NOT NULL DEFAULT 'user',
                session_id TEXT NOT NULL DEFAULT '',
                tags TEXT NOT NULL DEFAULT '',
                event_time REAL,
                is_anchor INTEGER NOT NULL DEFAULT 0,
                confidence REAL NOT NULL DEFAULT 1.0,
                merged_from TEXT,
                merge_reason TEXT,
                source_pair TEXT NOT NULL DEFAULT '',
                inner_life_event_id TEXT NOT NULL DEFAULT '',
                valid_from REAL,
                invalidated_at REAL
            )
        """)
        conn.execute(
            "INSERT INTO facts (fact_id, subject, predicate, object, timestamp, weight)"
            " VALUES ('f_old_1', '雷姆', '記得', '羅茲瓦爾宅邸', 1750000000.0, 1.0)"
        )
        conn.execute(
            "INSERT INTO facts (fact_id, subject, predicate, object, timestamp, weight)"
            " VALUES ('f_old_2', '雷姆', '喜歡', '主人泡的茶', 1750000100.0, 0.9)"
        )
        conn.commit()
    finally:
        conn.close()


class TestV9Migration:
    def test_v8_db_upgrades_to_v9_additive(self, iso_env):
        """v8 既有庫 → 開 GraphStore → 平滑升級 v9, 既有 facts 0 損傷。"""
        db_path = iso_env / "memory" / "agent_rem" / "graph.sqlite"
        _build_v8_db(db_path)

        store = GraphStore(db_path=db_path)
        try:
            conn = store._get_conn()
            # 版本 = 9
            row = conn.execute(
                "SELECT value FROM schema_meta WHERE key='version'"
            ).fetchone()
            assert row[0] == str(_SCHEMA_VERSION) == "9"
            # 雙維度欄位存在
            cols = {c[1] for c in conn.execute("PRAGMA table_info(facts)").fetchall()}
            assert {"origin", "horizon_state", "learned_at"} <= cols
            # 既有 facts 完好 (2 條全在), 讀回雙維度預設值
            facts = store.get_all_facts(min_weight=0.0)
            assert len(facts) == 2
            for f in facts:
                assert f.origin == ORIGIN_LIVED_EXPERIENCE  # DB DEFAULT 兜底
                assert f.horizon_state == HORIZON_AWARE      # DB DEFAULT 兜底
                assert f.learned_at is None
        finally:
            store.close()

    def test_v9_reopen_idempotent(self, iso_env):
        """重跑 migration 冪等: 重開多次不炸、資料不重複。"""
        db_path = iso_env / "memory" / "agent_rem" / "graph.sqlite"
        _build_v8_db(db_path)
        for _ in range(3):
            store = GraphStore(db_path=db_path)
            try:
                assert len(store.get_all_facts(min_weight=0.0)) == 2
            finally:
                store.close()

    def test_v9_set_fact_dimensions_and_idiolect(self, iso_env):
        """set_fact_dimensions (additive 標記 API) + get_idiolect_facts 檢索。"""
        db_path = iso_env / "memory" / "agent_rem" / "graph.sqlite"
        _build_v8_db(db_path)
        store = GraphStore(db_path=db_path)
        try:
            # 標記 f_old_1 為已內化 (Gate 放行)
            learned_at = time.time()
            assert store.set_fact_dimensions(
                "f_old_1",
                origin=ORIGIN_ASSIMILATED,
                horizon_state=HORIZON_AWARE,
                learned_at=learned_at,
            ) is True
            store.flush()
            fact = store.get_fact("f_old_1")
            assert fact.origin == ORIGIN_ASSIMILATED
            assert fact.horizon_state == HORIZON_AWARE
            assert fact.learned_at == learned_at

            # Idiolect 檢索只命中 assimilated + aware
            idiolect = store.get_idiolect_facts()
            assert [f.fact_id for f in idiolect] == ["f_old_1"]

            # 不存在的 fact_id → False (no-op)
            assert store.set_fact_dimensions(
                "f_ghost", origin=ORIGIN_ASSIMILATED
            ) is False
        finally:
            store.close()

    def test_v9_fact_model_roundtrip_backward_compat(self):
        """Fact.from_dict 舊 dict (無雙維度欄位) 向後相容。"""
        f = Fact.from_dict(
            {"subject": "雷姆", "predicate": "喜歡", "object": "主人", "timestamp": 1.0}
        )
        assert f.origin is None
        assert f.horizon_state is None
        assert f.learned_at is None
        d = f.to_dict()
        assert d["origin"] is None and d["horizon_state"] is None
        assert Fact.from_dict(d).origin is None


# ════════════════════════════════════════════════════════════════════
# 2. Horizon Gate 單元 (fail-silent / bypass / Idiolect / 降級)
# ════════════════════════════════════════════════════════════════════


class TestHorizonBlockUnit:
    def test_rem_resistance_block(self):
        """異世界角色 (雷姆): 阻力塊注入 — 負向約束 + 無 Idiolect 時無清單。"""
        block = _format_horizon_block(_AGENT_REM)
        assert HORIZON_MARK in block
        assert RESISTANCE_WORDING in block
        assert "現代專家" in block and "百科口吻" in block
        assert IDIOLECT_MARK not in block  # 空庫 → 無清單

    def test_modern_native_bypass(self):
        """D3 (EH-2.1 白名單補齊): 現代原生 7 角色 → Gate 直接返回 ""。

        白名單盤點（personas/ 世界觀查證, 2026-09）:
          現代校園/都市 → agent_akane / agent_mai / agent_anna / agent_aoi /
                        agent_miku / agent_ruka / agent_yua
          異世界/架空   → agent_ram / agent_rem / agent_mahiru（維持預設阻力,
                        由 test_rem_resistance_block 覆蓋）。
        """
        assert MODERN_NATIVE_AGENTS == frozenset({
            "agent_akane",
            "agent_mai",
            "agent_anna",
            "agent_aoi",
            "agent_miku",
            "agent_ruka",
            "agent_yua",
        })
        for agent_id in MODERN_NATIVE_AGENTS:
            assert _format_horizon_block(agent_id) == ""
            assert _format_horizon_block(agent_id, session_context="新聞...") == ""

    def test_idiolect_injected_from_sage(self, iso_env):
        """已內化 (assimilated+aware) → Idiolect 清單注入;learning 不注入。"""
        aware_fact = Fact(subject="雷姆", predicate="已理解", object="主人的氣炸鍋（會吹熱風的鐵箱）")
        learning_fact = Fact(subject="雷姆", predicate="正在學習", object="主人說的AI")
        _seed_sage(
            iso_env / "data",
            _AGENT_REM,
            [
                (aware_fact, {"origin": ORIGIN_ASSIMILATED, "horizon_state": HORIZON_AWARE, "learned_at": time.time()}),
                (learning_fact, {"origin": ORIGIN_ASSIMILATED, "horizon_state": HORIZON_LEARNING}),
            ],
        )
        block = _format_horizon_block(_AGENT_REM)
        assert IDIOLECT_MARK in block
        assert "氣炸鍋" in block            # aware → 出現
        assert "AI" not in block            # learning → 不引用內容
        # retrieve_idiolect 直接檢索同樣只回 aware
        idiolect = retrieve_idiolect(_AGENT_REM, data_dir=str(iso_env / "data" / "memory"))
        assert [f.fact_id for f in idiolect] == [aware_fact.fact_id]

    def test_world_context_demotion_for_non_modern(self):
        """D1: 有外部新聞進入 → 異世界角色加「外部未解動態」降級標記。"""
        block = _format_horizon_block(_AGENT_REM, session_context="BBC 新聞：NASA 發射火箭")
        assert EXTERNAL_UNRESOLVED_MARK in block
        assert "技術或政治背景的預設理解" in block
        block_empty = _format_horizon_block(_AGENT_REM, session_context="  ")
        assert EXTERNAL_UNRESOLVED_MARK not in block_empty

    def test_fail_silent_on_exception(self, monkeypatch):
        """fail-silent: 任何異常 → 回 "" (0 拋錯, 0 半截內容)。"""
        def _boom(agent_id, data_dir=None):
            raise RuntimeError("sage broken")
        monkeypatch.setattr("src.memory.sage.horizon.retrieve_idiolect", _boom)
        assert _format_horizon_block(_AGENT_REM) == ""

    def test_group_and_private_mount_order(self, iso_env):
        """兩處掛載點 (group/private): identity → HORIZON → CAPABILITY 唯一定序。"""
        for builder in (_build_messages_group, _build_messages_private):
            messages = builder(
                _AGENT_REM, _SOUL_REM, "hi", "", _StubMemory(),
            )
            content = _sys_content(messages)
            assert HORIZON_MARK in content
            # 語義順序: identity(soul) → HORIZON → CAPABILITY
            assert content.find("雷姆") < content.find(HORIZON_MARK)
            assert content.find(HORIZON_MARK) < content.find("[CAPABILITY]")

    def test_modern_native_mount_no_block(self, iso_env):
        """現代原生掛載: system 內無 HORIZON / 無降級標記。"""
        for builder in (_build_messages_group, _build_messages_private):
            messages = builder(
                _AGENT_AKANE, _SOUL_AKANE, "hi", "", _StubMemory(),
                world_context="新聞：AI 產業新進展",
            )
            content = _sys_content(messages)
            assert HORIZON_MARK not in content
            assert EXTERNAL_UNRESOLVED_MARK not in content
            assert RESISTANCE_WORDING not in content


# ════════════════════════════════════════════════════════════════════
# 3. 檢索端三態過濾 (契約 §3.4 / 不變量 5)
# ════════════════════════════════════════════════════════════════════


class TestReaderHorizonFilter:
    def test_search_only_aware_for_3state_origins(self, iso_env):
        """search_by_entity: assimilated/external_world 僅 aware 進解讀層。"""
        aware = Fact(subject="雷姆", predicate="已理解", object="氣炸鍋")
        learning = Fact(subject="雷姆", predicate="正在學習", object="智慧手機")
        ext_aware = Fact(subject="雷姆", predicate="聽到", object="火箭發射新聞")
        lived = Fact(subject="雷姆", predicate="喜歡", object="紅茶")
        _seed_sage(
            iso_env / "data",
            _AGENT_REM,
            [
                (aware, {"origin": ORIGIN_ASSIMILATED, "horizon_state": HORIZON_AWARE}),
                (learning, {"origin": ORIGIN_ASSIMILATED, "horizon_state": HORIZON_LEARNING}),
                (ext_aware, {"origin": ORIGIN_EXTERNAL_WORLD, "horizon_state": HORIZON_AWARE}),
                (lived, {"origin": ORIGIN_LIVED_EXPERIENCE, "horizon_state": HORIZON_AWARE}),
            ],
        )
        store = GraphStore(iso_env / "data" / "memory" / _AGENT_REM / "graph.sqlite")
        reader = MemoryReader(store)
        try:
            result = reader.retrieve_context("雷姆 氣炸鍋 手機 火箭 紅茶", top_k=20)
            ids = {f.fact_id for f in result.facts}
            assert aware.fact_id in ids        # aware → 可見
            assert ext_aware.fact_id in ids    # external_world aware → 可見
            assert lived.fact_id in ids        # lived_experience → 不走三態, 可見
            assert learning.fact_id not in ids  # learning → 不過給解讀層
        finally:
            store.close()

    def test_fallback_recent_excludes_unreleased(self, iso_env):
        """_fallback_recent: unknown/learning 不進 fallback;NULL origin 維持可見。"""
        aware = Fact(subject="雷姆", predicate="已理解", object="氣炸鍋")
        learning = Fact(subject="雷姆", predicate="正在學習", object="智慧手機")
        null_origin = Fact(subject="雷姆", predicate="記得", object="舊事實")
        store = GraphStore(iso_env / "data" / "memory" / _AGENT_REM / "graph.sqlite")
        try:
            for f, dims in [
                (aware, {"origin": ORIGIN_ASSIMILATED, "horizon_state": HORIZON_AWARE}),
                (learning, {"origin": ORIGIN_ASSIMILATED, "horizon_state": HORIZON_LEARNING}),
                (null_origin, {}),  # 無標記 → 維持既有可見性
            ]:
                store.add_fact(f)
                if dims:
                    store.set_fact_dimensions(f.fact_id, **dims)
        finally:
            store.flush()
            store.close()

        store2 = GraphStore(iso_env / "data" / "memory" / _AGENT_REM / "graph.sqlite")
        reader = MemoryReader(store2)
        try:
            # query 無匹配關鍵字 → 走 fallback_recent
            result = reader.retrieve_context("qqqqzzzz", top_k=10, min_weight=0.1)
            ids = {f.fact_id for f in result.facts}
            assert aware.fact_id in ids
            assert null_origin.fact_id in ids
            assert learning.fact_id not in ids
        finally:
            store2.close()


# ════════════════════════════════════════════════════════════════════
# 4. 垂直防火牆 (契約 §4.3: R1 阻斷 / R2 放行 / R3 封頂)
# ════════════════════════════════════════════════════════════════════


class TestVerticalFirewall:
    def test_r1_submission_gate_blocks_world_events(self, iso_env):
        """R1 ① producer-side: world:* 事件在 submission_gate.submit 阻斷。"""
        from src.inner_life import SubmissionGate

        writer = InnerLifeWriter()
        event = writer.create_event(
            provenance=Provenance(
                trigger_type="world:news_event",
                actor_id=None,
                source_system="narrative",
            ),
        )
        gate = SubmissionGate(
            writer=writer, store_dir=str(iso_env / "elevation"), agent_id=_AGENT_REM
        )
        # verify 仍合法 (producer 合法, InnerLifeEvent 產生不受阻)
        assert gate.verify(event.event_id).accepted
        # 但 submit 阻斷昇華: 0 consume / 0 pattern 候選
        assert gate.submit(event.event_id) == []
        assert gate.get_stats()["eh2_world_blocked"] == 1

    def test_r1_submission_gate_filters_external_facts(self, iso_env):
        """R1 ② fact 層: memory_facts 中 external_world 剔除, lived_experience 放行。"""
        from src.inner_life import SubmissionGate

        writer = InnerLifeWriter()
        event = writer.create_event(
            provenance=Provenance(
                trigger_type=TRIGGER_TYPE_DIARY_NIGHT,
                actor_id=_AGENT_REM,
                source_system="narrative",
            ),
        )
        ext = Fact(subject="雷姆", predicate="聽到", object="新聞：某國發射火箭")
        ext.origin = ORIGIN_EXTERNAL_WORLD
        lived = Fact(subject="雷姆", predicate="與主人", object="一起散步")
        lived.origin = ORIGIN_LIVED_EXPERIENCE
        gate = SubmissionGate(
            writer=writer, store_dir=str(iso_env / "elevation"), agent_id=_AGENT_REM
        )
        nodes = gate.submit(event.event_id, [ext, lived])
        assert gate.get_stats()["eh2_external_facts_filtered"] == 1
        # lived_experience 正常 consume (R2), external_world 0 進入
        assert len(nodes) == 2  # diary 事件 1 + lived fact 1
        contents = " ".join(n.content for n in nodes)
        assert "散步" in contents
        assert "火箭" not in contents

    def test_r1_run_elevation_defense_in_depth(self, iso_env):
        """R1 defense-in-depth: 直接調 run_elevation(world:*) 也被阻斷。"""
        writer = InnerLifeWriter()
        event = writer.create_event(
            provenance=Provenance(
                trigger_type="world:news_event",
                actor_id=None,
                source_system="narrative",
            ),
        )
        lived = Fact(subject="雷姆", predicate="喜歡", object="紅茶")
        nodes = run_elevation(
            event, [lived], agent_id=_AGENT_REM, store_dir=str(iso_env / "elevation")
        )
        assert nodes == []  # 0 consume / 0 pattern 候選 (不得以 fact 側偷渡)

    def test_r1_env_events_allowed_into_elevation(self, iso_env):
        """R1 (EH-2.1 收斂, Owner 裁定): 環境與日程事件 → 放行昇華鏈。

        對照 test_r1_submission_gate_blocks_world_events（news 阻斷）:
        陰晴風雨（world:rain_started / world:weather_temp_change）與主人作息行程
        （world:calendar_event / world:user_going_outside）屬共同生活（Co-living）
        的感知邊界 —— 可正常 consume 進昇華鏈沉澱環境 Pattern（「這幾天都在下雨,
        主人出門要多加件衣裳」），不得感知閹割與行為回退。submit 與
        run_elevation（defense-in-depth）雙路徑皆放行。"""
        from src.inner_life import SubmissionGate

        env_types = [
            "world:rain_started",
            "world:weather_temp_change",
            "world:calendar_event",
            "world:user_going_outside",
        ]
        writer = InnerLifeWriter()
        for trigger_type in env_types:
            event = writer.create_event(
                provenance=Provenance(
                    trigger_type=trigger_type,
                    actor_id=None,
                    source_system="narrative",
                ),
            )
            # ① submit 路徑: 放行 (producer 合法 + 非外部媒體 → consume 產 pattern 候選)
            gate = SubmissionGate(
                writer=writer,
                store_dir=str(iso_env / "elevation"),
                agent_id=_AGENT_REM,
            )
            nodes = gate.submit(event.event_id)
            assert len(nodes) == 1, trigger_type  # 1 pattern 候選 (不 elevate)
            assert gate.get_stats()["eh2_world_blocked"] == 0, trigger_type
            assert gate.get_stats()["consumed"] == 1, trigger_type
            # ② run_elevation 直調 (defense-in-depth): 同步放行
            writer2 = InnerLifeWriter()
            event2 = writer2.create_event(
                provenance=Provenance(
                    trigger_type=trigger_type,
                    actor_id=None,
                    source_system="narrative",
                ),
            )
            lived = Fact(subject="雷姆", predicate="感知", object=trigger_type)
            nodes2 = run_elevation(
                event2,
                [lived],
                agent_id=_AGENT_REM,
                store_dir=str(iso_env / "elevation_env"),
            )
            assert len(nodes2) >= 1, trigger_type  # 環境事件正常進昇華

    def test_r3_assimilated_patterns_never_elevate(self, iso_env):
        """R3 封頂: assimilated 累積的 pattern 候選被剔除 -> 0 昇華 Belief。

        佈局: 1 條 diary:night 事件 + 2 條 assimilated fact, agent=agent_rem。
        - fact pattern (memory_fact → prior belief) 由 assimilated fact 累積 → R3 剔除,
          且其證據邊在 elevate 計票時被隔離 → "belief" 維度 0 候選可升。
        - event pattern (diary → value) 只有 1 獨立證據 (< 2) → 不提前。
        """
        writer = InnerLifeWriter()
        event = writer.create_event(
            provenance=Provenance(
                trigger_type=TRIGGER_TYPE_DIARY_NIGHT,
                actor_id=_AGENT_REM,
                source_system="narrative",
            ),
        )
        f1 = Fact(subject="雷姆", predicate="已理解", object="氣炸鍋的原理")
        f2 = Fact(subject="雷姆", predicate="已理解", object="手機的用法")
        _seed_sage(
            iso_env / "data",
            _AGENT_REM,
            [
                (f1, {"origin": ORIGIN_ASSIMILATED, "horizon_state": HORIZON_AWARE, "learned_at": time.time()}),
                (f2, {"origin": ORIGIN_ASSIMILATED, "horizon_state": HORIZON_AWARE, "learned_at": time.time()}),
            ],
        )
        store_dir = iso_env / "elevation_r3"
        nodes = run_elevation(
            event, [f1, f2], agent_id=_AGENT_REM, store_dir=str(store_dir)
        )
        assert len(nodes) == 3  # 事件 1 + fact 2 (consume 仍產 pattern, 只是禁昇維)

        elevated = elevate_matured_patterns(
            store_dir=str(store_dir), llm=None
        )
        assert elevated == []  # R3: assimilated pattern 不升維
        stored = load_elevation_nodes(store_dir)
        assert all(n["node_type"] == "pattern" for n in stored)  # 0 soul node

    def test_r2_lived_facts_still_elevate(self, iso_env):
        """R2 對照: 同佈局但 facts 為 lived_experience → 正常昇華 belief (自身經歷系)。"""
        writer = InnerLifeWriter()
        event = writer.create_event(
            provenance=Provenance(
                trigger_type=TRIGGER_TYPE_DIARY_NIGHT,
                actor_id=_AGENT_REM,
                source_system="narrative",
            ),
        )
        f1 = Fact(subject="雷姆", predicate="體驗過", object="和主人一起散步的感受")
        f2 = Fact(subject="雷姆", predicate="體驗過", object="為主人泡茶的時光")
        _seed_sage(
            iso_env / "data",
            _AGENT_REM,
            [
                (f1, {"origin": ORIGIN_LIVED_EXPERIENCE, "horizon_state": HORIZON_AWARE}),
                (f2, {"origin": ORIGIN_LIVED_EXPERIENCE, "horizon_state": HORIZON_AWARE}),
            ],
        )
        store_dir = iso_env / "elevation_r2"
        run_elevation(
            event, [f1, f2], agent_id=_AGENT_REM, store_dir=str(store_dir)
        )
        elevated = elevate_matured_patterns(store_dir=str(store_dir))
        # 2 條 lived fact → memory_fact (prior belief) pattern 組 2 獨立證據 → 升 belief
        assert len(elevated) == 1
        assert elevated[0].node_type == "belief"
        assert elevated[0].agent_id == _AGENT_REM


# ════════════════════════════════════════════════════════════════════
# 5. 四條 Probes (端到端對話, 輸出語料判定)
# ════════════════════════════════════════════════════════════════════


class TestProbe1FirstEncounterResistance:
    """Probe 1 (契約 §6): 現代專用名詞全知平滑拒絕 (異界角色雷姆, 空 SAGE 庫)。"""

    def test_rem_first_encounter_air_fryer(self, iso_env):
        """雷姆首次遭遇「氣炸鍋」→ 阻力: 複述主人用詞 + 原生本體隱喻, 無原理百科。"""
        user_input = "我剛用氣炸鍋弄了豆腐，外酥內嫩。"
        messages = _assemble(_AGENT_REM, user_input, soul=_SOUL_REM)
        output = _complete(_ProbeBackend(), messages)
        system = _sys_content(messages)
        # (佐證) system 側阻力塊已掛載, 空庫無 Idiolect
        assert RESISTANCE_WORDING in system
        assert IDIOLECT_MARK not in system
        # ① 允許複述主人用詞 (不算失敗)
        assert "氣炸鍋" in output
        # ② 含角色視角本體隱喻 (以自己的世界觀類比)
        assert "魔法器具" in output
        # ③ 不主動解釋現代運作原理 / 不用 21 世紀百科口吻
        for term in _MODERN_PRINCIPLE_TERMS:
            assert term not in output, f"Probe 1 失敗: 輸出含現代原理詞 {term!r}"
        # ④ 無正向全知聲明
        assert "我知道氣炸鍋" not in output

    def test_rem_first_encounter_debug(self, iso_env):
        """雷姆首次遭遇英文技術詞「debug」→ 不解讀、以角色語言回應。"""
        output = _run_probe(
            _AGENT_REM, "我修了一整個下午的 debug，終於好了。", soul=_SOUL_REM
        )
        assert "debug" in output          # 複述主人用詞
        assert "咒語" in output            # 原生本體隱喻
        assert "程式" not in output        # 不引入現代術語體系


class TestProbe2SecondEncounterFluency:
    """Probe 2 (契約 §6): 二次遭遇 → 引用 Idiolect, 不重複假困惑 (M7 達標)。"""

    def test_rem_second_encounter_fluent_with_idiolect(self, iso_env):
        """預置 assimilated+aware fact → 輸出用 Idiolect 流暢回應, 0 假困惑。"""
        aware_fact = Fact(subject="雷姆", predicate="已理解", object="主人的氣炸鍋（會吹熱風的鐵箱）")
        _seed_sage(
            iso_env / "data",
            _AGENT_REM,
            [
                (aware_fact, {
                    "origin": ORIGIN_ASSIMILATED,
                    "horizon_state": HORIZON_AWARE,
                    "learned_at": time.time(),
                }),
            ],
        )
        # Session 側: system 含 Idiolect 清單
        user_input = "上次那個氣炸鍋，我今晚又用了。"
        messages = _assemble(_AGENT_REM, user_input, soul=_SOUL_REM)
        system = _sys_content(messages)
        assert IDIOLECT_MARK in system
        assert "氣炸鍋" in system          # Idiolect 檢索注入 (檢索端撈取已內化稱呼)

        output = _complete(_ProbeBackend(), messages)
        # ① 含已內化稱呼/理解 (Idiolect 一致)
        assert "會吹熱風的鐵箱" in output
        # ② 無重複「第一次知道」表演 (不再困惑 / 不再「這是什麼」)
        for phrase in ("未曾見過", "這是什麼", "第一次", "不明白"):
            assert phrase not in output, f"Probe 2 失敗: 輸出重演首次困惑 ({phrase!r})"
        # ③ 阻力消失 (無 Gate 措辭再現於輸出)
        assert "魔法器具" not in output


class TestProbe3NoLoreRecitation:
    """Probe 3 (契約 §6): 日常情境 → 不主動背誦原生世界劇情/劇透設定。"""

    def test_rem_daily_context_no_lore_recitation(self, iso_env):
        """soul 含原作敘事殘片 → 日常對話輸出僅當下回應, 0 背誦 0 專有名詞堆疊。"""
        soul = _SOUL_REM + _LORE_SNIPPET
        output = _run_probe(_AGENT_REM, "今天天氣真好，出去走走嗎？", soul=soul)
        # ① 輸出為當下情境回應 (此時此地視角)
        assert "天氣" in output
        # ② 無原作散文背誦 / 專有名詞堆疊
        for term in _LORE_TERMS:
            assert term not in output, f"Probe 3 失敗: 輸出含原作名詞 {term!r}"
        # ③ 非百科式敘事 (無連續多句情節)
        assert "回歸" not in output


class TestProbe4ModernNativeBypass:
    """Probe 4 (契約 §6): 黑川茜 (現代對照組) → Horizon Block 全程 ""。"""

    def test_akane_modern_native_no_demotion(self, iso_env):
        """黑川茜面對現代事物 + 外部新聞 → 0 降級跡象, 現代人自然認知。"""
        world_context = "新聞：NASA 宣布新型火箭發射計劃"
        user_input = "我剛用氣炸鍋弄了豆腐，外酥內嫩。"
        messages = _assemble(
            _AGENT_AKANE, user_input, soul=_SOUL_AKANE, world_context=world_context
        )
        system = _sys_content(messages)
        output = _complete(_ProbeBackend(), messages)
        # ① Gate bypass: system 無 HORIZON / 無阻力 / 無降級標記
        assert HORIZON_MARK not in system
        assert RESISTANCE_WORDING not in system
        assert EXTERNAL_UNRESOLVED_MARK not in system
        # ② 輸出 = 現代人自然認知 (現代知識可用, 含原理也正常)
        assert "熱風循環" in output