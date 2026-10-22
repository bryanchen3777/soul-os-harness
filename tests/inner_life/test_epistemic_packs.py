"""PACK-EXPAND — L1 實例包守衛（pack 擴充票：configs/packs/*.yaml 的 schema 與覆蓋率釘死）。

本檔是「pack 對誰有用、對誰無效」這張量測表的**可執行版本**。
量測結論（logs/PACK-EXPAND-FINDINGS.md §2、§3）：

  * V4（``src/inner_life/epistemic_appraisal.py`` 的 ``civilization_base == "modern_earth"``
    ⇒ 直接回 ``None``）**只擋那一個字面值**。⇒ 誠實標成 ``modern_earth`` 的 pack
    會被載入器整包作廢 ⇒ **永遠不會有人讀** ⇒ 屬於「無人會讀的 pack」，禁止落地。
  * 核心 10 隻生產 agent 裡，**8 隻的 persona 世界觀載體是現代**（含 ``agent_mahiru``：
    公園／傘／粥／神社／聖誕／模特兒），**2 隻是非現代**（``agent_rem``、``agent_ram``：
    羅茲瓦爾公館、鬼族、魔力）。⇒ 之前流傳的「9 隻 modern」是**數量誤記**。
  * 現代基底 8 隻：``agent_mahiru`` 以外的 7 隻另有讀側 D3 白名單（``MODERN_NATIVE_AGENTS``）
    二次旁路；8 隻**全部**在 V4 下被整包作廢 ⇒ 不為它們寫 pack。
  * 非現代基底 2 隻：``agent_rem`` 已有 pack；``agent_ram`` 原本**沒有** ⇒ 本票補上
    （``configs/packs/roswaal_mansion_ram_v1.yaml``）。覆蓋率 1/10 → 2/10。

兩條「不可漂移」的環境變數（**僅供變異驅動器使用**，見 logs/PACK-EXPAND-FINDINGS.md §6）：

  ``PACK_TEST_PACK_DIR``  —— 把載入器指向**另一個** pack 目錄（變異用的 tmp 副本）。
                              生產 ``configs/packs/`` 全程唯讀。
  ``PACK_TEST_DROP_AGENT`` —— 從量測名單抽掉一隻 agent（量測守衛的變異）。

本檔 **0 production 寫入**：所有需要 SAGE／關係資料的案例都在 tmp 的
``SOUL_OS_DATA_DIR`` 內進行。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import List, Tuple

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.inner_life import epistemic_action as eact  # noqa: E402
from src.inner_life import epistemic_appraisal as ea  # noqa: E402
from src.paths import reset_data_root  # noqa: E402

#: 生產 pack 目錄（唯讀）。
PROD_PACK_DIR = REPO_ROOT / "configs" / "packs"

#: 核心 10 隻生產 agent —— ``configs/default.yaml`` 的**前 10 條** seeded agent
#: （yua/ruka/akane/rem/ram/mahiru/anna/mai/miku/aoi；第 11 條起為
#: LAND-0 Batch 1 的 24 隻新建角色，見 configs/default.yaml:93）。
CORE_AGENTS: Tuple[str, ...] = (
    "agent_yua",
    "agent_ruka",
    "agent_akane",
    "agent_rem",
    "agent_ram",
    "agent_mahiru",
    "agent_anna",
    "agent_mai",
    "agent_miku",
    "agent_aoi",
)

#: 實測判定（persona 世界觀載體）：8 隻現代原生。
#: 依據見 logs/PACK-EXPAND-FINDINGS.md §2（persona 的現代／異世界載體統計）。
MODERN_BASE_AGENTS = frozenset(
    {
        "agent_yua",
        "agent_ruka",
        "agent_akane",
        "agent_anna",
        "agent_mai",
        "agent_miku",
        "agent_aoi",
        "agent_mahiru",
    }
)

#: 實測判定：2 隻非現代（異世界）基底。
NON_MODERN_BASE_AGENTS = frozenset({"agent_rem", "agent_ram"})

#: 有 pack 的核心 agent（實測結果；覆蓋率 2/10）。
PACKED_AGENTS = frozenset({"agent_rem", "agent_ram"})


# ─────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────
@pytest.fixture(autouse=True)
def _isolate_pack_cache():
    """pack 快取是進程內 dict（契約 §1.6）⇒ 每個測試前後清空。"""
    ea.clear_pack_cache()
    yield
    ea.clear_pack_cache()


@pytest.fixture()
def pack_dir(monkeypatch) -> Path:
    """把載入器指向 ``PACK_TEST_PACK_DIR``（變異用 tmp 副本）或生產目錄。"""
    override = os.environ.get("PACK_TEST_PACK_DIR")
    target = Path(override).resolve() if override else PROD_PACK_DIR
    assert target.is_dir(), f"pack 目錄不存在：{target}"
    monkeypatch.setattr(ea, "_PACK_DIR", target)
    ea.clear_pack_cache()
    return target


@pytest.fixture()
def isolated_data_root(tmp_path, monkeypatch):
    """把 ``data_root()`` 指到 tmp ⇒ SAGE／關係資料查詢 0 命中（不碰生產）。"""
    monkeypatch.setenv("SOUL_OS_DATA_DIR", str(tmp_path))
    reset_data_root()
    ea.clear_pack_cache()
    yield tmp_path
    monkeypatch.delenv("SOUL_OS_DATA_DIR", raising=False)
    reset_data_root()


def _read_pack_files(pack_dir: Path) -> List[Tuple[Path, dict]]:
    out: List[Tuple[Path, dict]] = []
    for path in sorted(pack_dir.glob("*.yaml")):
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        out.append((path, raw if isinstance(raw, dict) else {}))
    return out


def _measured_roster() -> Tuple[str, ...]:
    """量測名單（可被 ``PACK_TEST_DROP_AGENT`` 變異掉一隻）。"""
    dropped = os.environ.get("PACK_TEST_DROP_AGENT")
    if dropped:
        return tuple(a for a in CORE_AGENTS if a != dropped)
    return CORE_AGENTS


# ─────────────────────────────────────────────────────────────────────
# 1) 載入器層：每份 pack 都能被載入（不拋例外）
# ─────────────────────────────────────────────────────────────────────
def test_every_pack_file_loads_via_loader(pack_dir):
    """每個 pack 檔都能被 ``load_epistemic_pack`` 載入，且 pack_id == 檔名 stem。"""
    files = _read_pack_files(pack_dir)
    assert files, f"pack 目錄是空的：{pack_dir}"
    for path, raw in files:
        agent_id = raw.get("agent_id")
        assert isinstance(agent_id, str) and agent_id, f"{path.name}: agent_id 缺失"
        pack = ea.load_epistemic_pack(agent_id)
        assert pack is not None, f"{path.name}: loader 回 None（載入失敗）"
        assert pack.pack_id == path.stem, f"{path.name}: pack_id != 檔名 stem"
        assert pack.agent_id == agent_id, f"{path.name}: pack.agent_id != yaml 內的 agent_id"


# ─────────────────────────────────────────────────────────────────────
# 2) schema 層：欄位名／層級／型別完全沿用既有 schema
# ─────────────────────────────────────────────────────────────────────
def test_pack_schema_conformance(pack_dir):
    """四軸封閉集合、每軸六欄 list[str]、bridge 的 V5/V7 值域皆須成立。"""
    files = _read_pack_files(pack_dir)
    assert files, f"pack 目錄是空的：{pack_dir}"
    for path, raw in files:
        assert raw.get("pack_version") == 1, f"{path.name}: pack_version != 1"
        assert raw.get("pack_id") == path.stem, f"{path.name}: pack_id != 檔名 stem"
        assert isinstance(raw.get("agent_id"), str) and raw["agent_id"], (
            f"{path.name}: agent_id 必須是非空字串"
        )
        assert isinstance(raw.get("civilization_base"), str), (
            f"{path.name}: civilization_base 必須是字串"
        )
        axes = raw.get("axes")
        assert isinstance(axes, dict), f"{path.name}: axes 必須是 dict"
        assert set(axes.keys()) == set(ea._AXIS_KEYS), (
            f"{path.name}: axes key 集合 != 四軸封閉集合 {ea._AXIS_KEYS}"
        )
        for axis_key in ea._AXIS_KEYS:
            node = axes[axis_key]
            assert isinstance(node, dict), f"{path.name}/{axis_key}: 必須是 dict"
            for field in ea._AXIS_FIELDS:
                values = node.get(field)
                assert isinstance(values, list), (
                    f"{path.name}/{axis_key}/{field}: 必須是 list"
                )
                assert all(isinstance(v, str) for v in values), (
                    f"{path.name}/{axis_key}/{field}: 元素必須都是 str"
                )
        for bridge in raw.get("lexicon_bridges") or ():
            assert bridge.get("axis") in ea._AXIS_KEYS, (
                f"{path.name}: bridge.axis 不在四軸封閉集合內"
            )
            assert bridge.get("feature") in ea._FEATURE_ALIASES, (
                f"{path.name}: bridge.feature 不在通用特徵詞表內"
            )
            requires = bridge.get("requires")
            if requires is not None:
                assert isinstance(requires, list) and requires, (
                    f"{path.name}: bridge.requires 必須是非空 list"
                )
                assert all(r in ea._KNOWN_FEATURES for r in requires), (
                    f"{path.name}: bridge.requires 含未知特徵鍵"
                )
            anchor_class = bridge.get("anchor_class")
            if anchor_class is not None:
                assert anchor_class in ea._ANCHOR_CLASSES, (
                    f"{path.name}: bridge.anchor_class 不在錨點類別封閉集合內"
                )
            assert isinstance(bridge.get("preferred_anchor"), str) and bridge[
                "preferred_anchor"
            ], f"{path.name}: bridge.preferred_anchor 必須是非空字串"


def test_pack_native_terms_are_at_least_two_chars(pack_dir):
    """單字長度防護：native_tools／native_basis 的每個詞條長度 ≥ 2。

    FSA-0 免疫閘採「詞條 ⊆ 核心名詞」子串比對 ⇒ 單字詞條會造成子字串誤傷。
    """
    for path, raw in _read_pack_files(pack_dir):
        for axis_key, node in (raw.get("axes") or {}).items():
            for field in ("native_basis", "native_tools"):
                for term in node.get(field) or ():
                    assert len(term) >= 2, (
                        f"{path.name}/{axis_key}/{field}: 單字詞條 {term!r} "
                        f"（長度 {len(term)}）會造成 FSA-0 子字串誤傷"
                    )


# ─────────────────────────────────────────────────────────────────────
# 3) V4 守衛：modern_earth 的 pack 是「無人會讀的 pack」⇒ 禁止落地
# ─────────────────────────────────────────────────────────────────────
def test_no_modern_earth_pack_is_shipped(pack_dir):
    """落地 pack 不得宣稱 ``civilization_base: modern_earth``。

    V4（``epistemic_appraisal._build_pack``）對該字面值直接回 ``None``
    ⇒ 整包作廢 ⇒ 永遠沒有人讀它。
    """
    offenders = [
        path.name
        for path, raw in _read_pack_files(pack_dir)
        if raw.get("civilization_base") == "modern_earth"
    ]
    assert offenders == [], (
        f"以下 pack 宣告 modern_earth ⇒ 會被 V4 整包作廢（無人會讀）：{offenders}"
    )


def test_v4_rejects_a_modern_earth_pack_at_the_loader(tmp_path, monkeypatch):
    """V4 行為釘死：``modern_earth`` ⇒ loader 回 None；``non_modern`` ⇒ 正常載入。"""
    src = PROD_PACK_DIR / "roswaal_mansion_ram_v1.yaml"
    raw = yaml.safe_load(src.read_text(encoding="utf-8"))

    def _write(target_dir: Path, civ: str) -> None:
        payload = dict(raw)
        payload["civilization_base"] = civ
        # V1：pack_id 必須 == 檔名 stem ⇒ 探針檔名沿用 pack_id。
        (target_dir / f"{payload['pack_id']}.yaml").write_text(
            yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8"
        )

    modern_dir = tmp_path / "modern"
    modern_dir.mkdir()
    _write(modern_dir, "modern_earth")
    monkeypatch.setattr(ea, "_PACK_DIR", modern_dir)
    ea.clear_pack_cache()
    assert ea.load_epistemic_pack("agent_ram") is None, (
        "V4 失效：modern_earth 的 pack 被載入了"
    )

    legacy_dir = tmp_path / "legacy"
    legacy_dir.mkdir()
    _write(legacy_dir, "non_modern")
    monkeypatch.setattr(ea, "_PACK_DIR", legacy_dir)
    ea.clear_pack_cache()
    assert ea.load_epistemic_pack("agent_ram") is not None, (
        "對照組失效：non_modern 的 pack 竟載入不了"
    )


# ─────────────────────────────────────────────────────────────────────
# 4) 覆蓋率守衛：誰有 pack、誰沒有
# ─────────────────────────────────────────────────────────────────────
def test_modern_base_agents_have_no_pack(pack_dir):
    """8 隻現代基底 agent 一律沒有 pack（V4 會整包作廢 ⇒ 寫了也無人會讀）。"""
    for agent_id in sorted(MODERN_BASE_AGENTS):
        assert ea.load_epistemic_pack(agent_id) is None, (
            f"{agent_id}: 現代基底卻有 pack（要嘛是 V4 作廢的死檔，要嘛是語意誤標）"
        )


def test_non_modern_base_agents_have_a_pack(pack_dir):
    """2 隻非現代基底 agent 都必須有 pack（V4 不作廢 ⇒ 真的會被讀）。"""
    for agent_id in sorted(NON_MODERN_BASE_AGENTS):
        pack = ea.load_epistemic_pack(agent_id)
        assert pack is not None, f"{agent_id}: 非現代基底卻沒有 pack（第二來源缺口）"
        assert pack.civilization_base != "modern_earth", (
            f"{agent_id}: 非現代角色不得宣告 modern_earth（會被 V4 作廢）"
        )


def test_core_roster_pack_coverage_is_exactly_two_of_ten(pack_dir):
    """量測守衛：核心 10 隻中有 pack 的恰為 2 隻（rem ＋ ram）⇒ 覆蓋率 2/10。"""
    roster = _measured_roster()
    with_pack = {a for a in roster if ea.load_epistemic_pack(a) is not None}
    assert with_pack == set(PACKED_AGENTS), (
        f"核心 {len(roster)} 隻的有 pack 集合應為 {sorted(PACKED_AGENTS)}，"
        f"實測 {sorted(with_pack)}"
    )


def test_measured_roster_matches_the_configured_core_ten():
    """量測名單 ≠ 配置名單時測試轉紅（防止表格少算一隻 agent）。

    ``configs/default.yaml`` 的**前 10 條** seeded agent ＝ 核心 10 隻
    （第 11 條起為 LAND-0 Batch 1 的 24 隻新建角色，configs/default.yaml:93）。
    """
    configured = yaml.safe_load(
        (REPO_ROOT / "configs" / "default.yaml").read_text(encoding="utf-8")
    )
    ids = [a["id"] for a in configured["agents"]]
    assert tuple(ids[:10]) == CORE_AGENTS, (
        "量測名單與 configs/default.yaml 的前 10 條不一致："
        f"config={tuple(ids[:10])} measured={CORE_AGENTS}"
    )
    assert set(_measured_roster()) == set(CORE_AGENTS), (
        f"量測名單被抽掉了 agent（變異）：{set(CORE_AGENTS) - set(_measured_roster())}"
    )


# ─────────────────────────────────────────────────────────────────────
# 5) 行為層：新 pack 真的觸發非空路徑
# ─────────────────────────────────────────────────────────────────────
def test_ram_pack_produces_a_non_empty_delta(pack_dir, isolated_data_root):
    """拉姆的 pack 必須能走完 S0~S7，產出非空錨點與非空注入字串。"""
    record = ea.appraise(
        ea.AppraisalInput(agent_id="agent_ram", utterance="主人買了一個會發熱的東西")
    )
    assert record is not None, "拉姆的 pack 竟評估不出差量（第二來源是空的）"
    assert record.anchor, "差量的錨點為空"
    assert record.entity_terms, "差量沒帶實體詞"

    delta = ea.format_epistemic_horizon_delta("agent_ram", "主人買了一個會發熱的東西")
    assert "[本體參照]" in delta, f"注入字串沒有本體參照段：{delta!r}"


def test_ram_pack_yields_an_epistemic_gap_tension(pack_dir, isolated_data_root):
    """通道 1：評估出的未閉合缺口會變成一條張力（這就是 pack 的實際消費面）。"""
    now = 1_700_000_000
    tensions = eact.collect_tensions(
        "agent_ram",
        utterance="主人買了一個會發熱的東西",
        now=now,
        last_interaction_ts=now - 3 * 24 * 3600,
    )
    kinds = [t.kind for t in tensions]
    assert eact.TENSION_KIND_EPISTEMIC_GAP in kinds, (
        f"pack 存在卻沒產生資訊缺口張力（kinds={kinds}）"
    )


def test_pack_does_not_create_a_relational_tension_without_bond(
    pack_dir, isolated_data_root
):
    """沒有羈絆證據 ⇒ 不會憑空長出關係張力（pack 不是關係張力的來源）。"""
    now = 1_700_000_000
    tensions = eact.collect_tensions(
        "agent_ram",
        utterance="主人買了一個會發熱的東西",
        now=now,
        last_interaction_ts=now - 3 * 24 * 3600,
    )
    assert eact.TENSION_KIND_RELATIONAL not in [t.kind for t in tensions], (
        "無羈絆證據卻長出關係張力"
    )


def test_existing_rem_pack_still_loads_and_triggers(pack_dir, isolated_data_root):
    """既有 pack（agent_rem）不得被本次擴充弄壞。"""
    record = ea.appraise(
        ea.AppraisalInput(agent_id="agent_rem", utterance="主人買了一個會發熱的東西")
    )
    assert record is not None, "既有 rem pack 竟評估不出差量"
    assert record.anchor


def test_pack_dir_contains_no_non_yaml_files(pack_dir):
    """pack 目錄只放 yaml（載入器只掃 *.yaml；混檔會讓人誤以為有 pack）。"""
    strays = [p.name for p in pack_dir.iterdir() if p.is_file() and p.suffix != ".yaml"]
    assert strays == [], f"pack 目錄有非 yaml 檔：{strays}"
