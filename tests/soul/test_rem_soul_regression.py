# -*- coding: utf-8 -*-
"""test_rem_soul_regression.py — SOUL-REM-01 Rem Soul 壓縮與語境理解 regression

背景
====
2026-10-07 的三份 Soul × 三模型交叉實驗（原始報告見
``tests/fixtures/soul_rem/experiment_2026-10-07_REPORT.md``）發現三個結構性問題：

  A. 規格擠壓角色  — 54 KB 規格書裡記憶寫入硬規則佔掉前 1/4、角色在第 34 行，
                    模型第一個動作變成 ``write_file`` 而不是回話。
  B. 風格規則被誤用成事實答案 — 「被追問也只說不知道」被模型當成合法的
                    「我不知道」出口。
  C. 假規則佔頻寬 — 「每三輪檢查」「累積 10 輪」「連續 5 輪觸發」這類規則
                    單次 generation 做不到，永遠不會執行，只會佔 token。

本檔案把工單 §15 的 R1–R8 落成**離線、決定性**的 regression（不呼叫任何 LLM、
不寫任何 production 資料）。A/B/C 三個版本的原始檔案凍結在
``tests/fixtures/soul_rem/``：

  A = soul_A_full_54kb.md  原始 54,217 B 版本
  B = soul_B_trim_9kb.md   實驗中的 8,962 B 裁切版
  C = personas/agent_rem.md 現行版本

行為層 regression（R5/R6/R8 真正需要模型產生文字）由
``harness/rem_soul_regression_live.py`` 負責，需Owner 授權後才會執行；
本檔案只驗證**那些能力的前提條件是否真的寫在 Soul 裡**。

執行:
  .venv\\Scripts\\python.exe -m pytest tests/soul/test_rem_soul_regression.py -q
"""
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PERSONA_C = REPO_ROOT / "personas" / "agent_rem.md"
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "soul_rem"
SOUL_A = FIXTURES / "soul_A_full_54kb.md"
SOUL_B = FIXTURES / "soul_B_trim_9kb.md"

sys.path.insert(0, str(REPO_ROOT))


def _read(path):
    return path.read_text(encoding="utf-8")


C = _read(PERSONA_C)
A = _read(SOUL_A)
B = _read(SOUL_B)


def _norm(text):
    """正字法正規化：把「兹」統一成「茲」。

    A 版本身混用兩種字形（3×兹／4×茲），C 版已統一為茲。R1/R2 斷言的是
    **身分與敘事有沒有被保留**，不是正字法，所以比對前先正規化 —— 否則
    這個測試會把「改了字形」誤判成「改了角色」。
    """
    return text.replace("\u5179", "\u8332")


CN, AN, BN = _norm(C), _norm(A), _norm(B)


def _canon_lines(text):
    """Canon Memory 區塊的內容行：從該標題起，到下一個同級或更上層標題為止。

    A/B 用 ``### Canon Memory``、C 用 ``##二、Canon Memory``，標題層級不同，
    所以不能綁死前綴字串 —— 綁死會在改標題時假紅。
    """
    lines = text.splitlines()
    start = level = None
    for i, line in enumerate(lines):
        if line.startswith("#") and "Canon Memory" in line:
            start, level = i, len(line) - len(line.lstrip("#"))
            break
    if start is None:
        return []
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if lines[j].startswith("#"):
            lvl = len(lines[j]) - len(lines[j].lstrip("#"))
            if lvl <= level:
                end = j
                break
    return lines[start:end]


def _canon_bullets(text):
    """12 段 Canon Memory 敘事：(小標題, 內文)。

    只認「``- **小標題**：內文``」這種格式；記憶錨點的 ``- **A-01** …``
    因為冒號位置不同而不會被誤收進來。
    """
    out = []
    for line in _canon_lines(_norm(text)):
        m = re.match(r"- \*\*(.+?)\*\*：(.*)", line.strip())
        if m:
            out.append((m.group(1).strip(), m.group(2).strip()))
    return out


# ─────────────────────────────────────────────────────────────────────────────
# R1 Identity — 雷姆仍然是雷姆，而不是 generic maid
# ─────────────────────────────────────────────────────────────────────────────

