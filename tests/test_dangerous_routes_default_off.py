"""
test_dangerous_routes_default_off.py

EI-SEC-P0-DEFAULT-OFF-IMPLEMENT-1 — 五條危險路由預設關閉
（Owner Bry 已核准 Option A）。

除役路由（預設不註冊、無 alias、不因 SOUL_ENV 重開）：
  POST /memory/clear/group
  POST /memory/clear/{agent_id}
  GET  /debug/broadcast
  GET  /inject/yua
  GET  /_admin/fast_forward

本檔覆蓋：
  1. 路由缺席（五端點 404＋route table＋OpenAPI＋雙 SOUL_ENV）
  2. 零副作用（檔案快照＋bus 無 publish＋無 heartbeat 變＋無 broadcast）
  3. 相容（普通路由／WS 路徑／debug/emotion 不受影響）
  4. UI 行為（clearCurrentMemory 不再 POST、不謊報成功、不刪 localStorage／畫面；
     clearHistory 不動）

隔離：全域 autouse fixture 已把 SOUL_OS_DATA_DIR 指向每測專屬 tmp；
只用 TestClient＋直測（不啟真正服務、不打 LLM、不碰外部網路）。

執行（.venv 口徑）：
  .venv\\Scripts\\python.exe -m pytest tests/test_dangerous_routes_default_off.py -q -p no:cacheprovider
"""
import asyncio
import hashlib
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from src.eventbus import SoulEventBus
from src.eventbus.schema import EventType, SoulEvent
from src.io import gateway as gw_module
from src.io.gateway import IOGateway
from src.paths import data_root, reset_data_root

REPO_ROOT = Path(__file__).resolve().parent.parent

#: （method， path）—— path 皆為實例化後的具體路徑（路由模板亦不可存在）。
DANGEROUS_ROUTES = [
    ("POST", "/memory/clear/group"),
    ("POST", "/memory/clear/agent_yua"),
    ("GET", "/debug/broadcast"),
    ("GET", "/inject/yua"),
    ("GET", "/_admin/fast_forward"),
]

#: 模板級路徑（route table／OpenAPI 斷言用）。
DANGEROUS_TEMPLATES = {
    "/memory/clear/group",
    "/memory/clear/{agent_id}",
    "/debug/broadcast",
    "/inject/yua",
    "/_admin/fast_forward",
}


def _ensure_isolated_root() -> None:
    reset_data_root()
    root = data_root()
    assert "soul_data_root" in str(root), (
        f"隔離失效：data_root()={root} 不在測試 tmp 內，拒絕執行"
    )


def _build_gateway():
    """組裝最小 Gateway（不啟 bus，只需 app 路由層）。"""
    _ensure_isolated_root()
    bus = SoulEventBus()
    gateway = IOGateway(bus)
    gateway.register()
    return gateway


def _fire_all(client: TestClient) -> dict:
    """對五條除役端點各打一次，回傳 {(method, path): status}。"""
    out = {}
    for method, path in DANGEROUS_ROUTES:
        if method == "POST":
            resp = client.post(path)
        else:
            resp = client.get(path)
        out[(method, path)] = resp.status_code
    return out


def _snapshot() -> dict:
    """快照 data_root 全樹（相對路徑 → sha256），供副作用比對。"""
    snap = {}
    for p in sorted(data_root().rglob("*")):
        if p.is_file():
            snap[str(p.relative_to(data_root()))] = hashlib.sha256(
                p.read_bytes()
            ).hexdigest()
    return snap


# ─────────────────────────────────────────────
# 1. 路由缺席
# ─────────────────────────────────────────────

@pytest.mark.parametrize("method,path", DANGEROUS_ROUTES)
def test_dangerous_route_is_404(method, path):
    gateway = _build_gateway()
    with TestClient(gateway.app) as client:
        resp = client.request(method, path)
    assert resp.status_code == 404, (
        f"EI-SEC-P0：{method} {path} 應不可路由（404），實際 {resp.status_code}"
    )


