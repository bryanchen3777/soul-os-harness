"""
test_memory_clear_path_validation.py

EI-SEC-PATH-VALIDATION-IMPLEMENT-1 — `/memory/clear/{agent_id}` 路徑輸入驗證。

只測「不可信路徑參數」的安全，不碰鑑權／SOUL_ENV／部署政策。

隔離：
  - 全域 autouse fixture 已把 SOUL_OS_DATA_DIR 指向每測專屬 tmp；
    本檔另行斷言 data_root() 落在 tmp 內（證明零生產路徑操作）。
  - 只用 TestClient（不啟真正 Soul OS 服務、不打 LLM、不碰外部網路）。

執行（.venv 口徑）：
  .venv\\Scripts\\python.exe -m pytest tests/test_memory_clear_path_validation.py -q -p no:cacheprovider
"""
import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from src.eventbus import SoulEventBus
from src.io import gateway as gw_module
from src.io.gateway import IOGateway
from src.paths import data_root, reset_data_root


def _build_client() -> TestClient:
    """組裝最小 Gateway app（不啟 bus，只需路由層）。"""
    reset_data_root()
    root = data_root()
    assert "soul_data_root" in str(root), (
        f"隔離失效：data_root()={root} 不在測試 tmp 內，拒絕執行"
    )
    bus = SoulEventBus()
    gateway = IOGateway(bus)
    gateway.register()
    return TestClient(gateway.app)


def _seed_private_file(agent_id: str, content: str = "seed") -> Path:
    conv = data_root() / "conversations"
    conv.mkdir(parents=True, exist_ok=True)
    p = conv / f"bryan_{agent_id}_private.json"
    p.write_text(json.dumps({"seed": content}), encoding="utf-8")
    return p


def _snapshot() -> dict:
    """快照 data_root 全樹（相對路徑 → sha256），供副作用比對。"""
    import hashlib

    snap = {}
    for p in sorted(data_root().rglob("*")):
        if p.is_file():
            snap[str(p.relative_to(data_root()))] = hashlib.sha256(
                p.read_bytes()
            ).hexdigest()
    return snap


# ─────────────────────────────────────────────
# 合法行為保持（回應契約不變）
# ─────────────────────────────────────────────

