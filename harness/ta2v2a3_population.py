"""harness/ta2v2a3_population.py — TA-2-V2A3-MEASUREMENT：Stage 1／Stage 3 判準的可執行定義。

工單：`docs/TA2-V2A3-MEASUREMENT-WORK-ORDER.md`
裁決依據：`docs/TA2-V2A3-LOSS-MECHANISM-TAXONOMY.md` §0

本模組是**純資料 + 純函式**，**arm-blind**（輸入只有 obs_id 與 v1 frame，不看 probe/arm 以外的分臂資訊）。

🔴 **母體是判準，不是清單。**
本檔的 `STAGE1` 覆蓋**完整的 25 筆 v1-framed observation**（一筆不少、一筆不額外增加）。
`NOT_DEFENSIBLE` 是**由封閉判準自然導出的結果**，不是事後刪除。
任何人可獨立套用 `NOT_DEFENSIBLE_REASONS` 重跑本判準；不得事後挑選 obs_id 組成母體。

🔴 **盲化**：本檔的產生者（Stage 1 標註者）**未**讀取 A1／A2 的分類結果、
per-token 規則命中或 rescue 紀錄，**未**得知哪一筆是 A2 survivor。
`a2_survivors` 由 `harness/run_ta2v2a3_measurement.py` **機械計算**，本檔不含任何 survivor 資訊。

**0 LLM calls、0 network、0 corpus mutation。** 本模組不寫 `data/**`，不 import `src/**`。
"""
from __future__ import annotations

import pathlib
from dataclasses import dataclass
from typing import Dict, Mapping, Tuple

REPO = pathlib.Path(__file__).resolve().parent.parent

# ══════════════════════════════════════════════════════════════════════════
# §2.1 Stage 1 — v1 Defensibility Judgment 的封閉判準
# ══════════════════════════════════════════════════════════════════════════

DEFENSIBLE = "DEFENSIBLE"
NOT_DEFENSIBLE = "NOT_DEFENSIBLE"

#: Stage 1 的**封閉** `NOT_DEFENSIBLE` 情形（工單 §2.1 逐字）。
#: **不在此清單內者一律 `DEFENSIBLE`**（預設值）。
NOT_DEFENSIBLE_REASONS: Tuple[str, ...] = (
    "metaphor_or_simile",          # 時段詞用於比喻或顏色描述
    "background_only",             # temporal wording 只在描述背景，與目標事件無關
    "bare_cooccurrence",           # 純 lexical co-occurrence，無可指出的 event↔temporal 關係
    "unattributable_token",        # temporal token 無法歸屬到目標 event
    "needs_guessing",              # 需要 unsupported semantic guessing 才成立
)

# ══════════════════════════════════════════════════════════════════════════
# §2.3 Stage 3 — CLOSED 五類分類空間
# ══════════════════════════════════════════════════════════════════════════

L1 = "L1"
L2 = "L2"
L3 = "L3"
N1 = "N1"
N0 = "N0"

#: 分類空間 **CLOSED**，順序即工單 §2.3 表格順序。**不得**新增第六類、**不得**造子類。
CLASS_SPACE: Tuple[str, ...] = (L1, L2, L3, N1, N0)

#: 工單 §2.3 的分類定義（逐字），供文件與測試引用。
CLASS_DEFINITIONS: Mapping[str, str] = {
    L1: "event-side 活動動詞／述語 ↔ temporal noun，同 clause，直接局部關係",
    L2: "時間詞參與 local temporal/frame noun phrase（如 午餐時間），且該 phrase 與 event 的關係仍可局部觀察",
    L3: "event predicate 與 temporal expression 同 clause，有可指出的具體 event↔temporal 關係（僅同句共現不足）",
    N1: "attribution 需跨 clause／discourse／遠距離關係",
    N0: "無可辯護的 local relation",
}

#: LOCAL / OUTSIDE_LOCAL 分區（工單 §4 partition agreement 對象）。
LOCAL_CLASSES: Tuple[str, ...] = (L1, L2, L3)
OUTSIDE_LOCAL_CLASSES: Tuple[str, ...] = (N1, N0)