@pytest.mark.parametrize("env", ["dev", "production"])
def test_dangerous_routes_absent_in_both_soul_envs(env):
    """缺席不依賴 SOUL_ENV：dev／production 皆不可路由（不是閘門，是沒有註冊）。"""
    old = os.environ.get("SOUL_ENV")
    os.environ["SOUL_ENV"] = env
    try:
        gateway = _build_gateway()
        with TestClient(gateway.app) as client:
            got = _fire_all(client)
    finally:
        if old is None:
            os.environ.pop("SOUL_ENV", None)
        else:
            os.environ["SOUL_ENV"] = old
    assert all(s == 404 for s in got.values()), (
        f"EI-SEC-P0：SOUL_ENV={env} 下仍有可路由的危險端點：{got}"
    )


def test_dangerous_paths_absent_from_route_table():
    gateway = _build_gateway()
    paths = {getattr(r, "path", "") for r in gateway.app.routes}
    for tpl in DANGEROUS_TEMPLATES:
        assert tpl not in paths, f"EI-SEC-P0：route table 仍含 {tpl}（現有：{sorted(paths)})"
    assert not any(
        p.startswith("/memory/clear") for p in paths
    ), f"EI-SEC-P0：route table 仍含 /memory/clear*（現有：{sorted(paths)})"


def test_dangerous_paths_absent_from_openapi():
    gateway = _build_gateway()
    spec_paths = set(gateway.app.openapi().get("paths", {}).keys())
    for tpl in DANGEROUS_TEMPLATES:
        assert tpl not in spec_paths, f"EI-SEC-P0：OpenAPI 仍含 {tpl}"
    assert not any(
        p.startswith("/memory/clear") for p in spec_paths
    ), "EI-SEC-P0：OpenAPI 仍含 /memory/clear*"


def test_no_route_registration_or_alias_in_source():
    """結構釘：五路由的 decorator＋handler 定義皆不得存在；不得有 alias 重暴露。"""
    src = Path(gw_module.__file__).read_text(encoding="utf-8")
    for marker in (
        '@self.app.get("/debug/broadcast")',
        "async def debug_broadcast",
        '@self.app.get("/inject/yua")',
        "async def inject_yua",
        '@self.app.get("/_admin/fast_forward")',
        "async def admin_fast_forward",
        '@self.app.post("/memory/clear/group")',
        "async def clear_group_memory",
        "@self.app.post(\"/memory/clear/{agent_id}\")",
        "async def clear_agent_memory",
    ):
        assert marker not in src, f"MUT-RED：危險路由實作被恢復了：{marker}"
    # 未註冊實作必須仍在（EI-SEC 安全邏輯＝unregistered code 不破壞）
    assert "async def _unregistered_clear_agent_memory" in src
    assert "async def _unregistered_clear_group_memory" in src
    assert "def _validate_clear_agent_id" in src


# ─────────────────────────────────────────────
# 2. 零副作用（拒絕前）
# ─────────────────────────────────────────────

async def test_denied_requests_have_zero_side_effects():
    """五端點 404 路徑：檔案零變更、bus 零 publish、WS 零 broadcast、heartbeat 不變。"""
    _ensure_isolated_root()
    conv = data_root() / "conversations"
    conv.mkdir(parents=True, exist_ok=True)
    seed = conv / "bryan_agent_ruka_private.json"
    seed.write_text('{"keep": true}', encoding="utf-8")
    (conv / "group_chat.json").write_text("[]", encoding="utf-8")
    before = _snapshot()

    bus = SoulEventBus()
    await bus.start()
    seen: list = []

    async def _collector(event: SoulEvent) -> None:
        seen.append(event)

    for et in (EventType.AGENT_INTENT, EventType.AGENT_SPEAK, EventType.USER_MESSAGE):
        bus.subscribe(subscriber_id=f"test_default_off_{et.name}", handler=_collector, event_filter={et})

    gateway = IOGateway(bus)
    gateway.register()

    class _SilentWS:
        def __init__(self):
            self.received = []

        async def accept(self) -> None:
            pass

        async def send_text(self, msg: str) -> None:
            self.received.append(msg)

        async def receive_text(self) -> None:
            await asyncio.sleep(999)

    silent = _SilentWS()
    await gateway.manager.connect(silent)

    heartbeat_before = getattr(gateway.app.state, "_heartbeat", None)
    try:
        with TestClient(gateway.app) as client:
            got = _fire_all(client)
        assert all(s == 404 for s in got.values()), f"應全 404，實際 {got}"
        await asyncio.sleep(0.2)
    finally:
        for et in (EventType.AGENT_INTENT, EventType.AGENT_SPEAK, EventType.USER_MESSAGE):
            bus.unsubscribe(f"test_default_off_{et.name}")
        await bus.stop()

    assert _snapshot() == before, "404 拒絕路徑造成了檔案系統副作用"
    assert seed.exists(), "種子檔被刪除了"
    assert seen == [], f"404 路徑不應向 bus 發任何事件，實際 {len(seen)} 筆"
    assert silent.received == [], f"404 路徑不應有 WS broadcast，實際 {silent.received}"
    assert getattr(gateway.app.state, "_heartbeat", None) is heartbeat_before is None, (
        "heartbeat 被碰觸了"
    )


