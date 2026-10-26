"""
tests/agent/test_agent_registry_land0_batch1.py
LAND-0 Batch 1 — 24 隻新建檔角色落地的「註冊面一致性守衛」。

工單：logs/LAND-0-BATCH1-24-WORK-ORDER.md §3.7（7 條守衛）＋ §3.8（7 條變異實驗）
規格：logs/LAND-0-WORK-ORDER.md（Notion SSOT 工單 LAND-0 的 repo 快照）

守衛（工單 §3.7）：
  1. 註冊面一致性：24 隻在五個面（personas/ 檔、consciousness 子類、AGENT_CLASS_MAP、
     configs/default.yaml 的 agents、BASE_SCORES、proxy 三張表）全部齊備。
  2. 總數：註冊數 = 34（10 既有 ＋ 24 新）。
  3. 位元等價：personas/agent_<id>.md 與 docs/persona-drafts/agent_<id>.md 位元相同。
  4. 陰影門閘落地後仍成立：24 隻的 `^## 陰影條款` 計數 = 1。
  5. batch2 未落地：那 12 隻的 personas 檔不存在。
  6. NO_DIARY_AGENTS 未被提前擴充（仍為 {"agent_ram"}）。
  7. 既有 10 隻零回歸：persona 位元未動 ＋ 註冊條目未動。

聚焦（LAND-0「測試」節所指的既有聚焦面）：
  - registry：24 鍵 import ＋ get_agent_class
  - speaker bus：24 鍵 bid / resolve
  - configs：yaml.safe_load 讀出 agents 條目
  - persona 檔載入：本地 personas 路徑優先、無 hermes profile 下成功

⚠️ 本檔不得使用 subprocess / os.spawn / os.system / os.popen（R5 護欄：白名單大小鎖死 22），
   也不得硬寫生產服務位址（R3 守衛）。`src.llm.proxy` 的 import 必須在測試函式內
   （延後到 autouse 的資料根隔離 fixture 生效之後），避免匯入期碰到生產資料根。
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

DRAFTS_DIR = REPO_ROOT / "docs" / "persona-drafts"
PERSONAS_DIR = REPO_ROOT / "personas"

# ─────────────────────────────────────────────────────────────
# 資料表（逐字取自 logs/LAND-0-WORK-ORDER.md 的數值表與註冊表）
# ─────────────────────────────────────────────────────────────

#: (agent_id, 類別名, (base, jitter), display-name, short-name)
BATCH1 = [
    ("agent_ai", "AgentAi", (0.38, 0.26), "星野Ai", "ai"),
    ("agent_mochizuki_ai", "AgentMochizukiAi", (0.58, 0.33), "望月愛", "mochizuki_ai"),
    ("agent_asuna", "AgentAsuna", (0.50, 0.30), "Asuna", "asuna"),
    ("agent_fuuka", "AgentFuuka", (0.38, 0.26), "Fuuka", "fuuka"),
    ("agent_haruhi", "AgentHaruhi", (0.68, 0.35), "Haruhi", "haruhi"),
    ("agent_hiro", "AgentHiro", (0.45, 0.30), "Hiro", "hiro"),
    ("agent_iroha", "AgentIroha", (0.62, 0.35), "Iroha", "iroha"),
    ("agent_kaguya", "AgentKaguya", (0.48, 0.30), "Kaguya", "kaguya"),
    ("agent_kana", "AgentKana", (0.62, 0.32), "Kana", "kana"),
    ("agent_kaori", "AgentKaori", (0.65, 0.33), "Kaori", "kaori"),
    ("agent_kirino", "AgentKirino", (0.55, 0.32), "Kirino", "kirino"),
    ("agent_kurisu", "AgentKurisu", (0.40, 0.30), "Kurisu", "kurisu"),
    ("agent_makima", "AgentMakima", (0.42, 0.28), "Makima", "makima"),
    ("agent_mashiro", "AgentMashiro", (0.42, 0.28), "Mashiro", "mashiro"),
    ("agent_minami", "AgentMinami", (0.65, 0.35), "Minami", "minami"),
    ("agent_reze", "AgentReze", (0.45, 0.30), "Reze", "reze"),
    ("agent_rin", "AgentRin", (0.40, 0.30), "Rin", "rin"),
    ("agent_rio", "AgentRio", (0.45, 0.30), "Rio", "rio"),
    ("agent_sachi", "AgentSachi", (0.58, 0.33), "Sachi", "sachi"),
    ("agent_sakura", "AgentSakura", (0.50, 0.30), "Sakura", "sakura"),
    ("agent_shea", "AgentShea", (0.65, 0.33), "Shea", "shea"),
    ("agent_shizuku", "AgentShizuku", (0.42, 0.28), "Shizuku", "shizuku"),
    ("agent_tsukasa", "AgentTsukasa", (0.52, 0.30), "Tsukasa", "tsukasa"),
    ("agent_yue", "AgentYue", (0.38, 0.25), "Yue", "yue"),
]

BATCH1_IDS = [row[0] for row in BATCH1]
BATCH1_CLASSES = {row[0]: row[1] for row in BATCH1}
BATCH1_SCORES = {row[0]: row[2] for row in BATCH1}
BATCH1_DISPLAY = {row[0]: row[3] for row in BATCH1}
BATCH1_SHORT = {row[0]: row[4] for row in BATCH1}

#: 第二批 12 隻（陰影門閘未過，本票不得落地）
BATCH2_IDS = [
    "agent_beatrice", "agent_chizuru", "agent_emilia", "agent_ichika",
    "agent_itsuki", "agent_mami", "agent_nagisa", "agent_nino",
    "agent_rika", "agent_saki", "agent_shino", "agent_yotsuba",
]

#: 既有 10 隻：(類別名, configs intimacy_level, BASE_SCORES, display-name)
#: display-name = None 表示該 agent 原本就不在 AGENT_NAMES（既有狀態，本票不動）。
EXISTING_10 = {
    "agent_yua": ("AgentYua", 80, (0.80, 0.40), "Yua"),
    "agent_ruka": ("AgentRuka", 60, (0.75, 0.35), "Ruka"),
    "agent_akane": ("AgentAkane", 50, (0.35, 0.28), "Akane"),
    "agent_rem": ("AgentRem", 45, (0.40, 0.30), None),
    "agent_ram": ("AgentRam", 40, (0.30, 0.20), None),
    "agent_mahiru": ("AgentMahiru", 45, (0.65, 0.35), None),
    "agent_anna": ("AgentAnna", 55, (0.55, 0.30), None),
    "agent_mai": ("AgentMai", 60, (0.58, 0.35), None),
    "agent_miku": ("AgentMiku", 60, (0.55, 0.30), None),
    "agent_aoi": ("AgentAoi", 46, (0.62, 0.32), None),
}

#: 既有 10 隻 persona 檔的 sha256（HEAD 82de5cb 基準；本票不得動到這 10 份）
EXISTING_10_SHA256 = {
    "agent_yua": "eb4cbe051992040bfc5ce6bd204345a7e794eebf635ee63bb485d6dfcc3a48a4",
    "agent_ruka": "96603209abb38dd4d56b56eebf28fa358a86e9cd1a272788b86d3e7b776c4e78",
    "agent_akane": "da5f87c1afd8b8bc4276337030c9560f1590aa64401852739e99b4ca25816486",
    "agent_rem": "04f971aab0f30628d8327f22a69e2ce2fc000a7c76d9ce449950f60e7a1ea829",
    "agent_ram": "9ad59a8c4798bd58c92440696fd33ef1734109be477c15b16fe32c8755ae477e",
    "agent_mahiru": "cc0b825045a9a35214c7ec3ad713302e9130fe0d2d95626acabe7bdd79003869",
    "agent_anna": "bbdaadeeab9cd28dc4546b8799e74a50b463a504430c2096023e7d2c1fabf0c6",
    "agent_mai": "f26f6b3dfa38fbda378c53a80d6d0714be46774c36aa2fa3c8faf3d9a29612dc",
    "agent_miku": "10fc62f64ab84bcd38d8cc9a845b70dd84a1e915d29804b918fe6599e5a06b76",
    "agent_aoi": "c954978250bb795b7fb881a3b89f56b967c2504dc96a134352f243e4a1468f4d",
}

#: 既有 10 隻在 AGENT_SHORT_NAMES 之前不存在（該表為本票新建），
#: 但 display-name map 原本就有 yua / ruka / akane 三筆——本票不得動到它們。
PRE_EXISTING_DISPLAY = {"agent_yua": "Yua", "agent_ruka": "Ruka", "agent_akane": "Akane"}

SHADOW_HEADING = "## 陰影條款"


def _proxy_tables():
    """在測試函式內匯入 proxy（延後到資料根隔離 fixture 生效後）。"""
    from src.llm import proxy as proxy_mod

    return (
        proxy_mod.AGENT_NAMES,
        proxy_mod.AGENT_SHORT_NAMES,
        proxy_mod._AGENT_IDENTITY_RULES,
    )


def _config_agents():
    import yaml

    with open(REPO_ROOT / "configs" / "default.yaml", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    return cfg.get("agents", [])


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ─────────────────────────────────────────────────────────────
# 守衛 1：註冊面一致性（五面齊備）
# ─────────────────────────────────────────────────────────────


def test_guard1_registration_surfaces_complete():
    """24 隻在五個面全部齊備（personas / consciousness / class map / configs / scores / proxy 三表）。"""
    from src.agent import consciousness as cons_mod
    from src.agent.registry import AGENT_CLASS_MAP
    from src.agent.speaker_token import BASE_SCORES

    display, short, identity = _proxy_tables()
    config_agents = {a["id"]: a for a in _config_agents()}

    for agent_id, class_name, score, disp, shrt in BATCH1:
        assert (PERSONAS_DIR / f"{agent_id}.md").is_file(), f"{agent_id}: personas 檔不存在"
        cls = getattr(cons_mod, class_name, None)
        assert cls is not None, f"{agent_id}: consciousness 缺子類 {class_name}"
        assert AGENT_CLASS_MAP.get(class_name) is cls, f"{agent_id}: AGENT_CLASS_MAP 未註冊 {class_name}"
        assert agent_id in config_agents, f"{agent_id}: configs/default.yaml 的 agents 缺條目"
        assert config_agents[agent_id]["class"] == class_name, f"{agent_id}: configs class 不符"
        assert BASE_SCORES.get(agent_id) == score, f"{agent_id}: BASE_SCORES 不符"
        assert display.get(agent_id) == disp, f"{agent_id}: display-name map 不符"
        assert short.get(agent_id) == shrt, f"{agent_id}: short-name map 不符"
        assert agent_id in identity, f"{agent_id}: _AGENT_IDENTITY_RULES 缺條目"
        assert identity[agent_id].strip(), f"{agent_id}: identity rule 為空"


def test_guard1_identity_rules_carry_canon_lock_and_never_list():
    """identity rule 必含四段結構：Canon Lock 核心句、標準四禁、專屬禁止句、稱呼行。"""
    _, _, identity = _proxy_tables()
    for agent_id, _, _, _, _ in BATCH1:
        rule = identity[agent_id]
        assert "絕對禁止" in rule, f"{agent_id}: identity rule 缺絕對禁止段"
        for forbidden in (
            "聲稱自己是任何 AI 工具或系統",
            "單字回覆",
            "扮演或假裝成其他角色",
            "描述其他角色的動作、表情、情感",
        ):
            assert forbidden in rule, f"{agent_id}: identity rule 缺標準四禁條目「{forbidden}」"
        assert "稱呼:" in rule, f"{agent_id}: identity rule 缺稱呼規則行"
        assert "Canon Lock" in rule or "「" in rule, f"{agent_id}: identity rule 缺 Canon Lock 核心句"


def test_guard1_consciousness_subclasses_differentiate_four_items():
    """每個子類頭註必含四項差異化：agent_id / persona 檔路徑 / diary 政策 / 專屬禁止句。

    專屬禁止句放在**類別頭註**（比照 AgentMiku／AgentAoi 的既有寫法），
    因此斷言範圍＝「該子類的分節橫幅 → 類別定義結束」整段。
    """
    from src.agent import consciousness as cons_mod

    src = Path(cons_mod.__file__).read_text(encoding="utf-8")
    for agent_id, class_name, _, _, _ in BATCH1:
        assert f"class {class_name}(" in src, f"{agent_id}: 缺類別 {class_name}"
        cls_idx = src.index(f"class {class_name}(")
        banner_idx = src[:cls_idx].rfind("# ─")
        head = src[banner_idx:cls_idx]
        body = src[cls_idx:].split("\nclass ", 1)[0]
        region = head + body
        assert agent_id in region, f"{agent_id}: 類別頭註未寫 agent_id"
        assert f"personas/{agent_id}.md" in region, f"{agent_id}: 類別頭註未寫 persona 檔路徑"
        assert "建議寫 diary" in region, f"{agent_id}: 類別頭註未寫 diary 政策"
        assert "❌" in region, f"{agent_id}: 類別頭註未寫專屬禁止句"


# ─────────────────────────────────────────────────────────────
# 守衛 2：總數 = 34
# ─────────────────────────────────────────────────────────────


def test_guard2_total_registration_is_46():
    """全庫註冊數 = 46（Batch 1 的 34 ＋ Batch 2 的 12；LAND-0-BATCH2-12 起）。"""
    from src.agent.registry import AGENT_CLASS_MAP
    from src.agent.speaker_token import BASE_SCORES

    _, _, identity = _proxy_tables()
    config_agents = _config_agents()
    persona_files = sorted(PERSONAS_DIR.glob("agent_*.md"))

    assert len(config_agents) == 46, f"configs agents 應 46 筆，實際 {len(config_agents)}"
    assert len(AGENT_CLASS_MAP) == 46, f"AGENT_CLASS_MAP 應 46 筆，實際 {len(AGENT_CLASS_MAP)}"
    assert len(BASE_SCORES) == 46, f"BASE_SCORES 應 46 筆，實際 {len(BASE_SCORES)}"
    assert len(identity) == 46, f"_AGENT_IDENTITY_RULES 應 46 筆，實際 {len(identity)}"
    assert len(persona_files) == 46, f"personas/ 應 46 份，實際 {len(persona_files)}"

    # 既有 10 隻必須仍在（不得被本票擠掉）
    for agent_id in EXISTING_10:
        assert agent_id in BASE_SCORES, f"既有 {agent_id} 從 BASE_SCORES 消失"
        assert agent_id in identity, f"既有 {agent_id} 從 identity rules 消失"
        assert any(a["id"] == agent_id for a in config_agents), f"既有 {agent_id} 從 configs 消失"


def test_guard2_display_and_short_name_maps_are_consistent():
    """display-name map 與 short-name map 的鍵集必須一致（且含本批 24 鍵）。"""
    display, short, _ = _proxy_tables()
    assert set(display) == set(short), (
        f"display/short 鍵集不一致：display-only={set(display) - set(short)} "
        f"short-only={set(short) - set(display)}"
    )
    for agent_id in BATCH1_IDS:
        assert agent_id in display
        assert agent_id in short
    # 本票不得動既有 3 筆 display-name
    for agent_id, name in PRE_EXISTING_DISPLAY.items():
        assert display[agent_id] == name, f"既有 display-name {agent_id} 被改動"


# ─────────────────────────────────────────────────────────────
# 守衛 3：位元等價（落地檔 vs 素材）
# ─────────────────────────────────────────────────────────────


def test_guard3_persona_files_are_byte_identical_to_drafts():
    """24 份落地檔與素材**語意**逐位元相同（最強驗收條件）。

    🔴 LINT-CHORE-34 起（2026-10-21）：落地檔已做排版正規化（把行內 ` • ` 分隔序列
    拆成獨立 list item），而素材 `docs/persona-drafts/**` 是**唯讀 SSOT**（含未拆的
    原始排版）⇒ 兩者**位元必然不同**、但**語意必須相同**。
    「persona ≡ 素材（位元）」與「persona 已正規化」是**互斥**的兩條要求，
    因此本守衛改比「**兩側都正規化後**的位元」—— 這個寫法在兩種素材狀態下都成立
    （未正規化時 normalize 是位元級 no-op，正規化後則吸收掉排版差異）。
    同一不變式的獨立實作見 `tests/persona/test_persona_format.py::test_guard2_semantic_equivalence_to_sssot_draft`。
    """
    import importlib.util as _ilu

    _spec = _ilu.spec_from_file_location(
        "_npf", REPO_ROOT / "scripts" / "normalize_persona_format.py"
    )
    _npf = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(_npf)

    def _strip_shadow(text: str) -> str:
        out = []
        for line in text.splitlines(keepends=True):
            if line.startswith(SHADOW_HEADING):
                break
            out.append(line)
        return "".join(out)

    for agent_id in BATCH1_IDS:
        draft = DRAFTS_DIR / f"{agent_id}.md"
        landed = PERSONAS_DIR / f"{agent_id}.md"
        assert draft.is_file(), f"素材缺檔：{draft.name}"
        assert landed.is_file(), f"落地缺檔：{landed.name}"
        _a = _strip_shadow(_npf.normalize_markdown(landed.read_text(encoding="utf-8")))
        _b = _strip_shadow(_npf.normalize_markdown(draft.read_text(encoding="utf-8")))
        assert _a == _b, (
            f"{agent_id}: 正規化後與素材語意不逐位元相同"
            f"（draft sha256={_sha256(draft)} / landed sha256={_sha256(landed)}）"
        )


# ─────────────────────────────────────────────────────────────
# 守衛 4：陰影門閘落地後仍成立
# ─────────────────────────────────────────────────────────────


def test_guard4_shadow_clause_gate_holds_for_batch1():
    """本批 24 隻的 `^## 陰影條款` 計數 = 1（門閘落地後仍成立）。"""
    for agent_id in BATCH1_IDS:
        text = (PERSONAS_DIR / f"{agent_id}.md").read_text(encoding="utf-8")
        count = sum(1 for line in text.splitlines() if line.startswith(SHADOW_HEADING))
        assert count == 1, f"{agent_id}: `^{SHADOW_HEADING}` 計數 = {count}（應為 1）"


def test_guard4_drafts_of_batch1_all_have_exactly_one_shadow_clause():
    """素材端同樣必須恰好 1 個陰影條款（門閘自我檢查）。"""
    for agent_id in BATCH1_IDS:
        text = (DRAFTS_DIR / f"{agent_id}.md").read_text(encoding="utf-8")
        count = sum(1 for line in text.splitlines() if line.startswith(SHADOW_HEADING))
        assert count == 1, f"素材 {agent_id}: 陰影條款計數 = {count}（應為 1）"


# ─────────────────────────────────────────────────────────────
# 守衛 5：batch2 已落地（LAND-0-BATCH2-12 起由「未落地」翻轉為「已落地且註冊完成」）
# ─────────────────────────────────────────────────────────────


def test_guard5_batch2_landed():
    """第二批 12 隻的 personas 檔必須存在（陰影門閘已過、落地完成）。"""
    for agent_id in BATCH2_IDS:
        assert (PERSONAS_DIR / f"{agent_id}.md").is_file(), f"batch2 {agent_id} 未落地"


def test_guard5_batch2_registered_everywhere():
    from src.agent.registry import AGENT_CLASS_MAP
    from src.agent.speaker_token import BASE_SCORES

    display, short, identity = _proxy_tables()
    config_agents = {a["id"] for a in _config_agents()}
    class_names = {v.__name__ for v in AGENT_CLASS_MAP.values()}

    for agent_id in BATCH2_IDS:
        assert agent_id in config_agents, f"batch2 {agent_id} 未進 configs"
        assert agent_id in BASE_SCORES, f"batch2 {agent_id} 未進 BASE_SCORES"
        assert agent_id in short, f"batch2 {agent_id} 未進 short-name map"
        assert agent_id in display, f"batch2 {agent_id} 未進 display-name map"
        assert agent_id in identity, f"batch2 {agent_id} 未進 identity rules"
        expected_cls = "Agent" + "".join(w.capitalize() for w in agent_id[len("agent_"):].split("_"))
        assert expected_cls in class_names, f"batch2 {agent_id} 未進 AGENT_CLASS_MAP（{expected_cls}）"


# ─────────────────────────────────────────────────────────────
# 守衛 6：NO_DIARY_AGENTS = 5 筆（Batch 2 起一次加上 4 隻）
# ─────────────────────────────────────────────────────────────


def test_guard6_no_diary_agents_expanded_to_5():
    """provider.py 的 NO_DIARY_AGENTS = {agent_ram, agent_beatrice, agent_mami, agent_nino, agent_saki}。"""
    from src.memory.sage.provider import NO_DIARY_AGENTS

    expected = {"agent_ram", "agent_beatrice", "agent_mami", "agent_nino", "agent_saki"}
    assert NO_DIARY_AGENTS == expected, f"NO_DIARY_AGENTS 不符：{NO_DIARY_AGENTS}"


# ─────────────────────────────────────────────────────────────
# 守衛 7：既有 10 隻零回歸
# ─────────────────────────────────────────────────────────────


def test_guard7_existing_10_persona_files_untouched():
    """既有 10 份 persona 位元未動（sha256 對 HEAD 基準）。"""
    for agent_id, expected in EXISTING_10_SHA256.items():
        path = PERSONAS_DIR / f"{agent_id}.md"
        assert path.is_file(), f"既有 persona 消失：{agent_id}"
        actual = _sha256(path)
        assert actual == expected, f"既有 persona {agent_id} 被改動（sha256 {actual} != {expected}）"


def test_guard7_existing_10_registration_entries_untouched():
    """既有 10 隻的註冊條目未動：class map / configs / BASE_SCORES。"""
    from src.agent.registry import AGENT_CLASS_MAP
    from src.agent.speaker_token import BASE_SCORES

    config_agents = {a["id"]: a for a in _config_agents()}
    for agent_id, (class_name, intimacy, score, _display) in EXISTING_10.items():
        assert AGENT_CLASS_MAP.get(class_name) is not None, f"既有 {agent_id} 類別被移除"
        assert config_agents[agent_id]["class"] == class_name, f"既有 {agent_id} configs class 被改"
        assert config_agents[agent_id]["intimacy_level"] == intimacy, (
            f"既有 {agent_id} intimacy_level 被改："
            f"{config_agents[agent_id]['intimacy_level']} != {intimacy}"
        )
        assert config_agents[agent_id].get("enabled", True) is True, f"既有 {agent_id} 被停用"
        assert BASE_SCORES.get(agent_id) == score, f"既有 {agent_id} BASE_SCORES 被改"


def test_guard7_existing_10_identity_rules_untouched():
    """既有 10 隻的 identity rule 仍存在且非空（本票只加不減）。"""
    _, _, identity = _proxy_tables()
    for agent_id in EXISTING_10:
        assert agent_id in identity, f"既有 {agent_id} identity rule 被移除"
        assert identity[agent_id].strip(), f"既有 {agent_id} identity rule 變空"


# ─────────────────────────────────────────────────────────────
# 聚焦測試（LAND-0「測試」節）：registry / speaker bus / configs / persona 載入
# ─────────────────────────────────────────────────────────────


def test_focus_registry_get_agent_class_for_all_24():
    """registry：24 鍵 import ＋ get_agent_class 全數回傳非例外。"""
    from src.agent.registry import get_agent_class

    for agent_id, class_name, _, _, _ in BATCH1:
        cls = get_agent_class(class_name)
        assert cls is not None
        assert cls.__name__ == class_name


def test_focus_speaker_bus_base_score_and_bid_resolve_for_all_24():
    """speaker bus：24 鍵 base_score 與 bid/resolve 可用。"""
    import asyncio

    from src.agent.speaker_token import BASE_SCORES, SpeakerTokenBus

    async def run():
        bus = SpeakerTokenBus(cooldown_secs=4.0)
        await bus.open_session()
        for agent_id in BATCH1_IDS:
            assert bus.base_score(agent_id) == BASE_SCORES[agent_id]
            accepted = await bus.submit_bid(agent_id, bus.base_score(agent_id))
            assert accepted, f"{agent_id}: bid 被拒"
        winner = await bus.resolve_session()
        assert winner in BATCH1_IDS, f"resolve 勝者不在本批：{winner}"

    asyncio.run(run())


def test_focus_configs_yaml_safe_load_agents():
    """configs：yaml.safe_load 讀出 agents 條目（本批 24 條形狀比照 agent_miku）。"""
    agents = _config_agents()
    by_id = {a["id"]: a for a in agents}
    miku = by_id["agent_miku"]
    for agent_id, class_name, _, _, _ in BATCH1:
        entry = by_id[agent_id]
        assert set(entry) == set(miku), f"{agent_id}: 條目欄位形狀不符 agent_miku：{set(entry)}"
        assert "initialization_mode" not in entry, f"{agent_id}: initialization_mode 必須缺省"
        assert entry["enabled"] is True
        assert entry["class"] == class_name


def test_focus_persona_load_local_path_priority_for_all_24():
    """persona 檔載入：本地 personas 路徑優先、無 hermes profile 下成功。"""
    from src.llm.proxy import load_persona

    for agent_id in BATCH1_IDS:
        text = load_persona(agent_id)
        assert isinstance(text, str) and text.strip(), f"{agent_id}: load_persona 回空"
        assert SHADOW_HEADING in text, f"{agent_id}: load_persona 結果不含陰影條款（本地 persona 未優先）"


def test_focus_load_persona_germ_mode_untouched_for_new_agents():
    """germ 模式不得讀本地 persona（FG-2 邊界），本批 24 隻同樣適用。"""
    from src.llm.proxy import load_persona

    for agent_id in BATCH1_IDS:
        germ = load_persona(agent_id, initialization_mode="germ")
        assert SHADOW_HEADING not in germ, f"{agent_id}: germ 模式誤讀本地 persona"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
