# tests/inner_life/test_contradiction_judge_prototype.py
"""G-3 Mock 判定器沙盒原型 —— 守衛 J1～J7（**恰 7 支**）。

工單：戰役二第三片 G-3（Mock 判定器沙盒原型）
契約：logs/CAMPAIGN-B-BATTLE2-G3-SEMANTIC-JUDGMENT-CONTRACT.md（§三／§三之二／§四／§六）
被測模組：``src/inner_life/contradiction_judge_prototype.py``

守衛總表
--------
- **J1** prompt 骨架：必然帶上既有命題與新候選命題；明文要求只回覆單一 token
  ``CONTRADICT`` 或 ``COMPATIBLE``；**不得**出現模型名／provider／URL／API key。
- **J2** 二元解析（嚴格）：去首尾空白後**完全等於**兩個 token 之一才認；
  **其餘一切**（多餘文字、兩者並列、空、非字串）⇒ ``UNDECIDED``。
- **J3** fail-closed 方向：``UNDECIDED`` 與 ``COMPATIBLE`` **同義**（都不記錄）；
  **只有** ``CONTRADICT`` 才記錄。
- **J4** 預算上限：每 agent 每日至多 1 次；超額 ⇒ **不呼叫 backend**、
  ``budget_exhausted`` 遞增、回 fail-closed。
- **J5** Mock backend 可注入：四種假造回應（``CONTRADICT``／``COMPATIBLE``／畸形／例外）
  驅動全鏈路，**全程不碰真實 LLM**。
- **J6** 零 LLM 成本（**結構性**）：AST 檢查本模組不得 import 任何
  LLM／網路／引擎／設定載入面。
- **J7** 不含預濾邏輯：AST 檢查本模組不得出現候選內容比對／跨日比對／相似度條件的痕跡
  ⇒ 預濾**留給**穩定態資料後的那一片。

紀律
----
- 本檔**不**呼叫真實 LLM、**不**連網、**不**寫 ``data/**``、**不**啟動伺服器。
- 本檔**不**使用 ``subprocess``／``os.spawn*``／``os.system``／``os.popen``（守衛 R5）。
- 守衛之間**互不耦合**：J3 只查純映射層、J5 只查 backend 注入 ⇒ 單一變異只會打紅一條。
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any, List, Optional, Set, Tuple

from src.inner_life import contradiction_judge_prototype as proto

REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO_ROOT / "src/inner_life/contradiction_judge_prototype.py"

#: J6 —— 禁止 import 的模組（完全等於或以其為前綴皆算）。
BANNED_IMPORT_MODULES: Tuple[str, ...] = (
    "src.llm",
    "httpx",
    "requests",
    "socket",
    "subprocess",
    "urllib",
    "asyncio",
    "soul_elevation",
    "configs.loader",
)

#: J7 —— 預濾痕跡的詞彙（檢查**非 docstring** 的程式碼字串與識別字）。
PREFILTER_VOCABULARY: Tuple[str, ...] = (
    "similarity",
    "similar",
    "jaccard",
    "cosine",
    "embedding",
    "levenshtein",
    "difflib",
    "sequencematcher",
    "overlap",
    "dimension",
    "prefilter",
    "pre_filter",
    "threshold",
    "crossday",
    "cross_day",
    "daysapart",
    "weight",
    "score",
    "相似",
    "維度",
    "跨日",
    "門檻",
    "預濾",
    "比對",
    "權重",
)


def _module_source() -> str:
    return MODULE_PATH.read_text(encoding="utf-8")


class _FakeBackend:
    """假造回應的 backend（**不碰任何真實 LLM**；只記錄收到的 prompt）。"""

    def __init__(self, response: str = "CONTRADICT", error: Optional[BaseException] = None) -> None:
        self.response = response
        self.error = error
        self.prompts: List[str] = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if self.error is not None:
            raise self.error
        return self.response


def _judge_once(backend: Any, existing: str, candidate: str) -> proto.JudgeOutcome:
    """用**全新的預算範圍**跑一次判定。

    J5 的四種情境各自需要一次「第一次呼叫」⇒ 每個情境都給一份新的計數器，
    否則 J4 的每日額度會把後三個情境擋掉（兩個守衛互相干擾）。
    """
    judge = proto.ContradictionJudge(
        backend, calls=proto.new_call_counter(), agent_id="agent-test"
    )
    return judge.judge(existing, candidate)


# ───────────────────────────────────────────────────────────
# J1 —— prompt 骨架
# ───────────────────────────────────────────────────────────


def test_j1_prompt_carries_both_claims_and_demands_a_single_binary_token():
    """J1 — prompt 骨架：帶上兩段命題、明文要求單一 token、無任何連線資訊洩漏。"""
    existing = "使用者習慣在雨天的午後沿河散步"
    candidate = "使用者從不外出散步"
    prompt = proto.build_judge_prompt(existing, candidate, ["source-a", "source-b"])

    # ① 必然帶上既有命題與新候選命題。
    assert existing in prompt, "prompt 必須帶上既有命題內容"
    assert candidate in prompt, "prompt 必須帶上新候選命題內容"

    # ② 明文要求只回覆單一 token。
    assert "CONTRADICT" in prompt and "COMPATIBLE" in prompt, (
        "prompt 必須明文列出兩個允許的回覆"
    )
    assert "只回覆" in prompt, "prompt 必須明文要求『只回覆』單一 token"
    assert "不得輸出任何其他文字" in prompt, "prompt 必須明文禁止任何其他輸出"

    # ③ 不得出現模型名／provider／URL／API key。
    lowered = prompt.lower()
    for banned in (
        "http://",
        "https://",
        "www.",
        "api_key",
        "api-key",
        "apikey",
        "bearer",
        "secret",
        "provider",
        "model",
        "temperature",
        "max_tokens",
        "endpoint",
        "openai",
        "anthropic",
        "claude",
        "gemini",
        "deepseek",
        "qwen",
        "llama",
        "mistral",
        "gpt",
        "sk-",
    ):
        assert banned not in lowered, f"prompt 不得出現 {banned!r}（連線／供應商資訊）"


# ───────────────────────────────────────────────────────────
# J2 —— 二元解析（嚴格）
# ───────────────────────────────────────────────────────────


def test_j2_parser_accepts_only_exact_tokens_and_never_guesses():
    """J2 — 解析器：完全相等才認；其餘一律 ``UNDECIDED``，不做任何語意推測。"""
    assert proto.parse_verdict("CONTRADICT") == "CONTRADICT"
    assert proto.parse_verdict("contradict") == "CONTRADICT"
    assert proto.parse_verdict("  CONTRADICT  ") == "CONTRADICT"
    assert proto.parse_verdict("\tContradict\n") == "CONTRADICT"
    assert proto.parse_verdict("COMPATIBLE") == "COMPATIBLE"
    assert proto.parse_verdict("compatible") == "COMPATIBLE"
    assert proto.parse_verdict("  COMPATIBLE  ") == "COMPATIBLE"

    for bad in (
        "",
        "   ",
        None,
        123,
        4.2,
        True,
        [],
        {},
        "CONTRADICT COMPATIBLE",
        "CONTRADICT, definitely",
        "I think it is CONTRADICT",
        "compatible enough",
        "compatible.",
        "不矛盾",
        "矛盾的",
        "CONTRADICT\nCOMPATIBLE",
    ):
        assert proto.parse_verdict(bad) == "UNDECIDED", (
            f"{bad!r} 必須被判為 UNDECIDED（不得推測）"
        )


# ───────────────────────────────────────────────────────────
# J3 —— fail-closed 方向
# ───────────────────────────────────────────────────────────


def test_j3_undecided_is_synonymous_with_compatible_and_only_contradict_records():
    """J3 — 方向：``UNDECIDED`` ≡ ``COMPATIBLE``（都不記錄）；只有 ``CONTRADICT`` 才記錄。

    本守衛只查**純映射層**（``decide_action``）：

    - 走 backend 的那條鏈路由 **J5** 負責（否則「backend 可注入」與「方向正確」兩個守衛
      會互相污染：任一變異都會打紅兩條）；
    - 兩條守衛合起來仍覆蓋端到端方向：J5 會斷言鏈路的 ``should_record``
      **恆等於** ``decide_action(verdict)`` 的結果。
    """
    assert proto.decide_action(proto.VERDICT_CONTRADICT) == proto.ACTION_RECORD
    assert proto.decide_action(proto.VERDICT_COMPATIBLE) == proto.ACTION_NO_RECORD
    assert proto.decide_action(proto.VERDICT_UNDECIDED) == proto.ACTION_NO_RECORD

    # 任何無法辨識的判定值，一律落到「不記錄」那一邊（fail-closed）。
    assert proto.decide_action("MAYBE") == proto.ACTION_NO_RECORD
    assert proto.decide_action("") == proto.ACTION_NO_RECORD


# ───────────────────────────────────────────────────────────
# J4 —— 預算上限
# ───────────────────────────────────────────────────────────


def test_j4_budget_allows_at_most_one_call_per_agent_per_day():
    """J4 — 每 agent 每日至多 1 次；超額 ⇒ 不呼叫 backend、遞增、回 fail-closed。"""
    calls = proto.new_call_counter()
    backend = _FakeBackend("CONTRADICT")

    first = proto.ContradictionJudge(backend, calls=calls, agent_id="agent-a")
    first_out = first.judge("既有命題甲", "候選命題乙")
    assert calls["attempted"] == 1, "第一次呼叫應記一次嘗試"

    # 同一份計數器、同一個 agent 的**第二個實例**（跨實例共用）⇒ 額度已用盡。
    second = proto.ContradictionJudge(backend, calls=calls, agent_id="agent-a")
    second_out = second.judge("既有命題甲", "候選命題乙")
    assert calls["attempted"] == 1, "額度用盡後不得再送出呼叫"
    assert calls["budget_exhausted"] == 1, "額度用盡必須被記錄"
    assert second_out.backend_invoked is False, "額度用盡 ⇒ 不得呼叫 backend"
    assert second_out.should_record is False, "額度用盡 ⇒ fail-closed（不記錄矛盾）"
    assert second_out.reason == proto.REASON_BUDGET_EXHAUSTED
    assert first_out.should_record is True, "第一次判定為 CONTRADICT ⇒ 應記錄"

    # 另一個 agent 在同一份計數器上仍有**自己的** 1 次額度（每 agent 各 1 次）。
    other = proto.ContradictionJudge(backend, calls=calls, agent_id="agent-b")
    other_out = other.judge("既有命題甲", "候選命題乙")
    assert other_out.backend_invoked is True, "另一個 agent 有自己的額度"
    assert calls["agents"]["agent-b"]["attempted"] == 1


# ───────────────────────────────────────────────────────────
# J5 —— Mock backend 可注入
# ───────────────────────────────────────────────────────────


def test_j5_mock_backend_is_injectable_and_drives_the_whole_chain():
    """J5 — 四種假造回應驅動全鏈路（全程 0 真實 LLM）。"""
    backend = _FakeBackend("CONTRADICT")

    out_contradict = _judge_once(backend, "既有命題甲", "候選命題乙")
    assert out_contradict.verdict == "CONTRADICT"

    backend.response = "COMPATIBLE"
    out_compatible = _judge_once(backend, "既有命題甲", "候選命題乙")
    assert out_compatible.verdict == "COMPATIBLE"

    # 畸形回應（**不含**兩個 token ⇒ 不受「寬鬆包含」解析器影響）。
    backend.response = "I am not sure"
    out_malformed = _judge_once(backend, "既有命題甲", "候選命題乙")
    assert out_malformed.verdict == "UNDECIDED"

    # 例外回應 ⇒ 不得冒泡，一律 fail-closed。
    backend.error = RuntimeError("backend exploded")
    out_error = _judge_once(backend, "既有命題甲", "候選命題乙")
    assert out_error.verdict == "UNDECIDED"
    assert out_error.reason == proto.REASON_BACKEND_ERROR

    # backend 真的被呼叫了 4 次（證明是**注入**進來的，不是硬編碼回應）。
    assert len(backend.prompts) == 4, "backend 必須被呼叫 4 次（回應可注入）"
    assert all(isinstance(p, str) and p for p in backend.prompts)

    # 例外路徑是**硬性** fail-closed：backend 丟例外時，不論判定層怎麼變都不記錄矛盾。
    assert out_error.should_record is False, "例外路徑必須 fail-closed（不記錄矛盾）"
    assert out_error.action == proto.ACTION_NO_RECORD

    # 鏈路一致性（**解析出判定值**的路徑）：最終動作恆等於純映射層的結果。
    # （例外路徑刻意走**硬性** fail-closed、不經過映射層 ⇒ 不列入本斷言。）
    for outcome in (out_contradict, out_compatible, out_malformed):
        assert outcome.should_record == (
            proto.decide_action(outcome.verdict) == proto.ACTION_RECORD
        )


# ───────────────────────────────────────────────────────────
# J6 —— 零 LLM 成本（結構性）
# ───────────────────────────────────────────────────────────


def _import_offenders(name: str, lineno: int) -> List[str]:
    """import 目標是否落在禁用清單（完全等於或以其為前綴皆算）。"""
    for banned in BANNED_IMPORT_MODULES:
        if name == banned or name.startswith(banned + "."):
            return [f"import:{name}@line{lineno}"]
    return []


def test_j6_module_imports_no_llm_network_engine_or_config_surface():
    """J6 — 零 LLM 成本是**結構性**保證：本模組不得 import 任何能力面。"""
    tree = ast.parse(_module_source())
    offenders: List[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                offenders.extend(_import_offenders(alias.name, node.lineno))
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            offenders.extend(_import_offenders(module, node.lineno))
            for alias in node.names:
                combined = f"{module}.{alias.name}" if module else alias.name
                offenders.extend(_import_offenders(combined, node.lineno))

    assert offenders == [], f"本模組不得具備任何 LLM／網路／引擎／設定能力：{offenders}"


# ───────────────────────────────────────────────────────────
# J7 —— 不含預濾邏輯
# ───────────────────────────────────────────────────────────


def _docstring_constant_ids(tree: ast.AST) -> Set[int]:
    """模組／函式／類別 docstring 的 Constant 節點 id（排除於詞彙掃描之外）。"""
    ids: Set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", None)
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                ids.add(id(body[0].value))
    return ids


def _code_vocabulary(tree: ast.AST) -> Tuple[List[str], List[str]]:
    """收集**非 docstring** 的字串常數與識別字（註解不在 AST 內 ⇒ 天然排除）。"""
    docstrings = _docstring_constant_ids(tree)
    strings: List[str] = []
    names: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) not in docstrings:
                strings.append(node.value)
        elif isinstance(node, ast.Name):
            names.append(node.id)
        elif isinstance(node, ast.Attribute):
            names.append(node.attr)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.append(node.name)
        elif isinstance(node, ast.arg):
            names.append(node.arg)
        elif isinstance(node, ast.keyword) and node.arg:
            names.append(node.arg)
    return strings, names


def _claim_content_offenders(tree: ast.AST) -> List[str]:
    """命題內容**不得**被拿來做任何比對／加工（預濾的施工位置）。

    允許的只有「原樣傳遞給 prompt 骨架」；其餘（比較、字串手術、切片、方法呼叫）皆違規。
    """
    claim_names: Set[str] = {
        node.arg
        for node in ast.walk(tree)
        if isinstance(node, ast.arg) and "claim" in node.arg.lower()
    }
    # 別名傳遞：把命題內容指派給別的名字再加工，一樣不行。
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Name)
            and node.value.id in claim_names
        ):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    claim_names.add(target.id)

    pass_through = {proto.build_judge_prompt.__name__}
    offenders: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            if any(isinstance(x, ast.Name) and x.id in claim_names for x in ast.walk(node)):
                offenders.append(f"compare@line{node.lineno}")
        elif isinstance(node, ast.Call):
            callee: Optional[str] = None
            if isinstance(node.func, ast.Name):
                callee = node.func.id
            elif isinstance(node.func, ast.Attribute):
                callee = node.func.attr
            if callee not in pass_through:
                args = list(node.args) + [kw.value for kw in node.keywords]
                for arg in args:
                    if isinstance(arg, ast.Name) and arg.id in claim_names:
                        offenders.append(f"call-arg:{callee}@line{node.lineno}")
        elif isinstance(node, ast.BinOp):
            if any(isinstance(x, ast.Name) and x.id in claim_names for x in ast.walk(node)):
                offenders.append(f"binop@line{node.lineno}")
        elif isinstance(node, ast.Attribute):
            if isinstance(node.value, ast.Name) and node.value.id in claim_names:
                offenders.append(f"attribute:{node.attr}@line{node.lineno}")
        elif isinstance(node, ast.Subscript):
            if any(isinstance(x, ast.Name) and x.id in claim_names for x in ast.walk(node)):
                offenders.append(f"subscript@line{node.lineno}")
    return offenders


def test_j7_module_contains_no_prefilter_logic():
    """J7 — 預濾留給穩定態資料後的那一片：本模組不得含任何預濾痕跡。"""
    tree = ast.parse(_module_source())
    strings, names = _code_vocabulary(tree)

    offenders: List[str] = []
    for text in strings + names:
        lowered = text.lower()
        for word in PREFILTER_VOCABULARY:
            if word in lowered:
                offenders.append(f"{word!r}∈{text!r}")
    assert offenders == [], f"本模組不得含預濾痕跡（預濾留給穩定態資料後的那一片）：{offenders}"

    assert _claim_content_offenders(tree) == [], "命題內容不得被拿來比較或加工"

    floats = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, float)
    ]
    assert floats == [], f"本模組不得出現浮點常數（那是條件門檻的形狀）：{floats}"