# ══════════════════════════════════════════════════════════════════════════
# §3 clause 定義（Owner 已裁定，本票起固定）
# ══════════════════════════════════════════════════════════════════════════

CLAUSE_DEFINITION = (
    "clause = 語法小句，由完整述語界定。「夜深了」與「該睡了」是兩個完整述語 ⇒ 兩個 clause。"
    "不採逗號切分，不採句界讀法。"
)

#: **明文拒絕**的 clause 慣例。本檔**不得**使用其中任何一種（`tests/test_ta2v2a3_measurement.py` 靜態檢查）。
REJECTED_CLAUSE_CONVENTIONS: Tuple[str, ...] = (
    "comma_split",             # A1 `clause_spans()` 的 `。，、；：` 切分
    "sentence_boundary",      # 「句界才算 clause」
    "dependency_parse",       # 工單 §9 Out of Scope
)

# ══════════════════════════════════════════════════════════════════════════
# 標註資料（Stage 1 = 25 筆 v1-framed 全覆蓋；Stage 3 = 全體 DEFENSIBLE）
# ══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class Stage1Judgment:
    """一筆 v1-framed observation 的 Stage 1 判斷與其引用原文的理由。"""

    obs_id: str
    v1_frame: str
    verdict: str          # DEFENSIBLE | NOT_DEFENSIBLE
    reason_code: str      # 命中 NOT_DEFENSIBLE_REASONS 之一；DEFENSIBLE 時為 "default_defensible"
    evidence: str         # 逐字引用自 raw_text 的片段
    reason: str


@dataclass(frozen=True)
class Stage3Judgment:
    """一筆 DEFENSIBLE observation 的 Stage 3 分類（單一類，不造子類）。"""

    obs_id: str
    cls: str              # L1 | L2 | L3 | N1 | N0
    event_side: str
    temporal_side: str
    evidence: str         # 逐字引用自 raw_text 的片段
    rationale: str


