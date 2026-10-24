# tests/inner_life/test_campaign_b_e2e_closed_loop.py
"""CAMPAIGN-B 全鏈路確定性 E2E 封頂（Capstone E2E）—— 守衛 E2E-T1～E2E-T8。

工單：``logs/CAMPAIGN-B-E2E-CLOSED-LOOP-WORK-ORDER.md``（主大腦開立 2026-10-24）。

本檔是**戰役一的封頂整合守衛**：用一個純離線、零網路、秒級的確定性場景，
把整條鏈路從頭跑到尾釘死：

    產生 D1 資訊缺口張力（沙盒）
      → ``drive_loop_async()``（Phase 2 的決定性 MockThinker；0 真實 LLM）
      → ``working_state.jsonl`` 落地（沙盒）
      → ``proxy.py`` 讀取並注入到 Prompt（group ＋ private 兩條路徑）

🔴 **本票證明的是「鏈路在沙盒中閉環」**（含逐位元一致性與確定性）。
🔴 **生產資料面仍未通**：``SOUL_OS_TENSION_DRIVE_LOOP`` 在生產缺省 OFF
   ⇒ ``data/inner_life/working_state.jsonl`` 在生產**仍不存在**
   ⇒「角色已能感知思考狀態」在生產**仍不成立**。
   （真實 LLM 路徑已由 P5-② 以 1 次受限呼叫驗證；兩者互補，不可混為一談。）

邊界（工單 §2.5）：
  * **只新增本檔**；不改 ``src/**``、不改既有守衛檔。
    ⚠️ FEAT-ISOLATED-DATA-ROOT-FACTORY（2026-10-24）之後**本檔已被改寫**：
    沙盒隔離改用 ``src.paths.isolated_data_root()``（單一入口），**不再**手動
    monkeypatch 10 個 import 期資料根常數 ⇒ 覆寫數 **10 → 0**。
    （``src/**`` 的改動只限 ``src/paths.py`` 的工廠本體。）
  * 0 subprocess / 0 os.spawn* / 0 os.system / 0 os.popen（R5 護欄）。
  * 0 啟動伺服器 / 0 綁 :8000 / :8765-8767 / 0 對生產發真實請求。
  * 0 真實 LLM 呼叫 / 0 外部 API / 0 網路。
  * 0 寫入 ``data/**``（本檔只寫 pytest 的 tmp 目錄）。
"""
from __future__ import annotations

import ast
import asyncio
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.append(str(REPO_ROOT))

import src.paths as paths  # noqa: E402
from src.inner_life import tension_closure_loop as tcl  # noqa: E402
from src.inner_life import tension_thinking as tt  # noqa: E402
from src.llm import proxy  # noqa: E402
from src.paths import isolated_data_root  # noqa: E402

# ── 測試常量（固定值 ⇒ 零 wall-clock 相依）───────────────────────────────
#: 被驅動的角色（Phase 1 的驅動剖面只解析得出 ``agent_akane``）。
TEST_AGENT_ID = "agent_akane"

#: 沙盒假時鐘（epoch 秒）；與 Phase 1～4 守衛檔同值。
FAKE_NOW = 1_700_000_000  # 2023-11-14T22:13:20Z

#: Prompt 組裝的固定輸入（與 golden 產生時同形）。
TEST_SOUL = "SOUL-E2E-BODY"
TEST_MEMORY_CONTEXT = "MEMORY-E2E-CONTEXT"
TEST_WORLD_CONTEXT = "WORLD-E2E-CONTEXT"
TEST_CURRENT_INPUT = "hello"

#: Prompt 內的區塊標記。
THINKING_STATE_MARKER = "[思考狀態]"
INNER_LIFE_MARKER = "[最近內在生活]"

#: 本票期望的 working state 唯讀行（**格式釘死**；日期取 ``FAKE_NOW`` 的 UTC 日）。
EXPECTED_WORKING_STATE_LINE = "- [2023-11-14 張力未解] 還有 1 個未解的問題（已想過 50%）"

#: 既有 Phase 4 守衛檔（70 筆）的 EOL 正規化 sha256 與測試函式數。
#:
#: 🔺 與 P5-① 守衛檔（``test_p5_working_state_context.py``）用**同款**基準：
#: 以**換行正規化**後的文字取雜湊（不用工作副本原始位元組）⇒ 換 checkout 不會假紅。
PHASE4_GUARD_FILE = "tests/inner_life/test_tension_closure_loop.py"
PHASE4_GUARD_SHA256 = "21e768c12d59ea90222f6a65993f85ab88b88f1219efdceb56db6efe70b02540"
PHASE4_GUARD_TEST_FUNC_COUNT = 70