async def test_valid_agent_clears_private_file_and_keeps_contract():
    client = _build_client()
    _seed_private_file("agent_ruka")
    conv = data_root() / "conversations"
    group = conv / "group_chat.json"
    group.write_text(
        json.dumps(
            [
                {"speaker": "agent_ruka", "is_private": True, "text": "secret"},
                {"speaker": "agent_yua", "is_private": False, "text": "hello"},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    resp = client.post("/memory/clear/agent_ruka")

    assert resp.status_code == 200, f"合法 ID 應 200，實際 {resp.status_code}：{resp.text[:200]}"
    body = resp.json()
    # 原回應契約鍵集合不變
    assert set(body.keys()) >= {"agent_id", "cleared", "files"}, body
    assert body["agent_id"] == "agent_ruka"
    assert body["cleared"] is True
    assert not (conv / "bryan_agent_ruka_private.json").exists()
    # 群聊只移除該 agent 的私人標記，他人訊息保留
    rest = json.loads(group.read_text(encoding="utf-8"))
    assert all(m["speaker"] != "agent_ruka" for m in rest)
    assert any(m["speaker"] == "agent_yua" for m in rest)


async def test_valid_agent_without_file_is_noop():
    client = _build_client()
    resp = client.post("/memory/clear/agent_yua")
    assert resp.status_code == 200
    body = resp.json()
    assert body["cleared"] is False
    assert body["files"] == []


async def test_group_endpoint_unaffected_by_this_ticket():
    """本票不得改 /memory/clear/group 語意（Out of Scope 回歸樁）。"""
    client = _build_client()
    conv = data_root() / "conversations"
    conv.mkdir(parents=True, exist_ok=True)
    (conv / "group_chat.json").write_text("[1]", encoding="utf-8")
    resp = client.post("/memory/clear/group")
    assert resp.status_code == 200
    assert resp.json()["cleared"] is True
    assert not (conv / "group_chat.json").exists()


# ─────────────────────────────────────────────
# 非法輸入 fail-closed（4xx＋零副作用）
# ─────────────────────────────────────────────

INVALID_IDS_400 = [
    # 註：字面 `.`/`..` 會被 HTTP client 在送出前正規化（到不了 handler），
    # 故只在 validator 單測覆蓋；此處測「編碼後抵達」的等價路徑。
    "%2e%2e",  # 解碼後為 `..`（dot segment 經編碼抵達，必須由閘門擋）
    "%2E%2E",  # 大寫十六進位同上
    "..%5C..%5Cpwned",  # 反斜槓（含 bryan_ 前綴只抵 conversations 內，仍必須 400）
    "..%5c..%5cpwned",  # 小寫十六進位同上
    "a%5C..%5C..%5Cpwned",  # 真逃逸形：bryan_a\..\..\pwned → data_root/pwned_private.json
    "a%5c..%5c..%5cpwned",  # 小寫十六進位同上
    "agent_ruka%5C..%5Cx",  # 合法前綴＋反斜槓逃逸
    "agent_%2e%2e_x",  # 雙重編碼殘留（decode 一次後含 %，必須擋）
    "agent_YUA",  # 大寫非法
    "agent-ruka",  # 連字號非法
    "agent_ruka!",  # 標點非法
    "agent%20ruka",  # 空格非法（%20）
    "yua",  # 缺前綴（短 ID 不是本端點契約）
    "agent_",  # 空本體
    "agent_" + "a" * 49,  # 超長（上限 48）
    "agent_ruka%00",  # NUL 注入
]


@pytest.mark.parametrize("raw", INVALID_IDS_400)
async def test_invalid_agent_id_rejected_with_400_and_no_side_effects(raw):
    client = _build_client()
    _seed_private_file("agent_ruka")
    sentinel_outside = data_root() / "pwned_private.json"
    sentinel_outside.write_text("do not touch", encoding="utf-8")
    before = _snapshot()

    resp = client.post(f"/memory/clear/{raw}")

    assert resp.status_code == 400, (
        f"輸入 {raw!r} 應回 400，實際 {resp.status_code}：{resp.text[:200]}"
    )
    assert _snapshot() == before, f"非法輸入 {raw!r} 造成了檔案系統副作用"
    assert (data_root() / "conversations" / "bryan_agent_ruka_private.json").exists()


async def test_unknown_but_wellformed_id_returns_404():
    """合法字元集但註冊表查無 → 404（與 /debug/emotion 慣例一致）。"""
    client = _build_client()
    before = _snapshot()
    resp = client.post("/memory/clear/agent_nobody_xyz")
    assert resp.status_code == 404, f"未知 ID 應 404，實際 {resp.status_code}"
    assert _snapshot() == before


async def test_length_boundary_48_vs_49():
    client = _build_client()
    # 48 字元本體：字元集閘通過，止於成員資格閘 → 404
    resp48 = client.post("/memory/clear/agent_" + "b" * 48)
    assert resp48.status_code == 404, f"48 本體應止於 404，實際 {resp48.status_code}"
    # 49 字元本體：止於字元集閘 → 400
    resp49 = client.post("/memory/clear/agent_" + "b" * 49)
    assert resp49.status_code == 400, f"49 本體應止於 400，實際 {resp49.status_code}"


async def test_encoded_slash_does_not_reach_handler():
    """%2F 解碼成 / 後路由失配 → 404（斜槓遍歷在本路由不可達，鎖住此性質）。"""
    client = _build_client()
    before = _snapshot()
    resp = client.post("/memory/clear/..%2F..%2Fpwned")
    assert resp.status_code == 404, f"%2F 應路由失配 404，實際 {resp.status_code}"
    assert _snapshot() == before


def test_validator_unit_rejects_empty_and_dot_segments():
    """直測 validator：空值與 dot segments（HTTP 層測不到空 segment）。"""
    from fastapi import HTTPException

    for bad in ("", ".", ".."):
        with pytest.raises(HTTPException) as exc:
            gw_module._validate_clear_agent_id(bad)
        assert exc.value.status_code == 400


# ─────────────────────────────────────────────
# 安全變異測試（閘門被移除／放寬必須精準轉紅）
# ─────────────────────────────────────────────

def test_mutation_pin_validation_gate_precedes_first_file_op():
    """結構釘：`_validate_clear_agent_id(agent_id)` 呼叫必須存在，且位於
    第一個檔案操作（exists/unlink/read_text/write_text）之前。

    變異 M1（刪除閘門呼叫）→ 本測試轉紅。
    變異 M2（把閘門挪到檔案操作之後）→ 本測試轉紅。
    """
    src = Path(gw_module.__file__).read_text(encoding="utf-8")
    body = src.split("async def clear_agent_memory", 1)[1].split(
        "async def ", 1
    )[0]
    # 去註解後再找（避免有人把閘門註解掉卻留下字串殘留而逃過偵測）
    code_lines = [
        ln.split("#", 1)[0]
        for ln in body.splitlines()
    ]
    code = "\n".join(code_lines)
    gate_idx = code.find("_validate_clear_agent_id(agent_id)")
    assert gate_idx != -1, "MUT-RED：閘門呼叫被刪除了"
    first_file_op = min(
        code.find(marker)
        for marker in (".exists()", ".unlink()", ".read_text(", ".write_text(")
        if code.find(marker) != -1
    )
    assert gate_idx < first_file_op, (
        "MUT-RED：閘門必須在第一個檔案操作之前（fail-closed 順序被破壞）"
    )
    assert "path containment violation" in code, "MUT-RED：路徑收容檢查被刪除了"


def test_mutation_pin_containment_uses_realpath_not_prefix():
    """結構釘：收容必須用 realpath 解析比對，不得退化成字串前綴判斷。"""
    src = Path(gw_module.__file__).read_text(encoding="utf-8")
    assert "realpath" in src, "MUT-RED：realpath 收容被移除（字串前綴可被 .. 繞過）"


# ─────────────────────────────────────────────
# FALLBACK-FIX-1：權威失效必須 fail-closed（503/404＋零副作用）
#
# 授權來源僅限「成功讀取的 configs/default.yaml enabled agents」。
# 不得回退到 AGENT_DISPLAY_NAMES（僅顯示用途）或硬編碼清單。
# ─────────────────────────────────────────────

def _deny_client(monkeypatch, fake_cfg):
    """把 configs.loader.load_config 換成指定行為，回傳隔離 client。"""
    import configs.loader as _loader

    def _fake(*a, **k):
        if isinstance(fake_cfg, BaseException):
            raise fake_cfg
        return fake_cfg

    monkeypatch.setattr(_loader, "load_config", _fake)
    return _build_client()


async def test_authority_config_missing_denies_with_503(monkeypatch):
    client = _deny_client(monkeypatch, FileNotFoundError("simulated missing config"))
    _seed_private_file("agent_ruka")
    before = _snapshot()
    resp = client.post("/memory/clear/agent_ruka")
    assert resp.status_code == 503, f"權威缺失應 503，實際 {resp.status_code}：{resp.text[:200]}"
    assert _snapshot() == before, "權威失效後發生了檔案系統副作用"
    assert (data_root() / "conversations" / "bryan_agent_ruka_private.json").exists()


async def test_authority_parse_exception_denies_with_503(monkeypatch):
    import yaml

    client = _deny_client(monkeypatch, yaml.YAMLError("simulated parse failure"))
    before = _snapshot()
    resp = client.post("/memory/clear/agent_ruka")
    assert resp.status_code == 503, f"解析失敗應 503，實際 {resp.status_code}"
    assert _snapshot() == before


async def test_authority_malformed_agents_structure_denies(monkeypatch):
    client = _deny_client(monkeypatch, {"agents": "not-a-list"})
    before = _snapshot()
    resp = client.post("/memory/clear/agent_ruka")
    assert resp.status_code == 503, f"結構無效應 503，實際 {resp.status_code}"
    assert _snapshot() == before


async def test_authority_non_dict_entries_deny_unknown(monkeypatch):
    client = _deny_client(monkeypatch, {"agents": ["not-a-dict"]})
    before = _snapshot()
    resp = client.post("/memory/clear/agent_ruka")
    assert resp.status_code in (404, 503), f"髒條目下應拒絕，實際 {resp.status_code}"
    assert _snapshot() == before


async def test_disabled_agent_denied_404(monkeypatch):
    client = _deny_client(
        monkeypatch, {"agents": [{"id": "agent_ruka", "enabled": False}]}
    )
    _seed_private_file("agent_ruka")
    before = _snapshot()
    resp = client.post("/memory/clear/agent_ruka")
    assert resp.status_code == 404, f"已停用應 404，實際 {resp.status_code}"
    assert _snapshot() == before
    assert (data_root() / "conversations" / "bryan_agent_ruka_private.json").exists()


async def test_disabled_display_agent_with_config_failure_denies(monkeypatch):
    """關鍵回歸：display 名錶內的已停用 ID，在權威失效時不得被放行。
    舊 fallback 會接受它（→200 真刪）；新行為必須 503。"""
    client = _deny_client(monkeypatch, RuntimeError("simulated authority outage"))
    _seed_private_file("agent_ruka")
    before = _snapshot()
    resp = client.post("/memory/clear/agent_ruka")
    assert resp.status_code == 503, f"權威失效時 display 名內 ID 應 503，實際 {resp.status_code}"
    assert _snapshot() == before
    assert (data_root() / "conversations" / "bryan_agent_ruka_private.json").exists()


async def test_empty_enabled_list_denies(monkeypatch):
    client = _deny_client(monkeypatch, {"agents": []})
    before = _snapshot()
    resp = client.post("/memory/clear/agent_ruka")
    assert resp.status_code == 404, f"空授權集應 404，實際 {resp.status_code}"
    assert _snapshot() == before


def test_mutation_pin_no_display_name_authorization_fallback():
    """結構釘：`_validate_clear_agent_id` 函式體不得引用 AGENT_DISPLAY_NAMES；
    重新引入 display-name fallback（函式體出現該符號）→ 本測試轉紅。
    另鎖定權威失效路徑存在（503）。"""
    src = Path(gw_module.__file__).read_text(encoding="utf-8")
    fn_body = src.split("def _validate_clear_agent_id", 1)[1].split(
        "\n# ──", 1
    )[0]
    code_lines = [ln.split("#", 1)[0] for ln in fn_body.splitlines()]
    code = "\n".join(code_lines)
    assert "AGENT_DISPLAY_NAMES" not in code, (
        "MUT-RED：display-name fallback 被重新引入（授權來源污染）"
    )
    assert "503" in code, "MUT-RED：權威失效的 503 拒絕路徑被刪除"
    assert "agent authority unavailable" in code


if __name__ == "__main__":
    asyncio.run(test_valid_agent_clears_private_file_and_keeps_contract())