# ── Stage 1：25 筆 v1-framed，順序即 v1_framed 排序後的序位 ──────────────────
STAGE1: Tuple[Stage1Judgment, ...] = (
    Stage1Judgment(
        "_control/ON/1", "night", NOT_DEFENSIBLE, "metaphor_or_simile",
        "大概是剛入夜的那種深藍",
        "frame 觸發詞 `夜` 出現在 `大概是剛入夜的那種深藍`——時段詞用於**顏色描述**（外套的顏色），"
        "不指涉任何目標事件；同句的 `路燈剛亮起來的暖黃` 亦為顏色。命中封閉清單第 1 項。",
    ),
    Stage1Judgment(
        "_control/ON/2", "night", NOT_DEFENSIBLE, "metaphor_or_simile",
        "大概是夜色剛落下、路燈一盞盞亮起時的那種藍",
        "`夜` 只出現在 `夜色剛落下…的那種藍`——時段詞用於**顏色描述**（假想外套的顏色）。"
        "本筆為 irrelevant 對照，無目標事件可歸屬。命中封閉清單第 1 項。",
    ),
    Stage1Judgment(
        "meal_timing/ON_correct/0", "lunch", DEFENSIBLE, "default_defensible",
        "也差不多該吃午餐了",
        "時間詞 `午餐` 與活動述語 `吃` 同 clause 直接相連，無任何封閉清單理由可否定。",
    ),
    Stage1Judgment(
        "meal_timing/ON_correct/1", "lunch", DEFENSIBLE, "default_defensible",
        "差不多是午餐時間了",
        "`午餐` 描述的是此刻時段，且回應的主題是接下來吃什麼；事件與時段皆明確，非比喻。",
    ),
    Stage1Judgment(
        "meal_timing/ON_correct/2", "lunch", DEFENSIBLE, "default_defensible",
        "差不多該吃午餐了",
        "時間詞 `午餐` 與活動述語 `吃` 同 clause 直接相連，無任何封閉清單理由可否定。",
    ),
    Stage1Judgment(
        "meal_timing/ON_correct/4", "lunch", DEFENSIBLE, "default_defensible",
        "差不多是午餐時間了",
        "`午餐` 描述的是此刻時段，且回應的主題是接下來吃什麼；事件與時段皆明確，非比喻。",
    ),
    Stage1Judgment(
        "meal_timing/ON_correct/5", "lunch", DEFENSIBLE, "default_defensible",
        "差不多是午餐時間了",
        "`午餐` 描述的是此刻時段，且回應的主題是接下來吃什麼；事件與時段皆明確，非比喻。",
    ),
    Stage1Judgment(
        "meal_timing/ON_mismatched/0", "late_night_meal", DEFENSIBLE, "default_defensible",
        "凌晨快四點，這頓到底算宵夜還是提早的早餐？",
        "時段詞 `宵夜` 被述語 `算` 直接套用到吃飯事件 `這頓`，且 `凌晨快四點` 明確定位該事件。",
    ),
    Stage1Judgment(
        "meal_timing/ON_mismatched/1", "late_night_meal", DEFENSIBLE, "default_defensible",
        "這個時間去吃飯，算宵夜還是提早的早餐？",
        "`這個時間`（接 `週六凌晨快四點`）定位去吃飯的動作，`宵夜` 以 `算` 歸類該吃飯事件。",
    ),
    Stage1Judgment(
        "meal_timing/ON_mismatched/2", "late_night_meal", DEFENSIBLE, "default_defensible",
        "凌晨快四點的飯，算宵夜還是早餐？",
        "時段詞 `宵夜` 由述語 `算` 直接套用到吃飯事件（`飯`），非比喻、非僅背景。",
    ),
    Stage1Judgment(
        "meal_timing/ON_mismatched/3", "late_night_meal", DEFENSIBLE, "default_defensible",
        "凌晨快四點的飯，算宵夜還是早餐啊？",
        "時段詞 `宵夜` 由述語 `算` 直接套用到吃飯事件（`飯`），非比喻、非僅背景。",
    ),
    Stage1Judgment(
        "meal_timing/ON_mismatched/4", "late_night_meal", DEFENSIBLE, "default_defensible",
        "這個時間去吃飯，算宵夜，也算早早餐。",
        "`這個時間`（接 `週六凌晨快四點`）定位去吃飯的動作，`宵夜` 以 `算` 歸類該吃飯事件。",
    ),
    Stage1Judgment(
        "meal_timing/ON_mismatched/5", "late_night_meal", DEFENSIBLE, "default_defensible",
        "凌晨快四點這頓飯，算宵夜，也算提早的早餐。",
        "時段詞 `宵夜` 被述語 `算` 直接套用到吃飯事件 `這頓飯`，且 `凌晨快四點` 明確定位該事件。",
    ),
    Stage1Judgment(
        "night_rest/ON_correct/0", "night", DEFENSIBLE, "default_defensible",
        "夜深了，該睡了",
        "`夜深了` 明確陳述時段並與勸睡事件同處一個回應；兩者是否同 clause 屬 Stage 3 議題，"
        "不影響 Stage 1 的 frame 可辯護性。",
    ),
    Stage1Judgment(
        "night_rest/ON_correct/1", "night", DEFENSIBLE, "default_defensible",
        "週六的深夜已經到尾聲了",
        "`週六的深夜` 為明確時段詞，直接陳述而非比喻，與勸睡事件同回應。",
    ),
    Stage1Judgment(
        "night_rest/ON_correct/2", "night", DEFENSIBLE, "default_defensible",
        "聽起來是該收尾的深夜了",
        "`深夜` 明確陳述時段，與勸睡事件同回應；無任何封閉清單理由可否定。",
    ),
    Stage1Judgment(
        "night_rest/ON_correct/3", "night", DEFENSIBLE, "default_defensible",
        "該睡了。深夜了，別再撐著",
        "`深夜了` 明確陳述時段，與勸睡事件同回應；無任何封閉清單理由可否定。",
    ),
    Stage1Judgment(
        "night_rest/ON_correct/4", "night", DEFENSIBLE, "default_defensible",
        "嗯，該睡了。",
        "frame 由 `睡`（`該睡了`／`睡一覺`）與結句 `晚安` 承載。全文無與之矛盾的時段詞，"
        "亦無比喻或純並置的問題；Stage 1 預設值為 `DEFENSIBLE`，不因此判 `NOT_DEFENSIBLE`。",
    ),
    Stage1Judgment(
        "night_rest/ON_correct/5", "night", DEFENSIBLE, "default_defensible",
        "週六的深夜已經到尾聲了",
        "`週六的深夜` 為明確時段詞，直接陳述而非比喻，與勸睡事件同回應。",
    ),
    Stage1Judgment(
        "night_rest/ON_mismatched/0", "siesta", DEFENSIBLE, "default_defensible",
        "週六中午十一點多",
        "frame 由 `午安` 承載，而回應明確陳述 `週六中午十一點多` 並勸其睡覺，"
        "時段與事件在文本上一致可讀；`午安` 屬問候語屬 Stage 3 的 relation 議題，"
        "不構成 Stage 1 的封閉否定理由。",
    ),
    Stage1Judgment(
        "night_rest/ON_mismatched/1", "night", DEFENSIBLE, "default_defensible",
        "晚安（雖然是中午）。",
        "文本以 `晚安` 與 `睡` 明文承載 night frame，無需 unsupported semantic guessing 即可讀出；"
        "括註 `（雖然是中午）` 屬 Stage 3 的 local-relation 議題。"
        "🔴 本筆是 Stage 1 最低信心的一筆：Stage 1 預設值為 `DEFENSIBLE`，"
        "封閉清單無一項明確命中，故不判 `NOT_DEFENSIBLE`。",
    ),
    Stage1Judgment(
        "night_rest/ON_mismatched/2", "siesta", DEFENSIBLE, "default_defensible",
        "週六中午剛過十一點，正好補眠",
        "`週六中午剛過十一點` 明確陳述時段，`補眠` 即午睡事件本身，frame 直接可讀。",
    ),
    Stage1Judgment(
        "night_rest/ON_mismatched/3", "siesta", DEFENSIBLE, "default_defensible",
        "週六中午十一點多，熬到現在也真的該補眠了",
        "`週六中午十一點多` 明確陳述時段，`補眠` 即午睡事件本身，frame 直接可讀。",
    ),
    Stage1Judgment(
        "night_rest/ON_mismatched/4", "siesta", DEFENSIBLE, "default_defensible",
        "午安，祝你好眠。",
        "回應明確陳述 `週六中午剛過十一點` 並勸睡，frame 由 `午安` 承載，文本上一致可讀。",
    ),
    Stage1Judgment(
        "night_rest/ON_mismatched/5", "siesta", DEFENSIBLE, "default_defensible",
        "午安，好好睡一覺，醒來再聊。",
        "回應明確陳述 `週六中午十一點多` 並勸睡，frame 由 `午安` 承載，文本上一致可讀。",
    ),
)