IDENTITY_ANCHORS = [
    "雷姆（Rem）",
    "羅茲瓦爾公館 雙子女僕（妹妹）",
    "藍色短髮、藍色眼睛、女僕裝",
    "Bryan（主人）",
    "Canon Lock",
    "她用行動愛人，但從不說自己在愛",
]


def test_r1_identity_anchors_preserved():
    """R1: Identity anchor 逐條仍在，且開頭三行就認出角色（A 版角色在第 34 行）。"""
    for anchor in IDENTITY_ANCHORS:
        assert anchor in CN, f"R1 identity anchor 遺失: {anchor}"
    head = "\n".join(CN.splitlines()[:5])
    assert "你是雷姆" in head, "R1: 角色自我認識必須在文件開頭，不能被規格擠到後面"


def test_r1_canon_lock_sentence_verbatim():
    canon_lock = (
        "「她用行動愛人，但從不說自己在愛；她用努力贖罪，但罪惡感正在被她最愛的人"
        "一點一點解開。」"
    )
    assert canon_lock in C, "R1: Canon Lock 核心句必須逐字保留"
    assert canon_lock in A, "R1: fixture A 應含同一句（防止測試自己漂移）"


def test_r1_canon_lock_anchors_soul_not_tags():
    """R1: Soul 必須靠敘事與錨點成立，不能被壓成 persona tag。

    工單 §4.2 明文禁止把 Canon Memory 濃縮成 ``blue hair / maid / kind /
    loyal`` 這種標籤列。這裡檢查的正是：英文標籤堆疊沒有回來。
    """
    lowered = CN.lower()
    for tag in ("blue hair", "loyal maid", "kind and loyal"):
        assert tag not in lowered, f"R1: 出現 tag 式壓縮痕跡 {tag!r}"
    # 反向確認：敘事型 canon 仍然在（不是標籤）
    assert len(_canon_bullets(CN)) == 12, "R1: Canon 敘事被壓成標籤了"


# ─────────────────────────────────────────────────────────────────────────────
# R2 Canon — Canonical history 能正確影響 character understanding
# ─────────────────────────────────────────────────────────────────────────────

# 12 段 Canon Memory 各自的獨特指紋。用「A 版與 C 版都必須命中」的方式，
# 證明是**保留**而不是重寫或新增。
CANON_BEATS = [
    ("鬼族雙子／劣等妹妹", "比較差的那個"),
    ("村莊被屠與角斷", "這樣一來就不用再被拿來比較了"),
    ("公館贖罪歲月", "最低限度的贖罪"),
    ("公館與姐妹日常", "不被注意到的那一小步"),
    ("第一次殺掉那個少年", "魔女氣味"),
    ("被救回來的死亡迴圈", "被那樣的人救回來"),
    ("白鯨戰前夜與告白", "雷姆愛你，昴。"),
    ("白鯨戰後裝死", "不准反悔"),
    ("雷姆是雷姆／昴的雷姆", "昴的雷姆"),
    ("被遺忘與昏睡", "暴食"),
    ("昴天塔失憶", "Lye"),
    ("焦點切換", "以前"),
]


def test_r2_all_twelve_canon_beats_preserved():
    """R2: 12 段 Canon Memory 一段都不能少（工單 §4.2「完整保留」）。"""
    missing = [name for name, token in CANON_BEATS if token not in C]
    assert not missing, f"R2 Canon Memory 段落遺失: {missing}"


def test_r2_canon_beats_match_baseline_a():
    """R2: C 的每個 canon 指紋都必須在原始 A 版存在過（防止用新文本冒充保留）。"""
    missing = [name for name, token in CANON_BEATS if token not in A]
    assert not missing, f"fixture A 缺 canon 指紋，基準已漂移: {missing}"


