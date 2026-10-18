# SOUL-BUG-011 守衛：Diary raw response sidecar
#
# 這張測試存在的唯一理由：SOUL-BUG-011 的根因未定案，而**原始 LLM 回應從未被
# 持久化**。本測試釘住 sidecar 契約——「LLM 回什麼，sidecar 就逐位元存什麼」，
# 一旦未來有人註解掉寫入、或加了任何加工，這條立刻轉紅。
#
# 設計約束（Owner 明令）：
#   - 測試不得啟動伺服器、不得綁任何生產埠、不得對生產發真實請求、不得殺行程。
#   - data_root() 於 diary 模組內是 **runtime 呼叫**，可安全 monkeypatch；
#     但 `DEFAULT_DIARY_ROOT` 是 import 期凍結常數，故測試必須顯式傳入
#     writer / 或 monkeypatch `get_diary_writer`，不可依賴 reset_data_root()。

import json

import pytest

from src.soul import diary


RAW_WITH_ARTIFACTS = (
    "\n\n  **2026-10-18朝**\n\n朝的光很柔软。  \n<think>内部思考不该留下</think>\n"
)
# ⚠️ fixture 刻意帶「前後空白」。若沒有它，任何 `raw_response.strip()` /
# `.lstrip()` 之類的加工都不會改變字串，**逐位元守衛就會假綠**——
# 這正是本專案反覆實測到的守衛失效型態（MUT-03 實測：未帶空白時 5 passed）。
RAW_HAS_EDGE_WHITESPACE = RAW_WITH_ARTIFACTS != RAW_WITH_ARTIFACTS.strip()


def _reader(tmp_path, monkeypatch):
    """把 diary 的 data_root 導向 tmp，並回傳 sidecar 路徑。"""
    monkeypatch.setattr(diary, "data_root", lambda: tmp_path / "data")
    return tmp_path / "data" / "observability" / diary.RAW_RESPONSE_SIDECAR_FILENAME


def _patch_llm(monkeypatch, value):
    async def _fake(system, user, api_key, agent_id=None):
        return value

    monkeypatch.setattr(diary, "_call_llm_for_diary", _fake)


@pytest.mark.asyncio
async def test_e1_raw_response_is_persisted_byte_identical(tmp_path, monkeypatch):
    """主契約：sidecar 的 raw_response 必須與 LLM 回應**逐位元相同**。"""
    # 反假綩：先證明這個 fixture 本身有能力偵測到 strip/lstrip 加工。
    assert RAW_HAS_EDGE_WHITESPACE, (
        "fixture 沒有前後空白 → 任何 strip 加工都不會改變字串 → 逐位元守衛形同處詛"
    )
    sidecar = _reader(tmp_path, monkeypatch)
    _patch_llm(monkeypatch, RAW_WITH_ARTIFACTS)
    writer = diary.DiaryWriter(data_dir=str(tmp_path / "data" / "soul"))

    await diary.generate_diary_entry("agent_aoi", "morning", writer=writer)

    assert sidecar.exists(), "sidecar 未產生 —— 觀測端失效，SOUL-BUG-011 將永遠無法定案"
    lines = [l for l in sidecar.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["raw_response"] == RAW_WITH_ARTIFACTS, "raw_response 被加工過 —— 這正是本 sidecar 要消除的盲區"
    assert rec["agent_id"] == "agent_aoi"
    assert rec["slot"] == "morning"
    assert rec["ts"]


@pytest.mark.asyncio
async def test_e2_never_blocks_main_write_path(tmp_path, monkeypatch):
    """Fail-safe 鐵律：sidecar 寫入失敗**絕不可**阻斷 diary 主路徑落盤。"""
    _reader(tmp_path, monkeypatch)
    _patch_llm(monkeypatch, RAW_WITH_ARTIFACTS)

    def _boom(*a, **k):
        raise OSError("disk on fire")

    monkeypatch.setattr(diary, "data_root", _boom)
    writer = diary.DiaryWriter(data_dir=str(tmp_path / "data" / "soul"))

    out = await diary.generate_diary_entry("agent_aoi", "morning", writer=writer)

    assert out is not None and out.exists(), "sidecar 壞掉導致主路徑停擺 —— 違反 fail-safe 鐵律"


@pytest.mark.asyncio
async def test_e3_retry_attempt_is_also_captured(tmp_path, monkeypatch):
    """think_only retry 的第二次回應也要落盤，否則最需要觀測的那次會漏掉。"""
    sidecar = _reader(tmp_path, monkeypatch)
    calls = {"n": 0}

    async def _fake(system, user, api_key, agent_id=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return "<think>只有思考</think>"
        return RAW_WITH_ARTIFACTS

    monkeypatch.setattr(diary, "_call_llm_for_diary", _fake)
    writer = diary.DiaryWriter(data_dir=str(tmp_path / "data" / "soul"))

    await diary.generate_diary_entry("agent_aoi", "morning", writer=writer)

    lines = [l for l in sidecar.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(lines) == 2, "retry 的第二次回應未落盤"
    recs = [json.loads(l) for l in lines]
    assert recs[0]["raw_response"] == "<think>只有思考</think>"
    assert recs[1]["raw_response"] == RAW_WITH_ARTIFACTS


@pytest.mark.asyncio
async def test_e4_empty_response_writes_nothing(tmp_path, monkeypatch):
    """LLM 回空（None）時不應寫 sidecar —— 避免以空字串稀釋觀測資料。"""
    sidecar = _reader(tmp_path, monkeypatch)
    _patch_llm(monkeypatch, None)
    writer = diary.DiaryWriter(data_dir=str(tmp_path / "data" / "soul"))

    await diary.generate_diary_entry("agent_aoi", "morning", writer=writer)

    assert not sidecar.exists() or sidecar.read_text(encoding="utf-8").strip() == ""


def test_e5_sidecar_has_no_consumer():
    """sidecar 不得被任何產品路徑消費——否則它就不是純觀測。"""
    from src.inner_life import emergent_projection, elevation_recall

    for mod in (emergent_projection, elevation_recall, diary):
        src = (mod.__file__ or "")
        with open(src, encoding="utf-8") as fh:
            body = fh.read()
        # 允許「寫入端」自稱，但不得出現在讀取/投影/召回的消費點
        assert diary.RAW_RESPONSE_SIDECAR_FILENAME not in body or mod is diary, (
            f"{mod.__name__} 消費了 raw response sidecar —— 它必須保持純觀測"
        )