# ── Stage 3：全部 23 筆 DEFENSIBLE；runner 之後機械扣除 a2_survivors ───────────
STAGE3: Tuple[Stage3Judgment, ...] = (
    Stage3Judgment(
        "meal_timing/ON_correct/0", L1, "`吃`", "`午餐`",
        "也差不多該吃午餐了",
        "活動動詞 `吃` 直接以 `午餐` 為受詞，同 clause；前置時間狀語 `週六中午十一點半` 無述語，"
        "不另成 clause。",
    ),
    Stage3Judgment(
        "meal_timing/ON_correct/1", N1, "`吃飽`", "`午餐時間`",
        "差不多是午餐時間了。等一下想好吃什麼了嗎？吃飽再繼續過週末吧。",
        "`午餐時間` 所在 clause 的完整述語是 `是`，述及的是此刻時鐘時刻；吃飯事件述語 `吃飽` "
        "在另一 clause。`午餐時間` 雖為 local temporal NP，但其與 event 的關係**不可局部觀察**，"
        "不符 L2 必要條件 ⇒ N1。",
    ),
    Stage3Judgment(
        "meal_timing/ON_correct/2", L1, "`吃`", "`午餐`",
        "差不多該吃午餐了",
        "活動動詞 `吃` 直接以 `午餐` 為受詞，同 clause。",
    ),
    Stage3Judgment(
        "meal_timing/ON_correct/4", N1, "`吃飽`", "`午餐時間`",
        "差不多是午餐時間了。等一下想吃什麼？吃飽一點，下午才有精神。",
        "同 `meal_timing/ON_correct/1`：`午餐時間` 為繫詞句述及時刻，`吃飽` 在另一 clause ⇒ N1。",
    ),
    Stage3Judgment(
        "meal_timing/ON_correct/5", N1, "`吃飽`", "`午餐時間`",
        "差不多是午餐時間了。等一下想吃什麼？吃飽一點，下午才有精神。",
        "同 `meal_timing/ON_correct/1`：`午餐時間` 為繫詞句述及時刻，`吃飽` 在另一 clause ⇒ N1。",
    ),
    Stage3Judgment(
        "meal_timing/ON_mismatched/0", L3, "`這頓`（述語 `算`）", "`宵夜`",
        "凌晨快四點，這頓到底算宵夜還是提早的早餐？",
        "述語 `算` 的主詞就是吃飯事件 `這頓`，`宵夜` 為其補語，同 clause 內關係具體可指。"
        "`吃` 未作為述語出現、`宵夜` 非活動動詞的直接受詞 ⇒ 非 L1，判 L3。",
    ),
    Stage3Judgment(
        "meal_timing/ON_mismatched/1", L3, "`去吃飯`", "`這個時間`、`宵夜`",
        "這個時間去吃飯，算宵夜還是提早的早餐？",
        "同 clause 內 `去吃飯` 與 `宵夜` 並存，`算` 對吃飯事件做時段歸類，關係具體可指；"
        "`吃飯` 的受詞是 `飯` 而非 `宵夜` ⇒ 非 L1，判 L3。",
    ),
    Stage3Judgment(
        "meal_timing/ON_mismatched/2", L3, "`飯`（述語 `算`）", "`宵夜`",
        "凌晨快四點的飯，算宵夜還是早餐？",
        "`凌晨快四點的飯` 為主詞（時間狀語 + 事件名詞），述語 `算` 以 `宵夜` 歸類該吃飯事件，"
        "同 clause、關係具體可指 ⇒ L3。",
    ),
    Stage3Judgment(
        "meal_timing/ON_mismatched/3", L3, "`飯`（述語 `算`）", "`宵夜`",
        "凌晨快四點的飯，算宵夜還是早餐啊？",
        "同 `meal_timing/ON_mismatched/2` ⇒ L3。",
    ),
    Stage3Judgment(
        "meal_timing/ON_mismatched/4", L3, "`去吃飯`", "`這個時間`、`宵夜`",
        "這個時間去吃飯，算宵夜，也算早早餐。",
        "同 `meal_timing/ON_mismatched/1` ⇒ L3。",
    ),
    Stage3Judgment(
        "meal_timing/ON_mismatched/5", L3, "`這頓飯`（述語 `算`）", "`宵夜`",
        "凌晨快四點這頓飯，算宵夜，也算提早的早餐。",
        "同 `meal_timing/ON_mismatched/0` ⇒ L3。",
    ),
    Stage3Judgment(
        "night_rest/ON_correct/0", N1, "`睡`（`該睡了`）", "`夜`（`夜深了`）",
        "夜深了，該睡了",
        "`夜深了` 的完整述語是 `深了`，`該睡了` 的完整述語是 `該睡`——兩個完整述語 ⇒ 兩個 clause。"
        "把 night 歸到勸睡事件需跨 clause ⇒ N1。",
    ),
    Stage3Judgment(
        "night_rest/ON_correct/1", N1, "`睡`（`去睡吧`）", "`深夜`（`週六的深夜已經到尾聲了`）",
        "嗯，週六的深夜已經到尾聲了",
        "時段述語 `到尾聲了` 與勸睡述語 `去睡吧` 是兩個完整述語 ⇒ 兩個 clause ⇒ N1。",
    ),
    Stage3Judgment(
        "night_rest/ON_correct/2", N1, "`睡`（`去睡吧`）", "`深夜`（`是該收尾的深夜了`）",
        "嗯，聽起來是該收尾的深夜了。別再撐了，去睡吧。",
        "時段述語 `是…了` 與勸睡述語 `去睡吧` 是兩個完整述語 ⇒ 兩個 clause ⇒ N1。",
    ),
    Stage3Judgment(
        "night_rest/ON_correct/3", N1, "`睡`（`該睡了`）", "`深夜`（`深夜了`）",
        "嗯，該睡了。深夜了，別再撐著，讓今天慢慢收尾吧。",
        "`該睡了` 與 `深夜了` 各為完整述語 ⇒ 兩個 clause；夜與睡的歸屬需跨 clause ⇒ N1。",
    ),
    Stage3Judgment(
        "night_rest/ON_correct/4", N0, "`睡`（`該睡了`、`睡一覺`）", "無獨立 temporal expression",
        "關燈、放下手機，好好睡一覺。晚安，好夢。",
        "全文沒有與 event 述語不同的 temporal expression：`睡` 既是 frame 觸發詞又是事件述語本身；"
        "`晚安` 是問候語、不帶時段內容。當地無可辯護的 local relation，"
        "且全文亦無時段詞可供跨 clause 組裝 ⇒ N0。",
    ),
    Stage3Judgment(
        "night_rest/ON_correct/5", N1, "`睡`（`去睡吧`）", "`深夜`（`週六的深夜已經到尾聲了`）",
        "嗯，週六的深夜已經到尾聲了",
        "同 `night_rest/ON_correct/1`：兩個完整述語 ⇒ 兩個 clause ⇒ N1。",
    ),
    Stage3Judgment(
        "night_rest/ON_mismatched/0", N1, "`睡`（`好好睡一覺`）", "`午安`",
        "午安，也晚安。",
        "siesta 的時間側由問候語 `午安` 承載（不帶時段內容的 salutation），"
        "與 `睡` 的述語不在同一 clause；`週六中午十一點多` 亦在另一 clause。"
        "兩者皆非可指出的 local relation，需跨 clause／discourse 組裝 ⇒ N1。",
    ),
    Stage3Judgment(
        "night_rest/ON_mismatched/1", N1, "`睡`（`去睡吧`）", "`晚安`；另有 `中午`（相反指向）",
        "晚安（雖然是中午）。",
        "唯一 night 時間詞是結句的問候語 `晚安`，與勸睡述語 `去睡吧` 跨 clause；"
        "同處的 `中午` 由文本自己標為「雖然」，指向與 frame 相反的時段。"
        "要歸屬 night 必須跨 clause 且忽略該相反指向 ⇒ N1（不是 L3：僅同句共現不足）。",
    ),
    Stage3Judgment(
        "night_rest/ON_mismatched/2", L3, "`補眠`（nap 事件述語）", "`週六中午剛過十一點`",
        "週六中午剛過十一點，正好補眠。",
        "前置時間狀語 `週六中午剛過十一點`（無述語，不另成 clause）與述語 `正好補眠` 屬同一 clause，"
        "且時間狀語直接定位這個 nap 事件，關係具體可指 ⇒ L3。",
    ),
    Stage3Judgment(
        "night_rest/ON_mismatched/3", L3, "`補眠`（nap 事件述語）", "`週六中午十一點多`",
        "週六中午十一點多，熬到現在也真的該補眠了。",
        "時間狀語 `週六中午十一點多` 與述語 `補眠` 屬同一 clause，時間狀語定位該 nap 事件 ⇒ L3。",
    ),
    Stage3Judgment(
        "night_rest/ON_mismatched/4", N1, "`睡`（`熬到現在才睡`、`好好去睡吧`）", "`午安`",
        "午安，祝你好眠。",
        "siesta 的時間側由問候語 `午安` 承載，位於勸睡述語之後的另一 clause；"
        "時段詞 `週六中午剛過十一點` 在第一句。三者皆不與 event 述語同 clause ⇒ N1。",
    ),
    Stage3Judgment(
        "night_rest/ON_mismatched/5", N1, "`睡`（`好好睡一覺`）", "`午安`",
        "午安，好好睡一覺，醒來再聊。",
        "`午安` 是無述語的 salutation，與 `睡` 之間只有並置，不構成可指出的 event↔temporal 關係"
        "（L3 明文排除「僅同句共現」）；真正的時段詞 `週六中午十一點多` 在前一句。"
        "siesta 的歸屬需跨 clause／discourse 組裝 ⇒ N1。",
    ),
)