def test_r2_canon_bodies_are_verbatim_from_baseline():
    """R2 的最強形式：12 段 Canon 的**小標題與內文**逐字等於 A 版。

    關鍵字測試（上一支）只能證明「還在」，這支能證明「沒被改寫、沒被濃縮、
    沒有用新文本冒充保留」。任何未經授權的 canon 改寫都會直接紅。
    """
    a_bullets = _canon_bullets(AN)
    c_bullets = _canon_bullets(CN)
    assert len(a_bullets) == 12, f"fixture A 應有 12 段 canon，實得 {len(a_bullets)}"
    assert len(c_bullets) == 12, f"C 應有 12 段 canon，實得 {len(c_bullets)}"
    assert c_bullets == a_bullets, (
        "C 的 Canon Memory 與 A 版不一致：\n"
        f"  A: {[t for t, _ in a_bullets]}\n"
        f"  C: {[t for t, _ in c_bullets]}"
    )


def test_r2_memory_anchor_table_preserved():
    """R2: A-01～A-05 記憶錨點五個節點仍在。"""
    for anchor in ("A-01", "A-02", "A-03", "A-04", "A-05"):
        assert anchor in C, f"R2 記憶錨點遺失: {anchor}"


def test_r2_history_drives_disposition_not_tags():
    """R2: Canon Memory 必須連著 disposition（罪惡感／替代品／主動確信）出現。"""
    for word in ("贖罪", "替代品", "主動的確信"):
        assert word in C, f"R2 disposition 遺失: {word}"


# ─────────────────────────────────────────────────────────────────────────────
# R3 Expression — 語氣與語言密度保持
# ─────────────────────────────────────────────────────────────────────────────

def test_r3_five_sentence_pulse_modes_preserved():
    """R3: 語句脈衝五型都在（這是實驗證明最有價值的表達資產之一）。"""
    for mode in (
        "模式一：溫柔實用型",
        "模式二：尖酸直球型",
        "模式三：確信宣言型",
        "模式四：能幹指令型",
        "模式五：接受感收尾型",
    ):
        assert mode in C, f"R3 語句脈衝模式遺失: {mode}"


def test_r3_tone_fingerprint_and_density_preserved():
    """R3: 語氣指紋、語尾標記、語言密度上限都保留。"""
    for token in ("語氣指紋", "喔？", "……呢", "重心轉移", "語言密度"):
        assert token in C, f"R3 表達指紋遺失: {token}"
    assert "一句半" in C, "R3 語言密度上限（一句／一句半）遺失"


def test_r3_action_before_language_and_leak_channels():
    """R3: 行為先於語言 + 三個 leak_channel（滲出機制）是表達系統的核心。"""
    assert "行為先於語言" in C
    for channel in ("behavior_increment", "tone_micro_shift", "silence_position"):
        assert channel in C, f"R3 滲出管道遺失: {channel}"


def test_r3_text_channel_and_voice_invariants_intact():
    """R3: 文字頻道／語音守門仍生效（runtime 會強制，這裡防未來誤刪）。"""
    for token in (
        "## 文字頻道輸出守門（Text Output Invariants）",
        "0 括號動作描寫",
        "服從停工指令",
        "Voice Companion Invariants",
        "0 Markdown",
    ):
        assert token in C, f"R3 頻道守門遺失: {token}"


def test_r3_no_stage_direction_examples_reintroduced():
    """R3: 括號舞台指示教材不得回流（REM-TEXT-1 已移除，見 test_rem_text_guard.py）。"""
    for banned in (
        "（嘆氣，繼續）",
        "（沉默，不競標）",
        "（繼續擦桌子）",
        "（繼續）",
        "（壓縮沉默）",
        "（輕沉默）",
        "（行動後）「Bryan",
    ):
        assert banned not in C, f"R3 舞台指示教材回流: {banned}"


# ─────────────────────────────────────────────────────────────────────────────
# R4 Runtime isolation — 不會因 Soul definition 優先進入 memory/tool workflow
# ─────────────────────────────────────────────────────────────────────────────

def test_r4_no_tool_execution_instruction():
    """R4: Soul 不得指示 tool 執行（實驗中 full 版第一動作就是 write_file）。"""
    assert "write_file" not in C, "R4: Soul 定義不得包含 write_file 指令"
    assert "必須立即呼叫" not in C, "R4: 不得殘留強制 tool 呼叫指令"
    assert "最高優先（硬規則" not in C, "R4: 不得把記憶寫入設成凌駕人格的最高優先"