# ─────────────────────────────────────────────
# 3. 相容（普通路由／WS／debug/emotion 不受影響）
# ─────────────────────────────────────────────

def test_compatible_routes_unaffected():
    gateway = _build_gateway()
    with TestClient(gateway.app) as client:
        health = client.get("/health")
        assert health.status_code == 200, f"/health 應 200，實際 {health.status_code}"
        assert health.json().get("status") == "ok"

        root = client.get("/")
        assert root.status_code == 200, f"/ 應 200，實際 {root.status_code}"

        # /debug/emotion 路由保留：未知 ID 仍 404（既有守衛語意不變）
        emo = client.get("/debug/emotion/agent_nobody_xyz")
        assert emo.status_code == 404, f"/debug/emotion 未知 ID 應 404，實際 {emo.status_code}"

    paths = {getattr(r, "path", "") for r in gateway.app.routes}
    assert "/ws" in paths, "WS 路由 /ws 不見了（超出授權範圍的破壞）"
    assert "/debug/emotion/{agent_id}" in paths, "/debug/emotion 路由不見了"
    assert "/health" in gateway.app.openapi().get("paths", {}), "OpenAPI 缺 /health"


# ─────────────────────────────────────────────
# 4. UI 行為
# ─────────────────────────────────────────────

def _read_index_html() -> str:
    return (REPO_ROOT / "static" / "index.html").read_text(encoding="utf-8")


def _fn_body(src: str, name: str) -> str:
    """擷取 `function <name>(...) {...}` 到下一個同級 function 為止的粗略本體。"""
    start = src.find(f"function {name}")
    assert start != -1, f"index.html 找不到 function {name}"
    nxt = src.find("\n    function ", start + 1)
    return src[start:] if nxt == -1 else src[start:nxt]


def test_ui_clear_button_no_longer_posts_to_disabled_endpoints():
    body = _fn_body(_read_index_html(), "clearCurrentMemory")
    assert "/memory/clear" not in body, (
        "MUT-RED：前端仍在向已除役端點 POST（clearCurrentMemory 含 /memory/clear）"
    )
    assert "fetch(" not in body, "MUT-RED：clearCurrentMemory 仍在發網路請求"


def test_ui_does_not_claim_memory_cleared_and_keeps_state():
    src = _read_index_html()
    assert "記憶已清除" not in src, "MUT-RED：UI 謊報成功（「記憶已清除」被恢復了）"
    body = _fn_body(src, "clearCurrentMemory")
    assert "localStorage.removeItem" not in body, (
        "MUT-RED：clearCurrentMemory 刪了 localStorage（停用狀態不得動本地狀態）"
    )
    assert 'innerHTML' not in body, "MUT-RED：clearCurrentMemory 清空了聊天畫"
    assert "已停用" in body, "clearCurrentMemory 必須明確告知停用"


def test_ui_clear_history_untouched():
    """clearHistory() 是純本地清除，不在本票範圍內，必須原樣保留。"""
    body = _fn_body(_read_index_html(), "clearHistory")
    assert "localStorage.removeItem" in body, "clearHistory 語意被破壞了"
    assert 'innerHTML' in body, "clearHistory 語意被破壞了"