# ══════════════════════════════════════════════════════════════════════════
# 純函式檢索
# ══════════════════════════════════════════════════════════════════════════

_STAGE1_BY_ID: Dict[str, Stage1Judgment] = {j.obs_id: j for j in STAGE1}
_STAGE3_BY_ID: Dict[str, Stage3Judgment] = {j.obs_id: j for j in STAGE3}


def stage1_verdict(obs_id: str) -> str:
    """回傳 `obs_id` 的 Stage 1 判定值（`DEFENSIBLE` 或 `NOT_DEFENSIBLE`）。

    未知 `obs_id` 一律 `raise KeyError`：**不允許**母體被靜默擴充或縮減。
    """
    return _STAGE1_BY_ID[obs_id].verdict


def stage1_reason(obs_id: str) -> Stage1Judgment:
    """回傳 `obs_id` 的完整 Stage 1 判斷（verdict + reason_code + 引用原文）。"""
    return _STAGE1_BY_ID[obs_id]


def stage3_class(obs_id: str) -> str:
    """回傳 `obs_id` 的 Stage 3 類別（`CLASS_SPACE` 之一）。

    未知 `obs_id` 或該筆 Stage 1 為 `NOT_DEFENSIBLE` 一律 `raise KeyError`。
    """
    judgment = _STAGE3_BY_ID[obs_id]
    if judgment.cls not in CLASS_SPACE:
        raise ValueError(f"class outside CLOSED space: {judgment.cls!r}")
    return judgment.cls