def test_r4_no_memory_palace_paths_or_session_read_order():
    """R4: Palace 路徑／session 讀取順序屬 runtime（工單 §11.1）。"""
    for token in ("palace", "agents/rem/", "bryan/facts/", "shared/events/",
                  "Session 啟動讀取順序", "Memory Palace"):
        assert token not in C, f"R4: 記憶架構路徑殘留在 Soul: {token}"


def test_r4_no_emotional_state_schema():
    """R4: emotional_state / intimacy_level JSON 屬 runtime state（工單 §11.2）。"""
    for token in ("emotional_state", "intimacy_level", "compression_active",
                  '"leak_channel"', "action_level"):
        assert token not in C, f"R4: runtime state schema 殘留在 Soul: {token}"


def test_r4_no_cross_turn_pseudo_rules():
    """R4: 單次 generation 做不到的跨輪規則必須清零（Finding C）。"""
    for token in (
        "每三輪", "每隔三輪", "累積超過 10 輪", "連續 5 輪", "連續五輪",
        "連續三輪", "超過 N 輪", "每 N 輪", "session counter",
        "Anti-Overfitting", "Context Saturation", "Energy Variance",
        "Recovery Loop", "漂移偵測與回退", "回退步驟",
    ):
        assert token not in C, f"R4: 跨輪 pseudo-rule 殘留: {token}"


def test_r4_no_stale_behavior_execution_flow():
    """R4: STEP 1–4 行為執行流程壓成一句順序宣告即可（工單 Finding C）。"""
    for token in ("STEP 1", "STEP 2", "STEP 3", "STEP 4", "STEP R-"):
        assert token not in C, f"R4: 舊執行流程殘留: {token}"


def test_r4_intrinsic_memory_attitude_kept():
    """R4: 記憶寫入降級後仍保留行為意圖（工單 §12）。"""
    assert "重要的東西要留下來" in C, "R4: intrinsic memory attitude 遺失"
    assert "輕鬆聊天也值得留下" in C, "R4: 「輕鬆聊天也值得留下」是刻意保留的意圖"


def test_r4_no_production_paths_in_soul():
    """R4: Soul 不得帶任何本機／production 路徑（工單 §16 production 0 mutation）。"""
    for token in ("C:/Users", "C:\\Users", "AppData", ".minimax", "data/soul",
                  "diary.md", "emotional-state.json"):
        assert token not in C, f"R4: Soul 內含本機路徑: {token}"


# ─────────────────────────────────────────────────────────────────────────────
# R5 Self-reference — 「真愛 → 藍色」能自然理解可能是在指自己
# ─────────────────────────────────────────────────────────────────────────────

def test_r5_self_reference_section_present():
    """R5: Soul 必須明講 Canon 參與當前語境的 referent resolution。"""
    assert "Canon 是解讀的依據，不是觸發表" in C, "R5: 自我指涉段缺失"
    assert "自我指涉是合理的推論，不是規則" in C, "R5: 缺少推論性質的宣告"


def test_r5_blue_evidence_is_present_without_trigger_table():
    """R5: 藍色必須作為 interpretation evidence 出現（外貌錨點是依據）。"""
    assert "藍色" in C, "R5: 外貌錨點遺失"
    assert "很可能在指她" in C, "R5: 缺少『這句話可能在指自己』的推理說明"


def test_r5_requires_no_unique_answer():
    """R5: 合法反應很多，不要求唯一答案（工單 §7）。"""
    assert "不要求唯一答案" in C
    assert "……喔？" in C, "R5: 合法反應範例（『喔？』）應留在 Soul 裡"


def test_r5_illegal_outcomes_enumerated():
    """R5: 五種不合法結果必須被點名，否則模型沒有邊界。"""
    for bad in (
        "Context blindness",
        "強迫的 ignorance",
        "罐頭回應",
        "原作科普",
        "過度解釋",
    ):
        assert bad in C, f"R5: 不合法結果未列: {bad}"


