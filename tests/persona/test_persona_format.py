"""tests/persona/test_persona_format.py — LINT-CHORE-34 persona 排版守衛。

工單：``logs/LINT-CHORE-34-IMPLEMENTATION.md``
工具：``scripts/normalize_persona_format.py``（本檔以 importlib 載入，避免動到
``scripts/`` 的套件結構——``scripts/`` 沒有 ``__init__.py``，硬塞 ``sys.path``
會把 repo 根的路徑順序弄亂）。

── 這張票在守什麼 ───────────────────────────────────────────────
Notion 的項目符號清單在抓取時被壓成**行內** `` • ``（U+2022 + 半形空白）分隔序列：
一個段落裡原本多個獨立條目被併成一行。LINT-CHORE-34 把那 34 份既有 persona 的
排版拆回可讀的 markdown，**語意零改動**。

本檔的守衛因此有兩層：
  1. **形狀守衛**——檔案上不再有行內 `` • `` 分隔序列。
  2. **語意守衛**——正規化之後，人與 SSOT 素材（``docs/persona-drafts/``）**逐位元相同**。
     這是「只動了排版、沒動語意」的機器證明。

── 覆蓋範圍（重要）──────────────────────────────────────────────
* 覆蓋**既有 34 份**：10 份原始角色（無素材檔）＋ 24 份 batch1。
* **不覆蓋 12 隻新 id**（``beatrice``…``yotsuba``）——另一張工單正在建立它們，
  本票不得套用、不得驗證、不得干擾。
* 覆蓋清單是**寫死的 34 筆**，不是 glob——因為 ``personas/`` 會隨另一張工單變長，
  glob 會讓「守了幾份」這件事隨時間漂移。

紅線：本檔不啟動任何行程、不綁任何埠、不寫 ``data/**``、不動 ``personas/``。
"""
from __future__ import annotations

import hashlib
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
TOOL_PATH = REPO_ROOT / "scripts" / "normalize_persona_format.py"
PERSONAS_DIR = REPO_ROOT / "personas"
DRAFTS_DIR = REPO_ROOT / "docs" / "persona-drafts"