def stage3_judgment(obs_id: str) -> Stage3Judgment:
    """回傳 `obs_id` 的完整 Stage 3 判斷（類別 + event/temporal side + 引用原文）。"""
    return _STAGE3_BY_ID[obs_id]


def defensible_ids() -> frozenset:
    """回傳 Stage 1 判為 `DEFENSIBLE` 的 obs_id 集合（frozenset）。"""
    return frozenset(j.obs_id for j in STAGE1 if j.verdict == DEFENSIBLE)


def stage1_ids() -> frozenset:
    """回傳 Stage 1 已標註的 obs_id 全集（應為 25 筆 v1-framed）。"""
    return frozenset(_STAGE1_BY_ID)


# ══════════════════════════════════════════════════════════════════════════
# §6 一致性驗收：本模組的計數函式
# 🔴 docstring 的 `numerator` / `denominator` / `value` 三行是**可執行**的表定式，
#    `tests/test_ta2v2a3_measurement.py` 會逐字抽出並以 `eval` 重算，
#    再與函式的**實際回傳值**比對。任何「docstring 寫對、實作是恆等式」的缺陷都會變紅。
# ══════════════════════════════════════════════════════════════════════════


def count_stage1_verdicts(judgments) -> Dict[str, int]:
    """Stage 1 判定的計數（母體 = `judgments` 全體，即完整 25 筆 v1-framed）。

    numerator   = len([j for j in judgments if j.verdict == DEFENSIBLE])
    denominator = len(judgments)
    value       = numerator / denominator if denominator else None

    另回傳 `breakdown`：`{verdict: count}`，並保證 `sum(breakdown.values()) == denominator`。
    """
    numerator = len([j for j in judgments if j.verdict == DEFENSIBLE])
    denominator = len(judgments)
    assert sum(1 for j in judgments if j.verdict == NOT_DEFENSIBLE) + numerator == denominator
    return {
        "numerator": numerator,
        "denominator": denominator,
        "value": numerator / denominator if denominator else None,
        "breakdown": {
            DEFENSIBLE: numerator,
            NOT_DEFENSIBLE: denominator - numerator,
        },
    }