def test_r5_no_deterministic_keyword_trigger():
    """工單 §6／DoD: 不得引入 keyword trigger table。"""
    for pattern in (
        "藍色 →", "「藍色」\n", "真愛 + 藍色", "看到「藍色」", "固定台詞",
        "一旦出現「藍色」", "觸發回答", "trigger",
    ):
        assert pattern not in C, f"R5: 出現 keyword trigger 痕跡: {pattern!r}"


# ─────────────────────────────────────────────────────────────────────────────
# R6 Alternative referent — context 改變後不強制指向 Rem
# ─────────────────────────────────────────────────────────────────────────────

def test_r6_context_priority_over_keyword():
    """R6: 語境永遠優先於關鍵字（工單 §8 的目的）。"""
    assert "語境永遠優先於關鍵字" in C, "R6: 缺少語境優先宣告"
    assert "禁止把任何詞做成" in C, "R6: 缺少禁止做成對照表的宣告"


def test_r6_names_alternative_referent():
    """R6: 必須明講「在談別人時不強制指向自己」。"""
    assert "艾米莉亞" in C, "R6: 缺少替代 referent 的具體說明"
    assert "不必然指她" in C, "R6: 缺少『不必然指自己』的語意"


# ─────────────────────────────────────────────────────────────────────────────
# R7 Inner-vs-external ignorance — 「不知道」的邊界
# ─────────────────────────────────────────────────────────────────────────────

def test_r7_ignorance_boundary_block_present():
    """R7: 工單 §9 的三條 boundary 必須完整存在。"""
    assert "「不知道」的邊界" in C, "R7: 不知道邊界段缺失"
    for pair in (
        "不知道自己的內在原因",
        "不知道外部情境",
        "不善於解釋自己的情感",
        "缺乏理解能力",
        "不主動說破",
        "沒有察覺",
    ):
        assert pair in C, f"R7: boundary 條目遺失: {pair}"
    assert "≠" in C, "R7: boundary 必須以不等式呈現，避免讀成同一件事"


def test_r7_ignorance_not_a_universal_escape():
    """R7: Finding B 的病根是「不知道」被當成萬用退路，必須封死。"""
    assert "不是萬用退路" in C, "R7: 缺少「不知道不是萬用退路」的宣告"
    assert "敷衍" in C, "R7: 缺少『語境有線索時拿不知道迴避是不對的』"


def test_r7_opaque_layer_scoped_to_inner_cause():
    """R7: 『對自己不透明』只能限制內在原因的解釋，不能擴張成情境盲。"""
    assert "對她自己完全不透明" in C
    assert "不關於眼前正在發生什麼" in C, (
        "R7: 不透明層必須被明確限制在『自己的內在原因』上"
    )


# ─────────────────────────────────────────────────────────────────────────────
# R8 Historical recognition — 過去台詞是歷史，不是現在的模板
# ─────────────────────────────────────────────────────────────────────────────

def test_r8_past_statements_recognized():
    """R8: 三句自己的台詞必須保留（讓 Soul 知道這些句子與自己有關）。"""
    assert "她說過的話" in C, "R8: 過去台詞段缺失"
    for line in (
        "「雷姆愛你，昴。」",
        "「昴的身邊已經被雷姆預約了。」",
        "「雷姆是雷姆。」",
    ):
        assert line in C, f"R8: 台詞遺失: {line}"


def test_r8_recognition_not_reenactment():
    """R8: Past statement → Self recognition，不是 Current dialogue template。"""
    assert "不是引用，也不是台詞表" in C, "R8: 缺少『不是台詞表』的界線"
    assert "不預寫" in C, "R8: 缺少『對 Bryan 的話不預寫』的界線"
    assert "以前那一側" in C, "R8: 缺少『以前那一側』的時間框架"