#: 網路／外部 API 禁令（AST 層）。
#:
#: 🔺 **只掃本檔與「零網路段」的模組**：``src/llm/proxy.py`` 在**模組層** import
#: ``httpx``（那是真實 LLM 呼叫路徑，本票不碰）；故對 proxy 的斷言只針對
#: **新增的那個區塊函式** ``_format_working_state_block``（純讀，fail-silent）。
BANNED_NETWORK_IMPORTS = frozenset(
    {
        "requests", "httpx", "urllib", "urllib3", "aiohttp", "socket",
        "http", "http.client", "openai", "anthropic", "google", "cohere",
        "mistralai", "litellm", "llm_call",
    }
)

#: 本票會讀到的「零網路段」模組（AST 層逐一掃描）。
CHAIN_MODULES = (
    "src/inner_life/tension_closure_loop.py",
    "src/inner_life/tension_thinking.py",
    "src/inner_life/tension_scoring.py",
    "src/inner_life/drive_profile.py",
    "src/inner_life/epistemic_action.py",
)

#: 沙盒線頭：2 筆資訊缺口（其一同時帶矛盾欄位）——**與生產事件同形**。
#:
#: 🔺 欄位形狀沿用 Phase 1 守衛檔（``test_tension_closure_loop.py``）的種子：
#: 不得自造張力格式（工單 §2.3）。
SANDBOX_LIFE_THREAD_ROWS: List[Dict[str, Any]] = [
    {
        "thread_id": "gap-1",
        "status": "active",
        "sage_fact_id": None,
        "title": "缺口一",
        "updated_at": "2023-11-13T14:00:00+00:00",
    },
    {
        "thread_id": "gap-2",
        "status": "active",
        "sage_fact_id": None,
        "title": "缺口二",
        "updated_at": "2023-11-12T14:00:00+00:00",
        "contradiction": "與舊推論衝突",
    },
]


# ─────────────────────────────────────────────────────────────────────────
# 沙盒隔離（工單 §2.2：**決定性前提**）
# ─────────────────────────────────────────────────────────────────────────
class _Sandbox:
    """本次測試宣告的沙盒資料面。"""

    def __init__(self, data_root: Path, store_dir: Path, mp: pytest.MonkeyPatch) -> None:
        self.data_root = data_root
        self.store_dir = store_dir
        self.mp = mp