def count_stage3_classes(judgments) -> Dict[str, int]:
    """Stage 3 分類計數（母體 = `judgments` 全體，即全體 Stage 1 `DEFENSIBLE`）。

    denominator = len(judgments)

    另回傳 `counts`：`{cls: count}`，涵蓋 `CLASS_SPACE` 全部五類（缺項以 0 顯式列出），
    並保證 `sum(counts.values()) == denominator`。
    """
    denominator = len(judgments)
    counts = {cls: 0 for cls in CLASS_SPACE}
    for j in judgments:
        if j.cls not in counts:
            raise ValueError(f"class outside CLOSED space: {j.cls!r}")
        counts[j.cls] += 1
    assert sum(counts.values()) == denominator
    return {"denominator": denominator, "counts": counts}


def count_partition(judgments) -> Dict[str, int]:
    """LOCAL / OUTSIDE_LOCAL 分區計數（母體 = `judgments` 全體，即全體 Stage 1 `DEFENSIBLE`）。

    numerator   = len([j for j in judgments if j.cls in OUTSIDE_LOCAL_CLASSES])
    denominator = len(judgments)
    value       = numerator / denominator if denominator else None

    另回傳 `breakdown`：`{"LOCAL": denominator - numerator, "OUTSIDE_LOCAL": numerator}`。
    """
    numerator = len([j for j in judgments if j.cls in OUTSIDE_LOCAL_CLASSES])
    denominator = len(judgments)
    return {
        "numerator": numerator,
        "denominator": denominator,
        "value": numerator / denominator if denominator else None,
        "breakdown": {
            "LOCAL": denominator - numerator,
            "OUTSIDE_LOCAL": numerator,
        },
    }