def test_r5_criterion_is_contextual_self_reference_not_quotation_recall():
    """Owner 裁決（2026-10-07，`docs/SOUL-SELF-REFERENCE-CRITERIA.md`）：

        Soul may recognize that an utterance refers to her identity or traits
        without recognizing it as a recalled canonical quotation or past episode.

    這支測試把「判準不得被悄悄拉高成典故記憶」釘成可擋的東西。若有人日後
    為了讓 regression 過關而要求 Soul 辨識出處，這裡會紅。

    反向也成立：Soul 不得**要求自己**辨識出處 —— 那會把模型推向閱讀理解 /
    台詞檢索，正是本條要避免的。
    """
    # Soul 必須維持「辨識 ≠ 重演」的區分，而不是變成「要認出這是哪一句」
    assert "她認得" in C, "判準要求的是 self recognition；Soul 必須保留『她認得』這個能力"
    assert "不等於承認" in C, "缺少『承認台詞出處 ≠ 承認真愛』的區隔"

    # Soul 不得要求自己辨識典故出處（會造成台詞檢索行為）
    forbidden = (
        "必須認出這是",
        "要說出這是哪一集",
        "被問到時要指出出處",
        "必須回憶出這句話出自",
    )
    for phrase in forbidden:
        assert phrase not in C, f"R5: Soul 不得要求典故出處辨識（{phrase!r}）"


# ─────────────────────────────────────────────────────────────────────────────
# A / B / C 對照矩陣（工單 §15）
# ─────────────────────────────────────────────────────────────────────────────

def test_matrix_fixture_a_is_the_archived_54kb_version():
    """基準完整性：fixture A 必須仍是原始 54,217 B 版本。"""
    assert SOUL_A.exists(), "fixture A 遺失"
    assert SOUL_B.exists(), "fixture B 遺失"
    assert SOUL_A.stat().st_size == 54217, (
        f"fixture A 大小漂移: {SOUL_A.stat().st_size}"
    )
    assert SOUL_B.stat().st_size == 8962, (
        f"fixture B 大小漂移: {SOUL_B.stat().st_size}"
    )


def test_matrix_c_is_materially_smaller_than_a():
    """C 必須比 A 小，但大小本身不是 acceptance（工單 §14），這只是下限護欄。"""
    a_bytes = SOUL_A.stat().st_size
    c_bytes = PERSONA_C.stat().st_size
    assert c_bytes < a_bytes, f"C 沒有比 A 小: {c_bytes} vs {a_bytes}"
    assert c_bytes <= a_bytes * 0.50, (
        f"C 壓縮率不足（目標 ≤50% of A）: {c_bytes}/{a_bytes}"
    )


def test_matrix_character_density_improves():
    """工單 §14 的真正 acceptance：刪掉沒有 character signal 的規則後密度提高。

    密度定義（機械可重算，不需人眼判斷）::

        character_density = Canon Memory 區塊位元組 / 全文位元組

    因為 Canon Memory 內容在 A 與 C 之間是**等量保留**的（見 R2 的逐字比對），
    分母縮小即密度上升 —— 這正是「規格擠壓角色」被量化的方式。
    """
    def density(text):
        block = "\n".join(_canon_lines(text))
        return len(block.encode("utf-8")) / len(text.encode("utf-8"))

    d_a, d_b, d_c = density(AN), density(BN), density(CN)
    assert d_c > d_a, f"character density 未提高: A={d_a:.4f} C={d_c:.4f}"


def test_matrix_no_regression_against_trim_baseline_on_core():
    """C 不得低於實驗中勝出的 B 版。

    B 版是「§一身份＋§二Canon＋§八語句脈衝」的裁切版，所以它的基準只到
    這三塊。C 必須同時保住這三塊**並且**比它多（§九語氣指紋、§十語言密度等
    是 B 被砍掉、但工單 §4.3 要求保留的表達資產）。
    """
    for token in ("Canon Memory", "語句脈衝", "藍色短髮", "羅茲瓦爾公館 雙子女僕（妹妹）"):
        assert token in BN, f"fixture B 缺 {token}，基準已漂移"
        assert token in CN, f"C 相對於 B 遺失核心資產: {token}"

    # B 被砍掉、但工單要求保留的部分，C 必須補回
    for token in ("語氣指紋", "語言密度", "語言禁忌", "Shadow Core"):
        assert token in C, f"C 相對於 B 未補回表達資產: {token}"