def _load_tool() -> ModuleType:
    """以 importlib 載入正規化工具（``scripts/`` 不是套件）。"""
    spec = importlib.util.spec_from_file_location(
        "soulos_normalize_persona_format", TOOL_PATH
    )
    assert spec is not None and spec.loader is not None, (
        f"無法載入正規化工具：{TOOL_PATH}"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


npf = _load_tool()

# ─────────────────────────────────────────────────────────────
# 覆蓋清單（寫死，不 glob）
# ─────────────────────────────────────────────────────────────

#: 既有 10 隻原始角色——**沒有** ``docs/persona-drafts/`` 素材檔。
ORIGINAL_10 = (
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

#: LAND-0 batch1 的 24 隻——每隻都有 SSOT 素材檔可對帳。
BATCH1_24 = (
    "agent_ai",
    "agent_asuna",
    "agent_fuuka",
    "agent_haruhi",
    "agent_hiro",
    "agent_iroha",
    "agent_kaguya",
    "agent_kana",
    "agent_kaori",
    "agent_kirino",
    "agent_kurisu",
    "agent_makima",
    "agent_mashiro",
    "agent_minami",
    "agent_mochizuki_ai",
    "agent_reze",
    "agent_rin",
    "agent_rio",
    "agent_sachi",
    "agent_sakura",
    "agent_shea",
    "agent_shizuku",
    "agent_tsukasa",
    "agent_yue",
)

#: 本守衛覆蓋的全部 34 份。
COVERED_34 = tuple(ORIGINAL_10) + tuple(BATCH1_24)

#: 另一張工單正在建立的 12 隻——本票**不套用、不驗證**。
BATCH2_12 = (
    "agent_beatrice",
    "agent_chizuru",
    "agent_emilia",
    "agent_ichika",
    "agent_itsuki",
    "agent_mami",
    "agent_nagisa",
    "agent_nino",
    "agent_rika",
    "agent_saki",
    "agent_shino",
    "agent_yotsuba",
)

#: 既有 10 隻在 LAND-0 落地時的 sha256（逐字取自
#: ``tests/agent/test_agent_registry_land0_batch1.py`` 的 ``EXISTING_10_SHA256``）。
#: LINT-CHORE-34 對這 10 份是**位元級 no-op**（它們本來就沒有行內分隔序列），
#: 因此這組雜湊就是「語意未被改動」的機器證據。
ORIGINAL_10_SHA256 = {
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

SHADOW_HEADING = "## 陰影條款"


# ─────────────────────────────────────────────────────────────
# 小工具
# ─────────────────────────────────────────────────────────────


def _read(path: Path) -> str:
    return path.read_bytes().decode("utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def strip_shadow_section(text: str) -> str:
    """把 ``## 陰影條款`` 段（自該標題起至檔尾）剝掉。

    陰影條款是 Owner 2026-10-04 裁定、**逐字不得改動**的靈魂層；把它從兩側
    都剝掉，剩下的才是「素材主體」，可以拿來逐位元比對。
    """
    out = []
    for line in text.splitlines(keepends=True):
        if line.startswith(SHADOW_HEADING):
            break
        out.append(line)
    return "".join(out)


def _protected_lines(text: str):
    """yield (lineno, line)：依正規化工具同一套規則，列出**不得拆分**的行。"""
    in_fence = False
    fence_char = ""
    fence_len = 0
    for lineno, body in enumerate(text.splitlines(), 1):
        fence = npf._FENCE_RE.match(body)
        if fence is not None:
            marker = fence.group(1)
            if not in_fence:
                in_fence, fence_char, fence_len = True, marker[0], len(marker)
            elif (
                marker[0] == fence_char
                and len(marker) >= fence_len
                and not fence.group(2).strip()
            ):
                in_fence, fence_char, fence_len = False, "", 0
            yield lineno, body
            continue
        if (
            in_fence
            or npf._TABLE_RE.match(body)
            or npf._INDENT_RE.match(body)
        ):
            yield lineno, body


# ─────────────────────────────────────────────────────────────
# 守衛 0：覆蓋率（防止「只守了 33 份」）
# ─────────────────────────────────────────────────────────────


def test_guard0_coverage_is_exactly_the_34_existing_personas():
    """本守衛必須恰好覆蓋既有 34 份——少守一份就是靜默的漏網。"""
    assert len(COVERED_34) == 34, f"覆蓋清單應 34 筆，實際 {len(COVERED_34)}"
    assert len(set(COVERED_34)) == 34, "覆蓋清單有重複 id"
    assert set(COVERED_34) == set(ORIGINAL_10) | set(BATCH1_24), (
        "覆蓋清單與 10 ＋ 24 的組成不一致"
    )
    for agent_id in COVERED_34:
        path = PERSONAS_DIR / f"{agent_id}.md"
        assert path.is_file(), f"persona 檔不存在：{path}"


def test_guard0_batch2_ids_are_excluded_from_this_guard():
    """12 隻新 id 不在本守衛的覆蓋範圍內（另一張工單的資產，本票不碰）。"""
    assert not (set(COVERED_34) & set(BATCH2_12)), (
        "12 隻新 id 不得進入本守衛的覆蓋清單"
    )


# ─────────────────────────────────────────────────────────────
# 守衛 1：形狀——檔案上已無行內分隔序列
# ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("agent_id", COVERED_34)
def test_guard1_persona_has_no_inline_bullet_separator(agent_id):
    """34 份 persona 全部不得再有行內 `` • `` 分隔序列（受保護區塊除外）。"""
    text = _read(PERSONAS_DIR / f"{agent_id}.md")
    protected = {lineno for lineno, _ in _protected_lines(text)}
    offenders = []
    for lineno, body in enumerate(text.splitlines(), 1):
        if lineno in protected:
            continue
        if npf.SEPARATOR_RE.search(body):
            offenders.append((lineno, body[:120]))
    assert not offenders, (
        f"{agent_id}: 仍有行內 ' • ' 分隔序列（{len(offenders)} 行）：{offenders[:5]}"
    )


@pytest.mark.parametrize("agent_id", COVERED_34)
def test_guard1_persona_file_is_already_normalized(agent_id):
    """磁碟上的檔案必須**已經是**正規化後的樣子（normalize(text) == text）。"""
    text = _read(PERSONAS_DIR / f"{agent_id}.md")
    normalized = npf.normalize_markdown(text)
    assert normalized == text, (
        f"{agent_id}: 檔案尚未正規化（normalize(text) != text）"
    )


# ─────────────────────────────────────────────────────────────
# 守衛 2：語意等價（正規化後逐位元相同）
# ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("agent_id", BATCH1_24)
def test_guard2_semantic_equivalence_to_sssot_draft(agent_id):
    """正規化後，人與 SSOT 素材（剝掉陰影條款後）**逐位元相同**。

    這是「只動了排版、沒動語意」的機器證明：兩邊都跑同一個正規化，
    結果必須完全一樣——差一個字就代表語意被動過。
    """
    draft_path = DRAFTS_DIR / f"{agent_id}.md"
    persona_path = PERSONAS_DIR / f"{agent_id}.md"
    assert draft_path.is_file(), f"SSOT 素材缺檔：{draft_path}"

    persona = npf.normalize_markdown(_read(persona_path))
    draft = npf.normalize_markdown(_read(draft_path))

    assert strip_shadow_section(persona) == strip_shadow_section(draft), (
        f"{agent_id}: 正規化後與 SSOT 素材不逐位元相同（語意等價斷言失敗）"
    )


@pytest.mark.parametrize("agent_id", ORIGINAL_10)
def test_guard2_existing_10_are_byte_identical_to_land0_baseline(agent_id):
    """既有 10 隻沒有素材檔可對帳——用 LAND-0 落地的 sha256 當語意證據。

    這 10 份本來就沒有行內分隔序列，正規化對它們是**位元級 no-op**；
    雜湊不變 ⇔ 語意一個字都沒動。
    """
    path = PERSONAS_DIR / f"{agent_id}.md"
    actual = _sha256(path)
    expected = ORIGINAL_10_SHA256[agent_id]
    assert actual == expected, (
        f"{agent_id}: persona 位元已被改動（sha256 {actual} != {expected}）"
    )


# ─────────────────────────────────────────────────────────────
# 守衛 3：冪等
# ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("agent_id", COVERED_34)
def test_guard3_normalization_is_idempotent_on_the_file(agent_id):
    """對檔案套用兩次，第二次無變更。"""
    text = _read(PERSONAS_DIR / f"{agent_id}.md")
    once = npf.normalize_markdown(text)
    twice = npf.normalize_markdown(once)
    assert twice == once, f"{agent_id}: 正規化函式不冪等（normalize(normalize(x)) != normalize(x)）"


# ─────────────────────────────────────────────────────────────
# 守衛 4：規則本身的單元性質（合成樣本，不碰 repo 檔案）
# ─────────────────────────────────────────────────────────────


def test_guard4_rule_keeps_the_head_verbatim_and_prefixes_split_items():
    """行首那段（非分隔內容）逐字保留、不加前綴；被拆出來的每一段前綴 ``- ``。"""
    assert npf.normalize_markdown("前導句 • 甲 • 乙") == "前導句\n- 甲\n- 乙"
    assert npf.normalize_markdown("- 甲 • 乙") == "- 甲\n- 乙"
    # 「BULLET 前無空白」與「BULLET 前有空白」同樣是分隔序列
    assert npf.normalize_markdown("引導：• 甲") == "引導：\n- 甲"
    assert npf.normalize_markdown("引導： • 甲") == "引導：\n- 甲"


def test_guard4_rule_preserves_item_text_verbatim():
    """每一段的文字逐字保留（標點、日文、全形空白、數字都不動）。"""
    item = "「いやでちゅね〰」（偶像的音符）——0.35——全形空白　保留"
    out = npf.normalize_markdown(f"引導 • {item}")
    assert out == f"引導\n- {item}"


def test_guard4_rule_does_not_split_tables_or_code_blocks():
    """表格列、fenced code block、縮排區塊內的 `` • `` 不拆。"""
    table = "| Agent | 位置 |\n| --- | --- |\n| Ai | 偶像 • 本體 |"
    assert npf.normalize_markdown(table) == table

    fenced = "```\nkey • value\n```"
    assert npf.normalize_markdown(fenced) == fenced

    indented = "段落\n\n    key • value\n"
    assert npf.normalize_markdown(indented) == indented


def test_guard4_rule_preserves_line_endings_and_trailing_newline():
    """CRLF / LF 慣例與檔尾換行都必須原樣保留。"""
    crlf = "第一行\r\n第二行 • 甲\r\n"
    assert npf.normalize_markdown(crlf) == "第一行\r\n第二行\r\n- 甲\r\n"

    lf = "第一行\n第二行 • 甲\n"
    assert npf.normalize_markdown(lf) == "第一行\n第二行\n- 甲\n"

    no_trailing = "第一行\n第二行 • 甲"
    assert npf.normalize_markdown(no_trailing) == "第一行\n第二行\n- 甲"


def test_guard4_rule_is_idempotent_on_synthetic_samples():
    """合成樣本上的冪等性（守住工具本身，不只守住磁碟上的檔案）。"""
    samples = [
        "前導句 • 甲 • 乙",
        "- 甲 • 乙",
        "引導：• 甲",
        "| a | b • c |",
        "```\nx • y\n```",
        "段落\n\n    x • y\n",
        "沒有分隔符",
        "",
    ]
    for sample in samples:
        once = npf.normalize_markdown(sample)
        assert npf.normalize_markdown(once) == once, f"樣本不冪等：{sample!r}"


# ─────────────────────────────────────────────────────────────
# 守衛 5：寫入安全（不可漂移的守門條件）
# ─────────────────────────────────────────────────────────────


def test_guard5_write_guard_refuses_production_data_path(tmp_path):
    """``data/`` 是生產狀態——工具必須拒絕寫入。"""
    target = tmp_path / "data" / "elevation" / "trace.jsonl"
    target.parent.mkdir(parents=True)
    target.write_text("x • y\n", encoding="utf-8")
    with pytest.raises(PermissionError):
        npf.assert_write_allowed(target)


def test_guard5_write_guard_refuses_read_only_drafts_tree(tmp_path):
    """``docs/persona-drafts/`` 是唯讀 SSOT——工具必須拒絕寫入。"""
    target = tmp_path / "docs" / "persona-drafts" / "agent_x.md"
    target.parent.mkdir(parents=True)
    target.write_text("x • y\n", encoding="utf-8")
    with pytest.raises(PermissionError):
        npf.assert_write_allowed(target)


def test_guard5_write_guard_allows_ordinary_paths(tmp_path):
    """一般的 persona 路徑必須**可以**寫（守門條件不得把所有寫入都擋掉）。"""
    target = tmp_path / "personas" / "agent_x.md"
    target.parent.mkdir(parents=True)
    npf.assert_write_allowed(target)  # 不應拋出


def test_guard5_cli_has_no_built_in_default_paths():
    """CLI 不得內建任何預設路徑——檔案必須由呼叫端逐檔指名。

    這是 2026-10-19 事故的教訓：任何「只在特定參數下才會寫入」的開關，
    其守門條件都必須不可漂移。本工具的正解是**沒有預設值**。
    """
    parser = npf.build_parser()
    args = parser.parse_args(["a.md", "b.md"])
    assert args.paths == ["a.md", "b.md"]
    assert args.in_place is False
    assert args.check is False

    # 沒有 positional 參數 → argparse 直接報錯（exit 2），不會掃任何目錄。
    with pytest.raises(SystemExit):
        npf.main([])


def test_guard5_cli_in_place_only_touches_named_files(tmp_path):
    """``--in-place`` 只作用於指名的那幾個檔案；未指名的鄰居檔案不被動。"""
    named = tmp_path / "named.md"
    named.write_text("前導 • 甲\n", encoding="utf-8")
    bystander = tmp_path / "bystander.md"
    bystander.write_text("前導 • 乙\n", encoding="utf-8")
    before = bystander.read_bytes()

    exit_code = npf.main(["--in-place", "--quiet", str(named)])
    assert exit_code == 0
    assert named.read_text(encoding="utf-8") == "前導\n- 甲\n"
    assert bystander.read_bytes() == before, "未指名的檔案被動了"


if __name__ == "__main__":  # pragma: no cover - 手動執行用
    sys.exit(pytest.main([__file__, "-v"]))