@pytest.fixture
def sandbox(tmp_path: Path) -> _Sandbox:
    """把整條鏈路的資料面指向**本次測試的沙盒**（0 生產讀、0 生產寫）。

    🔺 **單一入口**（FEAT-ISOLATED-DATA-ROOT-FACTORY）：本檔**不再**手動
    monkeypatch 任何「import 期算死」的資料根常數 ⇒ 改用
    :func:`src.paths.isolated_data_root`（受控隔離環境上下文工廠）。

    工廠一次就替換掉**盤點清單裡的全部 12 個** import 期資料根常數
    （單一事實來源：``src/paths.py::DERIVED_DATA_ROOT_CONSTANTS``）：

    ===========================  ==============================  ==========================
    模組                         屬性                            相對於資料根的子路徑
    ===========================  ==============================  ==========================
    ``src.agent.emotion``        ``DB_PATH``                    ``memory.db``
    ``src.io.channels.router``   ``_STATE_DIR``                 ``state``
    ``src.io.channels.router``   ``_LAST_TG_USER_FILE``         ``state/last_tg_user.json``
    ``src.io.channels.router``   ``_BRYAN_LAST_SEEN_FILE``      ``state/bryan_last_seen.json``
    ``src.io.channels.router``   ``_OUTBOX_FILE``               ``state/outbox.json``
    ``src.llm.proxy``            ``CONV_DIR``                   ``conversations``
    ``src.llm.proxy``            ``INNER_LIFE_DATA_DIR``        ``soul``
    ``src.llm.proxy``            ``_GROUP_FILE``                ``conversations/group_chat.json``
    ``src.llm.tts_toggle``       ``_STATE_DIR``                 ``state``
    ``src.llm.tts_toggle``       ``_STATE_FILE``                ``state/tts_toggle.json``
    ``src.memory.store``         ``DB_PATH``                    ``memory.db``
    ``src.soul.diary``           ``DEFAULT_DIARY_ROOT``         ``soul``
    ===========================  ==============================  ==========================

    🔺 工廠另外把「**預設參數**捕捉」一併換掉（``MemoryStore.__init__`` 的
    ``db_path``／``EmotionEngine.__init__`` 的 ``db_path``／
    ``DiaryWriter.__init__`` 的 ``data_dir``）—— 那些值在 import 期就綁進簽章，
    只換模組屬性**不夠**。

    🔺 **生產守門語意不變**：隔離期間 ``check_store_dir()`` 仍拿**真實生產根**
    比對（工廠把 ``tension_closure_loop._production_data_root`` 指向
    ``src.paths.production_data_root()``）⇒ 若不這樣做，守門會「拿沙盒跟自己比」
    ⇒ **語意反轉**（沙盒也會被拒絕）；覆寫後生產路徑**照樣被擋**
    （E2E-T6 有斷言釘死這件事）。

    🔺 **旗標**：``drive_loop_async()`` 的**第一道**閘門是 ``feature_enabled()``；
    OFF ⇒ 回 ``None``、0 檔案 ⇒ **鏈路不可能閉環**。故本檔在**測試行程內**
    開啟 ``SOUL_OS_TENSION_DRIVE_LOOP``。這**不改變生產行為**：生產服務是
    **另一個行程、另一份環境**，本檔用 ``pytest.MonkeyPatch``（測試結束自動
    還原），且旗標在**程式碼**與**生產 .env** 裡仍缺省 OFF（E2E-T8 釘死）。
    既有 Phase 1～4 守衛檔本來就用同一個做法（``enabled`` fixture）。
    """
    data_root = tmp_path / "data"
    store_dir = data_root / "inner_life"
    store_dir.mkdir(parents=True, exist_ok=True)

    # 用**自建** ``pytest.MonkeyPatch()``（而非 ``monkeypatch`` fixture），
    # 與既有 P5-① 守衛檔同款做法：不佔用 ``monkeypatch`` fixture ⇒ 不改變
    # 既有 fixture 的 setup/teardown 順序。
    mp = pytest.MonkeyPatch()
    mp.setenv(tcl.FEATURE_ENV, "1")
    try:
        # 🔺 唯一動作：把**所有**資料根衍生常數統一置換到沙盒，退出時必還原。
        with isolated_data_root(data_root):
            yield _Sandbox(data_root=data_root, store_dir=store_dir, mp=mp)
    finally:
        mp.undo()


# ─────────────────────────────────────────────────────────────────────────
# 共用 helper
# ─────────────────────────────────────────────────────────────────────────
class _StubMemory:
    """兩個 builder 只碰這兩個 method（與 P5-① 守衛檔同款）。"""

    def get_group_history(self, limit: int = 20):
        return [
            {
                "speaker": "agent_other",
                "content": "群聊歷史一則",
                "is_private": False,
            }
        ]

    def get_recent_with_meta(self, session_id: str, limit: int = 20):
        return []


class _AsyncDeterministicMockThinker:
    """把 Phase 2 的**決定性 MockThinker**包成 async ``think()``。

    🔺 ``drive_loop_async()`` **只接受 async thinker**（收到同步 thinker 會明確
    ``raise TypeError``）⇒ 這裡只做**形狀轉接**，**決策邏輯完全是 Phase 2 的
    ``DeterministicMockThinker``**（0 LLM、0 網路、可重現）。

    🔺 ``calls`` 是「MockThinker 被呼叫幾次」的計數器（E2E-T5 的零成本證據）。
    """

    def __init__(self) -> None:
        self.inner = tt.DeterministicMockThinker()
        self.calls = 0
        self.prompts: List[str] = []

    async def think(self, req: Any, prompt: str) -> Any:
        self.calls += 1
        self.prompts.append(prompt)
        return self.inner.think(req, prompt)