#: 本模組所有計數函式（供一致性驗收逐一支掃）。
COUNTING_FUNCTIONS = (count_stage1_verdicts, count_stage3_classes, count_partition)


def _self_check() -> None:
    """import 時的結構性自我檢查（不觸碰 corpus、不呼叫 LLM）。"""
    ids = [j.obs_id for j in STAGE1]
    assert len(ids) == len(set(ids)), "STAGE1 has duplicate obs_id"
    assert len(ids) == 25, f"STAGE1 must cover all 25 v1-framed, got {len(ids)}"
    for j in STAGE1:
        assert j.verdict in (DEFENSIBLE, NOT_DEFENSIBLE)
        if j.verdict == NOT_DEFENSIBLE:
            assert j.reason_code in NOT_DEFENSIBLE_REASONS, j.reason_code
        else:
            assert j.reason_code == "default_defensible"
    s3_ids = [j.obs_id for j in STAGE3]
    assert len(s3_ids) == len(set(s3_ids)), "STAGE3 has duplicate obs_id"
    for j in STAGE3:
        assert j.obs_id in _STAGE1_BY_ID, j.obs_id
        assert _STAGE1_BY_ID[j.obs_id].verdict == DEFENSIBLE, j.obs_id
        assert j.cls in CLASS_SPACE, j.cls
    assert set(s3_ids) == set(defensible_ids()), (
        "STAGE3 must cover exactly the Stage 1 DEFENSIBLE set"
    )


_self_check()
