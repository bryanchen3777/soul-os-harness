"""src/inner_life/contradiction_accumulator_prototype.py — 矛盾累積器「饋入判定器」（沙盒原型：純函式、唯讀、零 LLM）。

工單：logs/FEAT-SUPERSEDED-ACCUMULATOR-PIPELINE-WORK-ORDER.md（FEAT-SUPERSEDED-ACCUMULATOR-PIPELINE）
上游：logs/FEAT-SCHEMA-CONTRADICTION-IMPLEMENTATION.md（Phase 1 實測：探針在生產圖譜上恆 0 組候選）

為什麼需要這個模組
------------------
``superseded`` 是召回池唯一能被**合法**回填的來源，但它**結構性不可達**：
``engine.record_contradiction()``（累積器寫入）的唯一呼叫點在 ``engine.supersede()`` 內，
而 ``supersede()`` 又以「獨立矛盾證據達門檻」為觸發前提 ⇒ 累積器恆空 ⇒ ``supersede()``
恆拋 ``ValueError``。

Phase 1 的探針（``src/inner_life/contradiction_probe.py``）回答的是死鎖的**前半段**：
「候選矛盾從哪裡來」。本模組回答死鎖的**後半段**：
「候選矛盾若真的寫進累積器，**會不會達門檻**」——
**不寫入任何引擎狀態**，只用引擎**既有計數器語意**算出「獨立矛盾數／跨模擬日數」，
再與引擎**既有門檻常數**比對。

本模組的硬約束（工單 §2.3）
---------------------------
- **零門檻常數**：模組內**無任何數值常數**；兩個門檻一律以
  ``getattr(prior, "<NAME>")`` 取得（``prior`` ＝ ``soul_elevation.prior``）。
  契約值 ``LIFECYCLE_N_SUPERSEDE``／``LIFECYCLE_SUPERSEDE_MIN_DAYS_SPREAD``
  **不得**新增／調低／調高——本模組只是**引用**它們。
- **零新欄位**：不新增、不修改任何引擎欄位；輸出是本模組**自己的** dataclass。
- **零 IO／零 LLM**：不讀檔、不連網、不呼叫任何模型。
- **不寫引擎狀態**：**不**呼叫 ``engine.record_contradiction()``／``engine.supersede()``，
  **不** import ``soul_elevation.engine``（守衛 S3 以 AST 釘死）。
- **確定性**：同一輸入兩次呼叫輸出逐位元相同（無時間戳、無 set 走訪順序洩漏到輸出）。

計數語意（**逐字對齊**引擎既有實作）
----------------------------------
本模組的兩個計數器是 ``soul_elevation.engine`` 既有 private 計數器的**鏡像**：

- 獨立矛盾數：``engine._count_independent_contradictions()``
  —— 同一 ``source_id`` 只算 1；同一 ``event_identity`` 只算 1（SE-1 evidence_key 語意）。
- 跨模擬日數：``engine._contradiction_spread_days()``
  —— 以 ``ts`` 的**日期部分**計；壞 ``ts`` 不計入。

⇒ 本模組**不重複發明**門檻語意，只做「同樣的計數、同樣的門檻」⇒ 可與引擎逐位元對帳
（守衛 S4 即以此比對）。

與引擎唯一的差異（**刻意、且已記錄**）
------------------------------------
引擎的 ``_parse_ts()`` 對非字串 ``ts`` 會拋 ``AttributeError``；本模組對非字串一律當
「壞值」（不計入跨日），避免呼叫端用壞資料把判定器炸掉。
**字串輸入**的行為與引擎**逐位元相同**（守衛 S4 以引擎計數器對帳）。

邊界
----
本模組**不是** Phase 2 的接線提案，也**不**做任何寫入：
它只回答「若這些候選寫進累積器，門檻會不會亮」。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional, Set, Tuple

#: 門檻常數的**唯讀**來源（``soul_elevation.prior``）。本模組只 ``getattr`` 取值，
#: 從不設定、不改寫、不新增 —— 門檻是**契約不是參數**。
from soul_elevation import prior


@dataclass(frozen=True)
class AccumulatorAssessment:
    """「若這些候選寫進累積器，會達門檻嗎」的判定結果（frozen；可稽核、可序列化）。

    欄位全部是**本模組自有的**觀測值，**不寫入**任何引擎欄位
    （引擎那邊的欄位族是 ``ElevationNode.contradiction_pressure`` 等，本模組不碰）。
    """

    #: 被判定的節點 id（**只是標籤**：本模組不做節點比對，節點篩選由呼叫端負責）。
    node_id: str
    #: 進入判定的記錄總數（既有 ``contradiction_pressure`` ＋ 候選矛盾）。
    record_count: int
    #: 獨立矛盾數（同一 ``source_id``／``event_identity`` 只算 1）。
    independent_contradictions: int
    #: 跨模擬日數（``ts`` 日期部分去重）。
    spread_days: int
    #: 達門檻所需的獨立矛盾數（引用引擎既有常數）。
    required_independent: int
    #: 達門檻所需的跨模擬日數（引用引擎既有常數）。
    required_spread_days: int
    #: 是否達「獨立矛盾數」門檻。
    meets_independent_threshold: bool
    #: 是否達「跨模擬日數」門檻。
    meets_spread_threshold: bool
    #: 是否**同時**達兩個門檻（＝ ``engine.supersede()`` 的非 essence 分支條件）。
    meets_supersede_thresholds: bool
    #: 距離獨立矛盾數門檻的差距（**≤ 0 表示已達標或超出**）。
    independent_gap: int
    #: 距離跨模擬日數門檻的差距（**≤ 0 表示已達標或超出**）。
    spread_gap: int


def _get(obj: Any, key: str) -> Any:
    """取欄位值（dict／物件皆可）⇒ 呼叫端可餵 JSON 列、引擎物件或探針的 dataclass。"""
    if isinstance(obj, Mapping):
        return obj.get(key)
    return getattr(obj, key, None)


def _parse_ts(value: Any) -> Optional[datetime]:
    """解析 ISO 8601 時間戳（**鏡像** 引擎 ``engine._parse_ts()``）。

    引擎版：``datetime.fromisoformat(ts.replace("Z", "+00:00"))``，壞值回 ``None``。
    本版對**非字串**一律回 ``None``（引擎會拋 ``AttributeError``）⇒ 呼叫端用壞資料
    不會把判定器炸掉。**字串輸入**與引擎逐位元相同。
    """
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _independent_count(records: Sequence[Any]) -> int:
    """獨立矛盾數（**鏡像** ``engine._count_independent_contradictions()``）。

    規則（SE-1 evidence_key 語意，與引擎同序）：

    1. ``source_id`` 已見過 ⇒ 不計；
    2. ``event_identity`` 非 None 且已見過 ⇒ 不計；
    3. 否則計 1，並記下本筆的 ``source_id``／``event_identity``。

    ⚠️ 順序與引擎完全一致：命中 (2) 的記錄**不會**把 ``source_id`` 記入已見集合。

    ⚠️ 實作註記：計數用 ``list`` 累加再 ``len()`` 取值（而非 ``count = 0`` /
    ``count += 1``）⇒ 模組內**不出現任何數值字面量**，讓守衛 S1 的「零門檻常數」
    得以成立為**零數值常數**的強形式（而不只是「不寫死 3／2／5／0.3」）。
    """
    seen_sources: Set[Any] = set()
    seen_events: Set[Any] = set()
    counted: list = []
    for record in records:
        source_id = _get(record, "source_id")
        if source_id in seen_sources:
            continue
        event_identity = _get(record, "event_identity")
        if event_identity is not None and event_identity in seen_events:
            continue
        seen_sources.add(source_id)
        if event_identity is not None:
            seen_events.add(event_identity)
        counted.append(record)
    return len(counted)


def _spread_days(records: Sequence[Any]) -> int:
    """跨模擬日數（**鏡像** ``engine._contradiction_spread_days()``）。

    以 ``ts`` 的**日期部分**去重計數；壞 ``ts`` 的記錄不計入（不貢獻跨時間一致性）。
    """
    days: Set[str] = set()
    for record in records:
        parsed = _parse_ts(_get(record, "ts"))
        if parsed is not None:
            days.add(parsed.date().isoformat())
    return len(days)


def assess_accumulator(
    node_id: str,
    candidates: Sequence[Any] = (),
    existing_pressure: Sequence[Any] = (),
) -> AccumulatorAssessment:
    """判定「若這些候選矛盾寫進累積器，會達 ``supersede()`` 門檻嗎」（純函式）。

    Args:
        node_id: 被判定的節點 id（**只是標籤**）。本函式**不做節點比對**——
            「哪些候選屬於哪顆節點」是**探針**的語意，判定器不重複發明。
        candidates: 該節點的候選矛盾清單（``contradiction_probe``
            .``find_candidate_contradictions()`` 的輸出：``CandidateContradiction``
            dataclass 或等價 dict）。
        existing_pressure: 該節點**既有**的 ``contradiction_pressure``
            （``ContradictionRecord`` 或等價 dict）。

    Returns:
        ``AccumulatorAssessment``（frozen）。

    Notes:
        記錄順序＝**既有壓力在前、候選在後**，與引擎 ``supersede()`` 的累積順序一致
        （引擎先寫新矛盾、再對整份 ``contradiction_pressure`` 計數）⇒ 判定結果可與引擎
        逐位元對帳。**本函式不寫任何引擎狀態**（不呼叫 ``record_contradiction()``）。
    """
    records: Tuple[Any, ...] = tuple(existing_pressure) + tuple(candidates)

    # 門檻**只從引擎既有常數取得**（契約不是參數；此處不得出現任何數值字面量）。
    required_independent = int(getattr(prior, "LIFECYCLE_N_SUPERSEDE"))
    required_spread_days = int(getattr(prior, "LIFECYCLE_SUPERSEDE_MIN_DAYS_SPREAD"))

    independent = _independent_count(records)
    spread = _spread_days(records)
    meets_independent = independent >= required_independent
    meets_spread = spread >= required_spread_days

    return AccumulatorAssessment(
        node_id=node_id,
        record_count=len(records),
        independent_contradictions=independent,
        spread_days=spread,
        required_independent=required_independent,
        required_spread_days=required_spread_days,
        meets_independent_threshold=meets_independent,
        meets_spread_threshold=meets_spread,
        meets_supersede_thresholds=meets_independent and meets_spread,
        independent_gap=required_independent - independent,
        spread_gap=required_spread_days - spread,
    )


__all__ = [
    "AccumulatorAssessment",
    "assess_accumulator",
]
