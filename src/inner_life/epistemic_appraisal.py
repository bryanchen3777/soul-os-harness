"""EH-4 L2 — 認知評估算子（Epistemic Appraisal Operator，實例包解耦版）。

契約依據：``docs/EH-4-EPISTEMIC-MIND-CONTRACT.md`` §1.3~§1.6 / §2.1~§2.5
（含 EH-4-AMEND-1 實體優先級與 Fail-Silent、EH-4-AMEND-2 原生免疫閘 FSA-0、
口語無介詞階梯 SL-1~SL-5、多錨點次級物理特徵分流 AC-1~AC-6、
當輪關切度量守門 IT-7~IT-10）。

算子性質（契約 §1.2 O1~O5）：
  O1 世界觀無關 —— 本模組可執行碼與模板字面 **0 世界觀名詞**；一切名詞由 L1 實例包注入。
  O2 確定性     —— 同輸入同輸出；0 隨機、0 時間依賴、0 外部 I/O（除 pack 讀取）。
  O3 0 額外 LLM —— 純結構化／字元規則；0 LLM、0 依存剖析器、0 外部 NLP 依賴
                    （契約 §2.1 L2-0 鐵律、§2.2 SL-5）。
  O4 fail-silent —— 任何異常 → 回 ``None``／``""``，不 raise、不注入半截內容。
  O5 0 新狀態   —— 本模組不持久化任何東西（read-side only；0 寫入）。

判定序（契約 §2.2 步驟表；FSA-0 依 NT-3 前置於特徵詞證據判定）::

    S0  load_epistemic_pack(agent_id) is None      → None（D-INV-2 bypass 鋼印）
    S2  實體抽取階梯（階梯 1 介詞 → 階梯 2 口語 → 階梯 3 保守放棄）
    S2a FSA-0 原生常識天然免疫閘                    → None（0 對撞）
    S1  表面物理特徵詞命中（空集合 → FSA-1(b)）      → None
    S4  軸映射（feature → axis；空 → None）
    S5  缺席判定（expect_absent / native_basis 未被提及）
    S5a 錨點類別分流（AC-1 固定優先序；無類別 → AC-3 回退）
    S6  錨點組裝 ``似{preferred_anchor}，卻無{缺席集合}``
    S7  產出 ``DeltaRecord``（唯讀、不持久化）

Phase 1 範圍註記（EH-4.1 工單）：
  - 本階段只實作「讀側感知 + 當輪姿態」。契約的 S3（內化檢查，需 SAGE 唯讀查詢）
    與 IT-4（同一實體一次性關切）屬後續階段，本模組不執行 SAGE 查詢。
  - ``acquisition`` 特徵鍵為 EH-4.1 工單 §步驟 1／Test B 明列的取得動詞路徑
    （「我買了一台 X」須回退基準錨點，證明 0 品名查表）；契約 §2.2 的通用詞表
    允許擴充且未含世界觀名詞，此擴充不違反 O1／AC-5。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger("soul_os.inner_life.epistemic_appraisal")

# ─────────────────────────────────────────────────────────────────────
# L1：實例包 schema 常數（封閉集合；0 世界觀名詞）
# ─────────────────────────────────────────────────────────────────────

_PACK_DIR = Path(__file__).resolve().parents[2] / "configs" / "packs"

#: 四軸封閉集合（契約 §1.3；不得新增第 5 軸）
_AXIS_KEYS: Tuple[str, ...] = (
    "energy_dynamics",
    "labor_domesticity",
    "safety_hazard",
    "sensory_metrics",
)

#: 每軸六欄（契約 §1.3／§1.4 V3）
_AXIS_FIELDS: Tuple[str, ...] = (
    "native_basis",
    "native_tools",
    "absence_assumptions",
    "analogy_anchors",
    "hazard_rules",
    "duty_hooks",
)

#: 錨點類別封閉集合（契約 §1.2；AC-4 不得新增第 5 類）—— 順序即 AC-1 固定優先序
_ANCHOR_CLASSES: Tuple[str, ...] = (
    "forced_air_heat",
    "enclosed_heat",
    "open_flat_heat",
    "cold_preservation",
)

_PACK_VERSION = 1


@dataclass(frozen=True)
class AxisPack:
    """單一常識軸的六欄資料（契約 §1.3）。"""

    key: str
    native_basis: Tuple[str, ...]
    native_tools: Tuple[str, ...]
    absence_assumptions: Tuple[str, ...]
    analogy_anchors: Tuple[str, ...]
    hazard_rules: Tuple[str, ...]
    duty_hooks: Tuple[str, ...]


@dataclass(frozen=True)
class Bridge:
    """通用特徵 → 本軸的映射覆寫（契約 §1.4 ``lexicon_bridges``）。"""

    feature: str
    axis: str
    expect_absent: Tuple[str, ...]
    preferred_anchor: str
    requires: Tuple[str, ...] = ()
    anchor_class: str = ""


@dataclass(frozen=True)
class InstancePack:
    """L1 實例包（純資料投影；載入時一次性組裝派生欄位）。"""

    pack_version: int
    pack_id: str
    agent_id: str
    civilization_base: str
    source_note: str
    axes: Dict[str, AxisPack]
    bridges: Tuple[Bridge, ...]
    forbidden_output_terms: Tuple[str, ...]
    #: 契約 §1.6 派生欄位：四軸 ``native_tools ∪ native_basis`` 去重保序聯集。
    #: FSA-0 免疫閘唯一的判定池（NT-1：算子不得自行拼接、不得維護器具名詞清單）。
    native_immunity_terms: Tuple[str, ...]


# ─────────────────────────────────────────────────────────────────────
# 通用表面特徵詞表（generic feature lexicon；0 世界觀名詞、0 現代器具名）
# 契約 §2.2 表 + EH-4.1 工單明列之觸發詞（氣流／轉盤／低溫／結冰…）
# ─────────────────────────────────────────────────────────────────────

#: (feature key, 觸發詞, 預設軸 or None＝次級特徵不單獨映射軸)
_FEATURE_LEXICON: Tuple[Tuple[str, Tuple[str, ...], Optional[str]], ...] = (
    ("heating", ("發熱", "發燙", "加熱", "燒熱", "好燙", "很燙", "燙", "熱", "烤", "烘"), "energy_dynamics"),
    ("lighting", ("發光", "亮", "燈", "螢幕", "顯示"), "energy_dynamics"),
    ("airflow", ("吹熱風", "吹風", "吹出", "出風", "氣流", "風"), "energy_dynamics"),
    ("self_drive", ("自己動", "自動", "運轉", "轉盤", "嗡", "震動"), "energy_dynamics"),
    ("electric", ("插電", "插座", "充電", "電池", "漏電", "電"), "energy_dynamics"),
    ("control", ("按鈕", "開關", "旋鈕", "遙控", "設定"), "labor_domesticity"),
    ("automated_chore", ("自動煮", "自動洗", "自動清", "一鍵"), "labor_domesticity"),
    ("hot_shell", ("外殼燙", "表面熱", "冒煙", "焦味", "異味", "燙傷"), "safety_hazard"),
    ("measure_display", ("幾度", "分鐘", "公斤", "數字", "定時"), "sensory_metrics"),
    ("enclosed", ("蓋", "門", "關起來", "密閉", "封閉", "箱子"), None),
    ("glass_window", ("玻璃", "透明", "看得見裡面"), None),
    ("timer", ("定時", "時間到", "計時", "幾分鐘"), "sensory_metrics"),
    ("open_flat", ("平底", "敞開", "開著", "淺盤", "平盤"), None),
    ("fast_sear", ("快炒", "煎", "炒", "一下子就好", "一下子就熟", "大火"), None),
    ("cooling", ("冰", "冷", "冷藏", "保冷"), "labor_domesticity"),
    ("preservation", ("保鮮", "放著不壞", "存起來", "冰起來"), "labor_domesticity"),
    ("fan_noise", ("風聲", "呼呼", "嗡嗡", "運轉聲"), "energy_dynamics"),
    ("utility_praise", ("很方便", "好用", "省事", "順手", "省力"), "labor_domesticity"),
    ("dysfunction", ("壞了", "不能用", "故障", "失靈", "不靈了", "不動了"), "energy_dynamics"),
    # EH-4.1 工單明列：階梯 2 S-2b 取得動詞路徑的表面證據（通用字元規則、0 世界觀名詞）
    ("acquisition", ("買了", "買到", "買", "入手", "添購", "收到", "帶回來"), "labor_domesticity"),
)

#: 注入措辭用（feature key → 中文詞；通用語彙，0 世界觀名詞）
_FEATURE_DISPLAY: Dict[str, str] = {
    "heating": "發熱",
    "lighting": "發光",
    "airflow": "吹風",
    "self_drive": "自行運轉",
    "electric": "帶電",
    "control": "按鈕與開關",
    "automated_chore": "自動完成家務",
    "hot_shell": "外殼發燙",
    "measure_display": "數字與度量",
    "enclosed": "封閉",
    "glass_window": "玻璃",
    "timer": "定時",
    "open_flat": "平底",
    "fast_sear": "快煎快炒",
    "cooling": "低溫",
    "preservation": "保鮮",
    "fan_noise": "風聲",
    "utility_praise": "很方便",
    "dysfunction": "故障",
    "acquisition": "新添之物",
}

#: bridge ``feature`` 欄位允許中文觸發詞 ↔ 通用 key 等價（契約 §1.4）
_FEATURE_ALIASES: Dict[str, str] = {}
for _key, _triggers, _axis in _FEATURE_LEXICON:
    _FEATURE_ALIASES[_key] = _key
    for _t in _triggers:
        _FEATURE_ALIASES.setdefault(_t, _key)
del _key, _triggers, _axis, _t

#: bridge ``requires`` 合法元素集合（V7）
_KNOWN_FEATURES = frozenset(k for k, _t, _a in _FEATURE_LEXICON)

#: EH-4.2 §步驟 1（UR-2 靜默邊界）：取得動詞階梯（「買了／有一台」）的**唯一**特徵鍵。
#: 該鍵只證明「主人添了新物」，**不構成任何物理特徵證據** —— 單獨命中時嚴禁
#: 觸發差量（0 錨點、0 品名回退、0 無特徵腦補 FSA-1）。
_ACQUISITION_FEATURE_KEYS = frozenset({"acquisition"})


# ─────────────────────────────────────────────────────────────────────
# 實體抽取階梯（Spoken Ingestion Ladder，契約 §2.2；0 名詞白名單）
# ─────────────────────────────────────────────────────────────────────

#: 階梯 1：P0 介詞錨定（「以」排除 所以／可以／加以／以及）
_LADDER1_PREP_RE = re.compile(r"(?:用|拿|透過|裝在|(?<![所可加])以)([^\s，。！？、；：,.!?;:]{1,16})")
_LADDER1_INSIDE_RE = re.compile(r"在([^\s，。！？、；：,.!?;:]{1,12})裡")

#: 階梯 2 S-2a：評價謂語（主語位）
_LADDER2_PRAISE_RE = re.compile(
    r"([^\s，。！？、；：,.!?;:]{1,14}?)(?:很方便|很好用|好方便|好用|省事|順手|省力|壞掉了|壞了|壞掉|不能用|故障|失靈|不靈了|不動了)"
)
#: 階梯 2 S-2b：取得動詞通用封閉集（受詞位）
_LADDER2_ACQUIRE_RE = re.compile(
    r"(?:買了|買到|買|入手了|入手|添購了|添購|收到了|收到|帶回來了|帶回來)([^\s，。！？、；：,.!?;:]{1,14})"
)

#: SL-2 代詞（通用語法封閉集；0 世界觀名詞）
_PRONOUNS = frozenset(
    ("我", "你", "他", "她", "它", "祂", "咱", "我們", "你們", "他們", "她們", "它們", "自己", "大家", "人家", "誰", "什麼")
)
#: SL-2 補充：泛用指示性占位名詞（UR-3：代詞指代一律回 None）
_GENERIC_PLACEHOLDERS = frozenset(("東西", "玩意", "玩意兒", "物品", "事物", "這樣", "那樣", "這東西", "那東西"))

#: SL-1：前置指示詞／量詞（去修飾正規化；不去語義修飾語）。
#: 長詞條在前（子句／指示詞 → 量詞 → 單字代詞），確保「那個黑箱子」只掉指示詞、
#: 保留語義修飾語「黑」。
_LEADING_STRIP = (
    "我覺得", "我想說", "我認為", "我想", "覺得", "認為", "想說",
    "那個", "這個", "那台", "這台", "那部", "這部", "那種", "這種", "那些", "這些",
    "一台", "兩台", "三台", "一把", "兩把", "一個", "兩個", "一只", "一張",
    "一件", "一部", "一支", "一條", "一組", "一套", "一款", "一種", "一具", "一座",
    "那", "這", "兩", "三", "幾", "一", "我", "你", "他", "她", "它", "咱",
)
_LEADING_MEASURE = ("台", "個", "把", "只", "張", "件", "部", "具", "支", "條", "顆", "組", "套", "款", "種", "座")

#: 階梯 1 的謂語切斷字（通用虛詞／高頻動作字；0 世界觀名詞）。
#: 只切「名詞片語之後的謂語」，不切修飾語（熱／風／吹… 皆不在集合內），
#: 且切點索引必須 ≥ 2（保留至少 2 字元的核心名詞 → 天然的單字防護）。
_CUT_CHARS = frozenset(
    "了著過弄做炒煮煎蒸炸洗擦掃拖買賣拿放擺裝拆看聽聞開關按用吃喝玩修換丟收起來去上下出進裡"
    "把被讓給和跟對從向就都也還再又很太好壞快慢是有在"
)

#: G1「在場操作」判定（IT-7；純句法、0 語義判斷 —— 契約 §8 D-11 採 (a)）
_FIRST_PERSON_TOKENS = ("我", "咱", "本人", "自己")
_OPERATION_TOKENS = (
    "用", "拿", "弄", "做", "煮", "烤", "煎", "炒", "蒸", "吃", "喝", "洗",
    "開", "關", "按", "端", "收", "試", "操作", "啟動", "放進", "弄好", "做好", "要吃了",
)


# ─────────────────────────────────────────────────────────────────────
# L1 載入器（契約 §1.6；唯一入口；fail-silent）
# ─────────────────────────────────────────────────────────────────────

_PACK_CACHE: Dict[str, Optional[InstancePack]] = {}


def clear_pack_cache() -> None:
    """清空進程內 pack 快取（測試隔離用；契約 §1.6）。"""
    _PACK_CACHE.clear()


def load_epistemic_pack(agent_id: str) -> Optional[InstancePack]:
    """載入該 agent 的 L1 實例包（契約 §1.6 唯一入口）。

    反硬編碼：**不得**含 agent 名 ↔ pack 名對照表；對照一律由 ``pack.agent_id``
    反查目錄（目錄掃描）。命中後進程內快取；失敗（V1~V7 任一）→ ``None`` +
    ``logger.debug``（fail-silent，不 raise）。
    """
    if not agent_id:
        return None
    if agent_id in _PACK_CACHE:
        return _PACK_CACHE[agent_id]
    pack = _scan_pack_dir(agent_id)
    _PACK_CACHE[agent_id] = pack
    return pack


def _scan_pack_dir(agent_id: str) -> Optional[InstancePack]:
    try:
        if not _PACK_DIR.is_dir():
            return None
        for path in sorted(_PACK_DIR.glob("*.yaml")):
            pack = _load_pack_file(path)
            if pack is not None and pack.agent_id == agent_id:
                return pack
    except Exception as exc:  # noqa: BLE001 — fail-silent
        logger.debug("[epistemic] pack 目錄掃描失敗: %s: %s", type(exc).__name__, exc)
    return None


def _load_pack_file(path: Path) -> Optional[InstancePack]:
    """讀取並驗證單一 pack 檔（V1~V7；任一致命條失敗即整包視為不存在）。"""
    try:
        import yaml  # 純資料解析；載入器只做 safe_load（D-INV-4）

        with open(path, "r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh)
    except Exception as exc:  # noqa: BLE001 — V6 fail-silent
        logger.debug("[epistemic] pack 讀取失敗 %s: %s: %s", path.name, type(exc).__name__, exc)
        return None
    return _build_pack(raw, path)


def _build_pack(raw: object, path: Path) -> Optional[InstancePack]:
    if not isinstance(raw, dict):
        return None
    # V1: pack_id == 檔名 stem；pack_version == 1
    if raw.get("pack_version") != _PACK_VERSION:
        return None
    pack_id = raw.get("pack_id")
    if not isinstance(pack_id, str) or pack_id != path.stem:
        return None
    agent_id = raw.get("agent_id")
    if not isinstance(agent_id, str) or not agent_id:
        return None
    civ = raw.get("civilization_base")
    # V4: modern_earth → 直接 None（不視為錯誤；D3 bypass 的資料側表達）
    if civ == "modern_earth":
        return None
    # V2: axes key 集合 == 四軸封閉集合（不多不少）
    axes_raw = raw.get("axes")
    if not isinstance(axes_raw, dict) or set(axes_raw.keys()) != set(_AXIS_KEYS):
        return None
    axes: Dict[str, AxisPack] = {}
    for key in _AXIS_KEYS:
        node = axes_raw.get(key)
        if not isinstance(node, dict):
            return None
        fields: Dict[str, Tuple[str, ...]] = {}
        # V3: 每軸六欄齊備且皆為 list[str]
        for fname in _AXIS_FIELDS:
            values = _as_str_list(node.get(fname))
            if values is None:
                return None
            fields[fname] = values
        axes[key] = AxisPack(
            key=key,
            native_basis=fields["native_basis"],
            native_tools=fields["native_tools"],
            absence_assumptions=fields["absence_assumptions"],
            analogy_anchors=fields["analogy_anchors"],
            hazard_rules=fields["hazard_rules"],
            duty_hooks=fields["duty_hooks"],
        )
    # V5 / V7: bridge 逐條驗證；失敗只丟棄該條（局部），不整包作廢
    bridges: List[Bridge] = []
    for item in raw.get("lexicon_bridges") or ():
        bridge = _build_bridge(item)
        if bridge is not None:
            bridges.append(bridge)
    forbidden = _as_str_list(raw.get("forbidden_output_terms")) or ()
    # §1.6 派生欄位：四軸 native_tools ∪ native_basis 去重保序聯集（一次性組裝）
    immunity: List[str] = []
    seen = set()
    for key in _AXIS_KEYS:
        axis = axes[key]
        for term in tuple(axis.native_tools) + tuple(axis.native_basis):
            if term and term not in seen:
                seen.add(term)
                immunity.append(term)
    return InstancePack(
        pack_version=_PACK_VERSION,
        pack_id=pack_id,
        agent_id=agent_id,
        civilization_base=str(civ or ""),
        source_note=str(raw.get("source_note") or ""),
        axes=axes,
        bridges=tuple(bridges),
        forbidden_output_terms=tuple(forbidden),
        native_immunity_terms=tuple(immunity),
    )


def _build_bridge(item: object) -> Optional[Bridge]:
    if not isinstance(item, dict):
        return None
    # V5: axis ∈ 四軸封閉集合
    axis = item.get("axis")
    if axis not in _AXIS_KEYS:
        return None
    feature_raw = item.get("feature")
    if not isinstance(feature_raw, str):
        return None
    feature = _FEATURE_ALIASES.get(feature_raw)
    if feature is None:
        return None
    anchor = item.get("preferred_anchor")
    if not isinstance(anchor, str) or not anchor:
        return None
    expect_absent = _as_str_list(item.get("expect_absent"))
    if expect_absent is None:
        return None
    requires_raw = item.get("requires")
    requires: Tuple[str, ...] = ()
    if requires_raw is not None:
        # V7: requires 必須為 list[str] 且元素 ∈ 通用特徵詞表
        requires = _as_str_list(requires_raw) or ()
        if not requires or any(r not in _KNOWN_FEATURES for r in requires):
            return None
    anchor_class = item.get("anchor_class")
    if anchor_class is not None:
        # V7: anchor_class 必須 ∈ 錨點類別封閉集合
        if not isinstance(anchor_class, str) or anchor_class not in _ANCHOR_CLASSES:
            return None
    else:
        anchor_class = ""
    return Bridge(
        feature=feature,
        axis=axis,
        expect_absent=expect_absent,
        preferred_anchor=anchor,
        requires=tuple(requires),
        anchor_class=str(anchor_class),
    )


def _as_str_list(value: object) -> Optional[Tuple[str, ...]]:
    if value is None:
        return ()
    if not isinstance(value, list):
        return None
    out: List[str] = []
    for item in value:
        if not isinstance(item, str):
            return None
        out.append(item)
    return tuple(out)


# ─────────────────────────────────────────────────────────────────────
# L2：差量計算 I/O（契約 §2.2）
# ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class AppraisalInput:
    """L2 輸入（契約 §2.2 Input）。"""

    agent_id: str
    utterance: str
    session_context: str = ""


@dataclass(frozen=True)
class DeltaRecord:
    """L2 輸出（契約 §2.2 Output；唯讀評估結果，不持久化、不寫 SAGE）。"""

    agent_id: str
    entity_terms: Tuple[str, ...]
    background_terms: Tuple[str, ...]
    extraction_ladder: str
    features: frozenset
    axis_hits: Tuple[str, ...]
    anchor_class: str
    absent_native_requirements: Tuple[str, ...]
    anchor: str
    hazard_suspected: bool
    duty_relevant: bool


def appraise(inp: AppraisalInput) -> Optional[DeltaRecord]:
    """認知評估主入口（契約 §2.2 S0~S7）；任何異常 → ``None``（fail-silent）。"""
    try:
        return _appraise_impl(inp)
    except Exception as exc:  # noqa: BLE001 — O4 fail-silent
        logger.debug("[epistemic] appraise 失敗: %s: %s", type(exc).__name__, exc)
        return None


def _appraise_impl(inp: AppraisalInput) -> Optional[DeltaRecord]:
    # S0: 無 pack → bypass（D-INV-2 bypass 鋼印；現代原生角色與未建包角色走此路）
    pack = load_epistemic_pack(inp.agent_id)
    if pack is None:
        return None

    utterance = str(inp.utterance or "")
    context = str(inp.session_context or "")

    # S2: 實體抽取階梯（階梯 3 → FSA-1(a)）
    entity, ladder, background = _extract_entity(utterance)
    if not entity:
        logger.debug("[epistemic] 階梯 3 保守放棄（無確信候選）")
        return None

    # S2a: FSA-0 原生常識天然免疫閘（NT-3：先於特徵詞證據判定）
    if _is_native_immune(entity, pack):
        logger.debug("[epistemic] FSA-0 原生免疫命中（0 對撞）")
        return None

    # S3: 已內化鎖定（EH-4.2 §步驟 3）——該 entity 已於 SAGE 具 aware 標記
    # （origin=assimilated + horizon_state=aware，即已同化過）→ 直接 None：
    # 二次遭遇**不再執行初次見面的差量對撞**，Horizon Block 完全改由 Idiolect
    # 清單投影已內化的心智模型與稱謂（平滑接話）。
    if _is_assimilated_entity(inp.agent_id, entity):
        logger.debug("[epistemic] S3 已內化鎖定（0 差量對撞）")
        return None

    # S1: 表面物理特徵（空集合 → FSA-1(b)）
    features = _match_features(utterance + "\n" + context)
    if not features:
        logger.debug("[epistemic] FSA-1(b) 無特徵詞證據")
        return None

    # S1a: UR-2 靜默邊界（EH-4.2 §步驟 1）——取得動詞階梯若除 acquisition 外
    # 別無任何**次級物理特徵詞**（熱風／玻璃門／平底／低溫…），嚴格 FSA-1 → None。
    # 校準理由：添購陳述只說「主人有了新物」，未經特徵描述或定義之前
    # 0 隱喻注入、0 品名回退（嚴禁無特徵腦補）。
    if ladder == "spoken_acquisition" and not (
        set(features) - _ACQUISITION_FEATURE_KEYS
    ):
        logger.debug("[epistemic] UR-2 取得句無次級物理特徵 → FSA-1")
        return None

    # S4: 軸映射
    axis_hits = _map_axes(features)
    if not axis_hits:
        return None

    # S5a: 錨點類別分流（AC-1 固定優先序）
    anchor_class = _resolve_anchor_class(features)
    bridge = _select_bridge(pack, features, anchor_class)

    # S5: 缺席判定（expect_absent 優先；無 bridge → 該軸 native_basis）
    if bridge is not None:
        absent = tuple(t for t in bridge.expect_absent if t and t not in utterance)
        if not absent:
            return None
        preferred = bridge.preferred_anchor
        if bridge.anchor_class:
            anchor_class = bridge.anchor_class
        elif anchor_class:
            anchor_class = ""
    else:
        # AC-3 退化宣告：無對應 bridge → 回退該軸 analogy_anchors[0]（非放棄）
        primary = axis_hits[0]
        axis_pack = pack.axes[primary]
        if not axis_pack.analogy_anchors:
            return None
        absent = tuple(t for t in axis_pack.native_basis if t and t not in utterance)
        if not absent:
            return None
        preferred = axis_pack.analogy_anchors[0]
        anchor_class = ""

    # S6: 錨點組裝
    anchor = "似" + preferred + "，卻無" + "、".join(absent)

    # S7: 產出唯讀評估結果
    return DeltaRecord(
        agent_id=inp.agent_id,
        entity_terms=(entity,),
        background_terms=background,
        extraction_ladder=ladder,
        features=frozenset(features),
        axis_hits=tuple(axis_hits),
        anchor_class=anchor_class,
        absent_native_requirements=absent,
        anchor=anchor,
        # §2.3 IT-7 G2 的「實質物理危害懷疑」：安全軸命中，或高溫本身
        #（契約 §2.3 IT-7 對照表第 2 列：熱湯→hazard（高溫））
        hazard_suspected=("safety_hazard" in axis_hits) or ("heating" in features),
        duty_relevant="labor_domesticity" in axis_hits,
    )


# ── 抽取 ────────────────────────────────────────────────────────────


def _extract_entity(utterance: str) -> Tuple[str, str, Tuple[str, ...]]:
    """實體抽取三階梯（契約 §2.2）。回 ``(entity_core, ladder, background)``。

    階梯 1（P0 介詞）命中時不執行階梯 2（SL-4 單一性）。
    """
    if not utterance:
        return "", "", ()

    # 階梯 1：P0 介詞錨定
    candidates: List[str] = []
    match = _LADDER1_PREP_RE.search(utterance)
    if match:
        candidates.append(match.group(1))
    inside = _LADDER1_INSIDE_RE.search(utterance)
    if inside:
        candidates.append(inside.group(1))
    for raw in candidates:
        core = _core_noun(raw)
        if core:
            return core, "preposition", _extract_background(utterance, core)

    # 階梯 2 S-2a：評價主語位
    praise = _LADDER2_PRAISE_RE.search(utterance)
    if praise:
        core = _core_noun(praise.group(1))
        if core:
            return core, "spoken_subject", _extract_background(utterance, core)

    # 階梯 2 S-2b：取得動詞受詞位
    acquire = _LADDER2_ACQUIRE_RE.search(utterance)
    if acquire:
        core = _core_noun(acquire.group(1))
        if core:
            return core, "spoken_acquisition", _extract_background(utterance, core)

    # 階梯 3：保守放棄（UR-1 Known Under-Recall by Design）
    return "", "", ()


def _core_noun(raw: str) -> str:
    """SL-1 去指示詞／量詞 → 切斷尾隨謂語 → 過濾代詞與泛用占位詞。

    順序要緊：**先去指示詞／量詞、再切謂語**（「一台〈器具〉」必須先掉「一台」，
    否則謂語切斷會在量詞處留下殘片）。切點索引 ≥ 2 → 保留至少 2 字元核心，
    天然的單字防護（禁收單字詞條的同一條防線）。
    """
    text = str(raw or "").strip()
    if not text:
        return ""
    text = _strip_leading(text)
    for idx in range(2, len(text)):
        if text[idx] in _CUT_CHARS:
            text = text[:idx]
            break
    text = _strip_leading(text)[:12]
    # SL-1（殘留量詞）：單一量詞前綴
    if len(text) > 2 and text[0] in _LEADING_MEASURE:
        text = text[1:]
    # SL-2：代詞／泛用占位詞排除（0 世界觀名詞黑名單）
    if text in _PRONOUNS or text in _GENERIC_PLACEHOLDERS:
        return ""
    if len(text) < 2:
        return ""
    return text


def _strip_leading(text: str) -> str:
    """SL-1：迴圈去除前置指示詞／量詞（保留語義修飾語；不去修飾語本身）。"""
    changed = True
    while changed:
        changed = False
        for token in _LEADING_STRIP:
            if text.startswith(token) and len(text) - len(token) >= 2:
                text = text[len(token):]
                changed = True
                break
    return text


def _extract_background(utterance: str, entity: str) -> Tuple[str, ...]:
    """P1／P2 背景降級：動作受詞／食材（FSA-2；永不作 entity_key／錨點主詞）。"""
    out: List[str] = []
    for match in re.finditer(r"(?:弄|切|煮|烤|煎|炒|蒸|吃|喝|洗|端|做)(?:了)?([^\s，。！？、；：,.!?;:]{1,6})", utterance):
        term = _core_noun(match.group(1))
        if term and term != entity and term not in out:
            out.append(term)
    return tuple(out)


def _is_native_immune(entity: str, pack: InstancePack) -> bool:
    """FSA-0：完全相等 **或** 詞條 ⊆ 核心名詞（子串）。0 模糊比對（NT-2）。"""
    for term in pack.native_immunity_terms:
        if entity == term or (term and term in entity):
            return True
    return False


#: S3（EH-4.2-FIX-1）主詞尾綴規範化用：尾隨語助詞／虛詞封閉集（純語法、0 世界觀名詞）。
#: 用途：SAGE 寫側（frozen，不在本單範圍）抽出的 ``subject`` 可能尾隨虛詞 ——
#: 例：核心詞 + 一個虛詞（``⟨核心詞⟩就``）。若以整串做比對，
#: 讀側 entity 一旦被多餘動詞／特徵詞尾隨（``⟨核心詞⟩吹熱風烤…``）即漏鎖。
_SUBJECT_TAIL_TRIM = frozenset("就是了個的了吧呢啊嘛呀喔哦啦而且也都很太於在有")


def _subject_core(subject: str) -> str:
    """S3 規範化：剝除尾隨語助詞／虛詞，取得核心詞（0 世界觀名詞、0 詞表）。

    保留至少 2 字元（與 ``_core_noun`` 的單字防護同一條防線）——
    單字殘片不得作為實體鍵，否則會與任意同字開頭的實體誤撞。
    """
    text = str(subject or "").strip()
    while len(text) > 2 and text[-1] in _SUBJECT_TAIL_TRIM:
        text = text[:-1]
    return text


def _assimilated_subject_match(entity: str, subject: str) -> bool:
    """S3 實體同一性判定（EH-4.2-FIX-1：對 entity 過度捕獲免疫）。

    三層判準（短路序；全部為純字元規則，0 LLM、0 NLP 依賴、0 世界觀名詞）：

    ① 原判準（backward compatible）—— 完全相等或雙向子串；
    ② **規範化核心詞方向性包含** —— ``_subject_core(subject)`` 與 entity 任一方向
       包含即視為同一實體。這一層是本單的結構保證：entity 被多餘動詞／特徵詞
       尾隨時（``⟨核心詞⟩＋謂語殘串``），核心詞仍 ⊆ entity → 命中，
       不再依賴「尾隨字恰好落在 ``_CUT_CHARS``」的運氣；
    ③ 最長共同前綴 —— 共同前綴覆蓋較短一方全長（≥2 字）即同源，
       吸收 entity 被**過度截短**（核心詞只剩前 2 字）的反向情形。

    誤殺防護：核心詞長度 ≥2，且判準皆要求整段核心詞（或其前綴）與 entity 對齊，
    未同化的實體與已同化核心詞 0 交集 → 仍正常產生差量。
    """
    if not entity or not subject:
        return False
    if entity == subject or entity in subject or subject in entity:
        return True
    core = _subject_core(subject)
    if len(core) < 2:
        return False
    if entity == core or entity in core or core in entity:
        return True
    common = 0
    for a, b in zip(entity, core):
        if a != b:
            break
        common += 1
    return common >= 2 and common >= min(len(core), len(entity))


def _is_assimilated_entity(agent_id: str, entity: str) -> bool:
    """S3（EH-4.2）：entity 是否已於 SAGE 內化（``assimilated`` + ``aware``）。

    唯一判準沿用既有讀側 Idiolect 檢索（``retrieve_idiolect``，契約 §3.5/§4.3）：
    實體鍵與已內化列的 ``subject`` 同一（判定細節見 ``_assimilated_subject_match``；
    EH-4.2-FIX-1 起為三層判準，對 entity 過度捕獲免疫）即視為已同化。

    fail-silent（O4）：SAGE 不存在／查詢失敗／無 agent_id → ``False``
    （退回初次遭遇語義，絕不因讀側失敗而吞掉正當差量）。
    """
    if not agent_id or not entity:
        return False
    try:
        from src.memory.sage.horizon import retrieve_idiolect

        for fact in retrieve_idiolect(agent_id):
            subject = str(getattr(fact, "subject", "") or "")
            if not subject:
                continue
            if _assimilated_subject_match(entity, subject):
                return True
    except Exception as exc:  # noqa: BLE001 — O4 fail-silent
        logger.debug("[epistemic] S3 內化查詢失敗: %s: %s", type(exc).__name__, exc)
    return False


def _match_features(text: str) -> Tuple[str, ...]:
    """通用特徵詞命中（命中即集合成員，無權重、無打分）。"""
    hits: List[str] = []
    for key, triggers, _axis in _FEATURE_LEXICON:
        for trigger in triggers:
            if trigger in text:
                hits.append(key)
                break
    return tuple(hits)


def _map_axes(features: Sequence[str]) -> List[str]:
    """S4：feature → axis（次級特徵不單獨映射軸；軸集合封閉）。"""
    hit_set = set(features)
    axes: List[str] = []
    for key, _triggers, axis in _FEATURE_LEXICON:
        if key in hit_set and axis and axis not in axes:
            axes.append(axis)
    return axes


def _resolve_anchor_class(features: Sequence[str]) -> str:
    """S5a：次級物理特徵分流矩陣（AC-1 固定優先序；0 打分、0 品名查表）。"""
    f = set(features)
    if "heating" in f and ({"airflow", "fan_noise"} & f):
        return "forced_air_heat"
    if "heating" in f and "enclosed" in f and ({"glass_window", "timer"} & f):
        return "enclosed_heat"
    if "heating" in f and "open_flat" in f and "fast_sear" in f:
        return "open_flat_heat"
    if "cooling" in f and "preservation" in f:
        return "cold_preservation"
    return ""


def _select_bridge(pack: InstancePack, features: Sequence[str], anchor_class: str) -> Optional[Bridge]:
    """AC-6 bridge 定序：① requires 全命中者優先 ② AC-1 類別序 ③ pack 出現序。

    無 ``requires`` 的 bridge 為**回退條款**：僅在無任何特定 bridge 命中時採用。
    """
    f = set(features)
    if anchor_class:
        for bridge in pack.bridges:
            if bridge.anchor_class == anchor_class and bridge.feature in f:
                return bridge
        return None
    specific = [b for b in pack.bridges if b.requires and b.feature in f and set(b.requires) <= f]
    if specific:
        order = {cls: idx for idx, cls in enumerate(_ANCHOR_CLASSES)}
        specific.sort(key=lambda b: order.get(b.anchor_class, len(_ANCHOR_CLASSES)))
        return specific[0]
    for bridge in pack.bridges:
        if not bridge.requires and bridge.feature in f:
            return bridge
    return None


# ─────────────────────────────────────────────────────────────────────
# 輸出注入（契約 §2.3；模板字面 0 世界觀名詞 —— D-INV-1／O1）
# ─────────────────────────────────────────────────────────────────────

_TEMPLATE_ANCHOR = (
    "[本體參照]\n"
    "你此刻感知到：{features}\n"
    "以你熟悉的世界來比擬：{anchor}\n"
    "只以這個比擬去理解它。不要解說它的運作原理，也不要只說自己不懂——\n"
    "用你熟悉之物的樣子去描述它。"
)

_TEMPLATE_CONCERN = (
    "[當下的關切]\n"
    "以你熟悉的世界比擬之餘，出於職責自然探詢一句：這物是否安全、我該如何協助。\n"
    "一句為度，是此刻的關切，不是連環盤問；主人若未答，也不追問。"
)


def is_modern_native_agent(agent_id: str) -> bool:
    """讀側現代原生分流（沿用既有白名單；不與 L1 資料側 bypass 合併）。

    契約 §1.6 註記：白名單（讀側 D3）與 ``civilization_base``（資料側 L1）
    是兩個**獨立** bypass；此處僅用於「0 差量運算」前置短路（§8 D-4 採 (a)），
    不參與 pack 對照（pack 對照一律走 ``load_epistemic_pack`` 的目錄反查）。
    """
    try:
        from src.memory.sage.horizon import is_modern_native

        return bool(is_modern_native(agent_id))
    except Exception:  # noqa: BLE001 — fail-silent：白名單不可得時不短路（改由 pack 缺失兜底）
        return False


def format_epistemic_horizon_delta(agent_id: str, utterance: str) -> str:
    """產生注入 Horizon Block 的 L2 差量字串（契約 §2.3）。

    回傳空字串＝不注入（FSA-3 退回通用平滑對話）。現代原生角色前置短路，
    **0 差量運算**（S8／§8 D-4）。
    """
    try:
        if is_modern_native_agent(agent_id):
            return ""
        record = appraise(AppraisalInput(agent_id=agent_id, utterance=utterance))
        if record is None:
            return ""
        parts = [
            _TEMPLATE_ANCHOR.format(
                features="、".join(_display_features(record.features)),
                anchor=record.anchor,
            )
        ]
        if _concern_gate(record, str(utterance or "")):
            parts.append(_TEMPLATE_CONCERN)
        return "\n".join(parts)
    except Exception as exc:  # noqa: BLE001 — O4 fail-silent
        logger.debug("[epistemic] 差量注入失敗: %s: %s", type(exc).__name__, exc)
        return ""


def _display_features(features: Sequence[str]) -> List[str]:
    """依通用詞表固定序輸出特徵中文詞（確定性；0 隨機）。"""
    hit = set(features)
    return [_FEATURE_DISPLAY[k] for k, _t, _a in _FEATURE_LEXICON if k in hit and k in _FEATURE_DISPLAY]


def _is_in_person_operation(utterance: str) -> bool:
    """IT-7 G1「在場操作」：第一人稱主語 ＋ 操作謂語（純句法、0 語義判斷）。"""
    if not any(p in utterance for p in _FIRST_PERSON_TOKENS):
        return False
    return any(v in utterance for v in _OPERATION_TOKENS)


def _concern_gate(record: DeltaRecord, utterance: str) -> bool:
    """IT-7 當輪關切度量守門：**G1 ∧ G2 才注入**；IT-8 背景提及靜默鋼印。

    IT-9 的一句上限由固定模板字面保證（每次至多注入一份 ``[當下的關切]``）。
    """
    g1 = _is_in_person_operation(utterance)
    g2 = bool(record.hazard_suspected or record.duty_relevant)
    if not (g1 and g2):
        return False
    return True


__all__ = [
    "AppraisalInput",
    "AxisPack",
    "Bridge",
    "DeltaRecord",
    "InstancePack",
    "appraise",
    "clear_pack_cache",
    "format_epistemic_horizon_delta",
    "is_modern_native_agent",
    "load_epistemic_pack",
]