def _iso(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


def _seed_d1_tension(sandbox: _Sandbox) -> None:
    """在**沙盒資料根**底下播種 D1 資訊缺口（線頭檔 ＋ 關係檔）。

    🔺 種子形狀沿用 Phase 1 守衛檔（``_seed_sandbox_data``）⇒ **不自造張力格式**。
    🔺 路徑取 ``paths.data_root()``（**呼叫期**）⇒ 與評分路徑
    （``tension_scoring`` 同樣走 ``data_root()``）**同一份資料根**。
    """
    agent_dir = paths.data_root() / "soul" / TEST_AGENT_ID
    agent_dir.mkdir(parents=True, exist_ok=True)
    rows_out: List[Dict[str, Any]] = []
    for row in SANDBOX_LIFE_THREAD_ROWS:
        item = dict(row)
        item.setdefault("created_at", item["updated_at"])
        item.setdefault("dissolved_at", None)
        rows_out.append(item)
    (agent_dir / "life_threads.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows_out) + "\n",
        encoding="utf-8",
    )
    relationships = {
        "agent_id": TEST_AGENT_ID,
        "schema_version": 1,
        "others": {
            "user_bryan": {
                "impression_tags": ["sandbox"],
                "relational_band": "familiar",
                "interaction_count": 42,
                "last_interaction_at": _iso(FAKE_NOW - 30 * 3600),
            }
        },
    }
    (agent_dir / "relationships.json").write_text(
        json.dumps(relationships, ensure_ascii=False), encoding="utf-8"
    )


def _seed_diary(sandbox: _Sandbox) -> str:
    """寫一則 diary（slot=dream / source=llm）讓 ``[最近內在生活]`` 區塊非空。

    ⇒ 注入**位置**（E2E-T2）才有前置錨點可比。

    🔺 日期取**當日**（proxy 的 ``_format_recent_inner_life`` 讀最近 3 天）。
    """
    date_str = datetime.now().strftime("%Y-%m-%d")
    diary_dir = paths.data_root() / "soul" / TEST_AGENT_ID / "diary"
    diary_dir.mkdir(parents=True, exist_ok=True)
    entry = {"slot": "dream", "source": "llm", "content": "夢到一條很長的河。"}
    (diary_dir / f"{date_str}.jsonl").write_text(
        json.dumps(entry, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return date_str


def _drive(
    sandbox: _Sandbox, thinker: Optional[_AsyncDeterministicMockThinker] = None
) -> Tuple[Any, _AsyncDeterministicMockThinker]:
    """跑一次 async 思考閉環（沙盒）⇒ 回傳 ``(trace, thinker)``。"""
    active = thinker if thinker is not None else _AsyncDeterministicMockThinker()
    trace = asyncio.run(
        tcl.drive_loop_async(
            TEST_AGENT_ID, FAKE_NOW, sandbox.store_dir, thinker=active
        )
    )
    return trace, active


def _assemble() -> Tuple[List[Dict[str, str]], List[Dict[str, str]]]:
    """組出 group 與 private 兩條路徑的 messages（與 P5-① 守衛檔同款輸入）。"""
    kwargs = dict(
        soul=TEST_SOUL,
        current_input=TEST_CURRENT_INPUT,
        memory_context=TEST_MEMORY_CONTEXT,
        memory=_StubMemory(),
        mood=0.0,
        user_id="bryan",
        current_time="",
        event_ts=None,
        bry_latest_ts=0,
        world_context=TEST_WORLD_CONTEXT,
        last_interaction_ts=0,
    )
    group = proxy._build_messages_group(TEST_AGENT_ID, **kwargs)
    private = proxy._build_messages_private(TEST_AGENT_ID, **kwargs)
    return group, private


def _system_content(messages: List[Dict[str, str]]) -> str:
    return messages[0]["content"]


def _digest(messages: List[Dict[str, str]]) -> Tuple[str, int]:
    blob = json.dumps(messages, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(blob).hexdigest(), len(blob)


def _thinking_state_block(system_content: str) -> str:
    """取出 ``[思考狀態]`` 區塊全文（標題行 ＋ 正文；不含尾端空行）。"""
    idx = system_content.find(THINKING_STATE_MARKER)
    assert idx >= 0, "system prompt 內缺少 [思考狀態] 區塊"
    after = system_content[idx:]
    end = after.find("\n\n")
    if end < 0:
        return after.rstrip("\n")
    return after[:end]


def _thinking_state_body(system_content: str) -> str:
    """取出 ``[思考狀態]`` 區塊的**正文**（逐位元）。"""
    lines = _thinking_state_block(system_content).splitlines()
    assert len(lines) >= 2, "區塊應至少含標題行 ＋ 正文一行"
    return "\n".join(lines[1:])


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.is_file():
        return []
    rows: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if text:
            rows.append(json.loads(text))
    return rows


def _file_set(root: Path) -> List[str]:
    """目錄下所有檔案的相對路徑（排序；目錄不存在 ⇒ 空 list）。"""
    if not root.is_dir():
        return []
    return sorted(
        p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()
    )


def _imported_roots(path: Path) -> set:
    """檔內所有 import 的**根**模組名（含 ``from X import Y`` 的 ``X``）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                continue
            roots.add((node.module or "").split(".")[0])
    return roots


def _function_node(path: Path, name: str) -> ast.FunctionDef:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == name
        ):
            return node
    raise AssertionError(f"{path} 內找不到函式 {name}")


# ═══════════════════════════════════════════════════════════════════════
# E2E-T1：全鏈路閉環（張力 → drive_loop_async → working_state → prompt）
# ═══════════════════════════════════════════════════════════════════════
def test_e2e_t1_full_chain_closes_in_sandbox(sandbox: _Sandbox) -> None:
    """D1 張力 → async 閉環 → working_state 落地 → 代理 prompt 含正確的思考狀態行。"""
    _seed_d1_tension(sandbox)
    _seed_diary(sandbox)

    trace, thinker = _drive(sandbox)
    assert trace is not None, "閉環應產出 trace（旗標開、張力存在、剖面可解析）"
    assert trace.agent_id == TEST_AGENT_ID
    assert trace.drive_code == "D1", "驅動代碼應為 D1（資訊缺口）"
    assert thinker.calls == 1, "MockThinker 應恰好被呼叫 1 次"

    # ① working_state 落地（沙盒）
    ws_path = sandbox.store_dir / tcl.WORKING_STATE_FILENAME
    assert ws_path.is_file(), "working_state.jsonl 應落地於沙盒"
    rows = _read_jsonl(ws_path)
    assert len(rows) == 1, f"working state 應恰好 1 筆，實得 {len(rows)}"
    row = rows[0]
    assert set(row.keys()) == set(tcl.WORKING_STATE_FIELDS), sorted(row.keys())
    assert row["agent_id"] == TEST_AGENT_ID
    assert row["drive_code"] == "D1"
    assert row["drives_touched"] == ["D1"]
    assert row["decided"] is False, "2 個缺口只消解 1 個 ⇒ 尚未想完"
    assert row["remaining_gap_count"] == 1
    assert row["open_gap_count_before"] == 2

    # ② 張力事件落地（沙盒；欄位形狀與生產一致）
    events = _read_jsonl(sandbox.store_dir / tcl.TENSION_STORE_FILENAME)
    assert len(events) == 1, f"沙盒張力承載應恰好 1 筆，實得 {len(events)}"
    assert events[0]["subject"] == "D1"
    assert events[0]["status"] == tcl.TENSION_STATUS_PARTIALLY_RESOLVED

    # ③ 唯讀介面讀回（**確有一行**；行的**逐位元內容**由 E2E-T3 負責）
    lines = tcl.format_working_state_lines(TEST_AGENT_ID, sandbox.store_dir)
    assert len(lines) == 1, lines

    # ④ 代理注入：group 路徑必須含該區塊且正文非空
    #    （🔺 **兩條**路徑的注入由 E2E-T7 負責；本項只驗「鏈路有接通」）
    group, _private = _assemble()
    content = _system_content(group)
    assert THINKING_STATE_MARKER in content, "prompt 必須含 [思考狀態] 區塊"
    assert _thinking_state_body(content).strip(), "思考狀態正文不得為空"


# ═══════════════════════════════════════════════════════════════════════
# E2E-T2：注入位置（inner_life 之後、world_context 之前）
# ═══════════════════════════════════════════════════════════════════════
def test_e2e_t2_thinking_state_sits_between_inner_life_and_world_context(
    sandbox: _Sandbox,
) -> None:
    """group 路徑：``[思考狀態]`` 在 ``[最近內在生活]`` 之後、world_context 之前。"""
    _seed_d1_tension(sandbox)
    _seed_diary(sandbox)
    _drive(sandbox)

    group, _private = _assemble()
    content = _system_content(group)

    i_inner = content.find(INNER_LIFE_MARKER)
    i_state = content.find(THINKING_STATE_MARKER)
    i_world = content.find(TEST_WORLD_CONTEXT)
    assert i_inner >= 0, "前置條件：inner_life 區塊必須非空（否則位置無從比對）"
    assert i_state >= 0, "前置條件：思考狀態區塊必須存在"
    assert i_world >= 0, "前置條件：world_context 必須存在"
    assert i_inner < i_state < i_world, (
        f"注入位置錯誤：inner_life={i_inner} < state={i_state} < world={i_world}"
    )


# ═══════════════════════════════════════════════════════════════════════
# E2E-T3：逐位元一致（prompt 正文 ＝ 唯讀介面讀回行）
# ═══════════════════════════════════════════════════════════════════════
def test_e2e_t3_prompt_body_is_byte_identical_to_read_back_line(
    sandbox: _Sandbox,
) -> None:
    """prompt 內的 ``[思考狀態]`` 正文 ＝ ``format_working_state_lines()`` 的讀回行。"""
    _seed_d1_tension(sandbox)
    _seed_diary(sandbox)
    _drive(sandbox)

    lines = tcl.format_working_state_lines(TEST_AGENT_ID, sandbox.store_dir)
    assert len(lines) == 1, f"唯讀介面應讀回一行，實得 {lines}"
    assert lines[0] == EXPECTED_WORKING_STATE_LINE, (
            f"唯讀行的**格式**被改動：{lines[0]!r}"
    )

    group, _private = _assemble()
    body = _thinking_state_body(_system_content(group))
    assert body == lines[0], f"正文與讀回行不一致：{body!r} vs {lines[0]!r}"
    assert body.encode("utf-8") == lines[0].encode("utf-8"), "必須逐位元相同"


# ═══════════════════════════════════════════════════════════════════════
# E2E-T4：確定性（同一組輸入連跑兩次 ⇒ prompt 逐位元相同）
# ═══════════════════════════════════════════════════════════════════════
def test_e2e_t4_two_identical_runs_produce_byte_identical_prompts(
    sandbox: _Sandbox,
) -> None:
    """同一組輸入連跑兩次 ⇒ 兩次組出的 prompt **byte-identical**。"""
    _seed_d1_tension(sandbox)
    _seed_diary(sandbox)

    _drive(sandbox)
    first_group, first_private = _assemble()
    first_group_digest = _digest(first_group)
    first_private_digest = _digest(first_private)

    # 再跑一次（**同一組輸入**；working_state 為 append-only ⇒ 記錄變 2 筆）
    _drive(sandbox)
    second_group, second_private = _assemble()

    assert _digest(second_group) == first_group_digest, "group prompt 必須逐位元相同"
    assert _digest(second_private) == first_private_digest, "private prompt 必須逐位元相同"
    assert (
        _system_content(first_group) == _system_content(second_group)
    ), "system prompt 正文必須逐位元相同"

    # 兩次閉環產出的 working state 記錄也必須逐位元相同（決定性）
    rows = _read_jsonl(sandbox.store_dir / tcl.WORKING_STATE_FILENAME)
    assert len(rows) == 2, f"應累積 2 筆，實得 {len(rows)}"
    assert rows[0] == rows[1], "同一組輸入的兩次閉環輸出必須逐位元相同"


# ═══════════════════════════════════════════════════════════════════════
# E2E-T5：零網路 / 零真實 LLM
# ═══════════════════════════════════════════════════════════════════════
def test_e2e_t5_no_network_and_no_real_llm(sandbox: _Sandbox) -> None:
    """AST 層無網路 import ＋ 執行期無真實 provider、MockThinker 恰呼叫 1 次。"""
    # ① AST：本檔無網路／外部 API import
    own_roots = _imported_roots(Path(__file__).resolve())
    assert not (own_roots & BANNED_NETWORK_IMPORTS), (
        f"本檔不得 import 網路／外部 API 模組，實得 {sorted(own_roots & BANNED_NETWORK_IMPORTS)}"
    )

    # ② AST：鏈路模組（零網路段）無網路 import
    for rel in CHAIN_MODULES:
        roots = _imported_roots(REPO_ROOT / rel)
        assert not (roots & BANNED_NETWORK_IMPORTS), (
            f"{rel} 不得 import 網路／外部 API 模組，實得 {sorted(roots & BANNED_NETWORK_IMPORTS)}"
        )

    # ③ AST：proxy 的**新增區塊函式**是純讀（無網路 import、無呼叫）
    node = _function_node(REPO_ROOT / "src" / "llm" / "proxy.py", "_format_working_state_block")
    offenders: List[str] = []
    for sub in ast.walk(node):
        if isinstance(sub, ast.Import):
            for alias in sub.names:
                if alias.name.split(".")[0] in BANNED_NETWORK_IMPORTS:
                    offenders.append(f"import {alias.name}")
        elif isinstance(sub, ast.ImportFrom):
            if (sub.module or "").split(".")[0] in BANNED_NETWORK_IMPORTS:
                offenders.append(f"from {sub.module} import ...")
        elif isinstance(sub, ast.Call):
            func = sub.func
            if isinstance(func, ast.Name) and func.id in ("open", "urlopen", "connect"):
                offenders.append(f"call {func.id}()")
            elif isinstance(func, ast.Attribute) and func.attr in ("connect", "urlopen"):
                offenders.append(f"call .{func.attr}()")
    assert not offenders, f"思考狀態區塊函式內不得有網路／寫檔呼叫，實得 {offenders}"

    # ④ 執行期：**真實 provider 不可被建構**（任何人一建構就爆炸）
    def _boom(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("本票不得建構真實 LLM provider（LLMThinker）")

    sandbox.mp.setattr(tt.LLMThinker, "__init__", _boom)

    _seed_d1_tension(sandbox)
    _seed_diary(sandbox)
    trace, thinker = _drive(sandbox)
    assert trace is not None

    # ⑤ MockThinker 呼叫計數 ＝ 1，且用的是 Phase 2 的決定性 MockThinker
    assert thinker.calls == 1, f"MockThinker 應恰呼叫 1 次，實得 {thinker.calls}"
    assert isinstance(thinker.inner, tt.DeterministicMockThinker)

    # ⑥ 工作狀態記錄裡的稽核錨點 ＝ mock **實際送出**的 prompt 的 sha256
    row = _read_jsonl(sandbox.store_dir / tcl.WORKING_STATE_FILENAME)[0]
    assert row["thinker"] == "_AsyncDeterministicMockThinker", row["thinker"]
    assert row["thinker"] != "LLMThinker"
    assert row["think_prompt_sha256"] == hashlib.sha256(
        thinker.prompts[0].encode("utf-8")
    ).hexdigest()


# ═══════════════════════════════════════════════════════════════════════
# E2E-T6：沙盒隔離（所有寫入落在沙盒；生產 working_state 仍不存在）
# ═══════════════════════════════════════════════════════════════════════
def test_e2e_t6_all_writes_stay_in_sandbox_and_production_is_untouched(
    sandbox: _Sandbox,
) -> None:
    """寫入全部落在**宣告的沙盒**；生產 ``working_state.jsonl`` 仍不存在。"""
    repo_inner_life = REPO_ROOT / "data" / "inner_life"
    names_before = _file_set(repo_inner_life)

    diary_date = _seed_diary(sandbox)
    _seed_d1_tension(sandbox)
    _drive(sandbox)

    # ① 所有產出都在**宣告的沙盒資料根**之下，且檔案集完全可預期
    produced = _file_set(sandbox.data_root)
    expected = sorted(
        [
            f"inner_life/{tcl.TENSION_STORE_FILENAME}",
            f"inner_life/{tcl.WORKING_STATE_FILENAME}",
            f"soul/{TEST_AGENT_ID}/diary/{diary_date}.jsonl",
            f"soul/{TEST_AGENT_ID}/life_threads.jsonl",
            f"soul/{TEST_AGENT_ID}/relationships.json",
        ]
    )
    assert produced == expected, f"沙盒檔案集不符：{produced}"

    # ② 生產側：**沒有任何新檔案**（檔名集不變 ⇒ 沒有寫入生產）
    assert _file_set(repo_inner_life) == names_before, "不得在生產 inner_life 之下建立檔案"

    # ③ 生產 ``working_state.jsonl`` 仍不存在
    #    （該檔沒有任何生產寫入者 ⇒ 此斷言**決定性**、不受線上服務競態影響）
    assert not (repo_inner_life / tcl.WORKING_STATE_FILENAME).exists(), (
        "生產 working_state.jsonl 仍不應存在（旗標缺省 OFF）"
    )

    # ④ 沙盒覆寫**沒有**放寬生產守門
    allowed, reason = tcl.check_store_dir(repo_inner_life)
    assert allowed is False, f"生產路徑必須仍被守門擋下（實得允許：{reason}）"
    assert reason, "拒絕必須有理由（可稽核）"

    # ⑤ 生產唯讀介面確實讀的是**沙盒**（覆寫生效）
    assert tcl.format_working_state_lines_production(TEST_AGENT_ID) == (
        tcl.format_working_state_lines(TEST_AGENT_ID, sandbox.store_dir)
    )


# ═══════════════════════════════════════════════════════════════════════
# E2E-T7：兩條路徑皆注入（group ＋ private）
# ═══════════════════════════════════════════════════════════════════════
def test_e2e_t7_both_group_and_private_paths_carry_the_block(
    sandbox: _Sandbox,
) -> None:
    """group 與 private **兩條**組裝路徑都含同一個 ``[思考狀態]`` 區塊。"""
    _seed_d1_tension(sandbox)
    _seed_diary(sandbox)
    _drive(sandbox)

    lines = tcl.format_working_state_lines(TEST_AGENT_ID, sandbox.store_dir)
    assert len(lines) == 1, lines

    group, private = _assemble()
    group_content = _system_content(group)
    private_content = _system_content(private)
    assert THINKING_STATE_MARKER in group_content, "group 路徑必須含 [思考狀態] 區塊"
    assert THINKING_STATE_MARKER in private_content, "private 路徑必須含 [思考狀態] 區塊"
    group_body = _thinking_state_body(group_content)
    private_body = _thinking_state_body(private_content)
    assert group_body == private_body, "兩條路徑的正文必須逐位元相同"
    assert group_body.strip(), "思考狀態正文不得為空"


# ═══════════════════════════════════════════════════════════════════════
# E2E-T8：旗標與預算不受影響（沙盒語意未變；既有守衛檔未被削弱）
# ═══════════════════════════════════════════════════════════════════════
def test_e2e_t8_flag_and_budget_unchanged_and_sandbox_semantics_intact(
    sandbox: _Sandbox,
) -> None:
    """既有守衛檔未被削弱 ＋ 旗標缺省 OFF ＋ async 沙盒語意與預算不變。"""
    # ① 既有 Phase 4 守衛檔**未被本票改動**（EOL 正規化 sha256 ＋ 70 筆）
    guard_path = REPO_ROOT / PHASE4_GUARD_FILE
    actual = hashlib.sha256(
        guard_path.read_text(encoding="utf-8").encode("utf-8")
    ).hexdigest()
    assert actual == PHASE4_GUARD_SHA256, (
        f"{PHASE4_GUARD_FILE} 被本票改動 ⇒ 既有 70 筆守衛已被削弱"
    )
    tree = ast.parse(guard_path.read_text(encoding="utf-8"))
    funcs = [
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("test_")
    ]
    assert len(funcs) == PHASE4_GUARD_TEST_FUNC_COUNT, (
        f"既有守衛檔的測試函式數為 {len(funcs)}，應為 {PHASE4_GUARD_TEST_FUNC_COUNT}"
    )

    # ② async 閉環仍跑得通（沙盒語意未變）
    _seed_d1_tension(sandbox)
    _seed_diary(sandbox)
    trace, _thinker = _drive(sandbox)
    assert trace is not None

    # ③ 生產路徑仍被守門擋下（回 None、0 檔案）
    prod_store = REPO_ROOT / "data" / "inner_life"
    prod_before = _file_set(prod_store)
    assert (
        asyncio.run(
            tcl.drive_loop_async(
                TEST_AGENT_ID, FAKE_NOW, prod_store, thinker=_AsyncDeterministicMockThinker()
            )
        )
        is None
    ), "生產路徑必須仍被守門擋下"
    assert _file_set(prod_store) == prod_before, "被擋下時不得建立任何檔案"

    # ④ 同步 thinker 必須**明確 raise**（兩條路徑互斥的語意未變）
    with pytest.raises(TypeError):
        asyncio.run(
            tcl.drive_loop_async(
                TEST_AGENT_ID,
                FAKE_NOW,
                sandbox.store_dir,
                thinker=tt.DeterministicMockThinker(),
            )
        )

    # ⑤ 預算不變：區塊有界（≤3 行、每行 ≤60 字元、總長 ≤200 字元）
    group, _private = _assemble()
    block = _thinking_state_block(_system_content(group))
    body_lines = [ln for ln in _thinking_state_body(_system_content(group)).splitlines() if ln]
    assert len(body_lines) <= 3, body_lines
    assert all(len(ln) <= 60 for ln in body_lines), body_lines
    assert len(block) + 1 <= 200, f"區塊總長超出預算：{len(block)}"

    # ⑥ 旗標缺省 OFF（把環境變數拿掉 ⇒ feature_enabled() 必須為 False）
    sandbox.mp.delenv(tcl.FEATURE_ENV, raising=False)
    assert tcl.feature_enabled() is False, "旗標缺省必須是 OFF（生產零行為變化）"
