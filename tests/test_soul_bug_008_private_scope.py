"""
tests/test_soul_bug_008_private_scope.py — SOUL-BUG-008 (P0) 群聊私域外洩防禦

缺陷 (2026-10-17 唯讀審查複核成立)
----------------------------------
`_build_messages_group` 的簽章**沒有 mode 參數**, 結構上無法做 scope 過濾,
於是它把三塊 1:1 私密內容搬進**公開群聊**的 system prompt:

1. `## Bry 最近訊息 (短期記憶)` —— 資料源 `_load_bry_recent()` → `_load_private()`,
   等於把 **Bry 跟該角色的私聊原文逐字**寫進公開 prompt。
2. `_format_attachment_str()` —— 「你對 Bry 的親密度目前是 N/100」。
3. `_format_temporal_anchor()` —— 「[TEMPORAL ANCHOR] 時間座標/體感經驗/關係時序」,
   其「牵挂」三態資格判定複用 M5.13-3 的 Bry 親密度 band。

爆炸半徑 = 10 隻角色 × 每一次群聊發言。

本檔的設計原則 (Bry 派工: 「在私聊歷史塞入特定敏感語料時, 斷言群聊輸出的
prompt 中絕對不出現該語料與私聊標題塊」)
------------------------------------------
- **不 mock 載入器**：直接寫一份真的 private history 檔, 走真的
  `_load_bry_recent()` / `_load_private()` 讀取路徑。
  理由：mock `_load_bry_recent` 的測試在修好之後會「因為根本沒人呼叫它」
  而綠，屬於對著 stub 驗證，對『真的會不會洩漏』沒有任何保證。
- **反空轉（anti-vacuity）**：每條「不得出現」的斷言都配一條「必須讀得到」
  的前置斷言。若語料其實沒被寫進去／讀不出來，這些測試會**先紅**，
  而不是默默通過。
- **`CONV_DIR` 必須導向 tmp**：`src/llm/proxy.py:70` 的 `CONV_DIR` 是
  **import 期就算死**的 module-level 常數，`conftest.py` 的
  `reset_data_root()` 救不回它（已於 SOUL-BUG-006 Step 4 記錄同一族陷阱）。
  不 monkeypatch 就會寫進**生產** `data/conversations/` ⇒ 本檔用
  `monkeypatch.setattr(proxy, "CONV_DIR", tmp_path)` 導流。

零退化不變量：`_build_messages_private` 的輸入/輸出/提示詞結構**逐位元不變**。
"""
import json
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.llm import proxy  # noqa: E402


AGENT_ID = "agent_b008_probe"
SOUL = "你是測試角色。"
CURRENT_TIME = "2026-10-17 22:30 週五 晚上 (EDT)"

# Bry 5 小時前講過話（非在線、非 0）——`_compute_silence_str` 在
# `bry_latest_ts <= 0` 或 Bry 在線時都會跳過沉默行，而 M7-3 attachment 與
# TA-2 anchor 都掛在沉默行之後。若不給這個時間錨，對照組會因為「沉默行
# 根本沒產生」而拿不到 sentinel，斷言就會指向錯誤的原因。
BRY_LATEST_TS = int(time.time()) - 5 * 3600

# 敏感語料：刻意用不可能自然出現在任何 system prompt 的字串, 避免誤判
SENTINEL_USER = "ZQXJK-SENTINEL-BRY-PRIVATE-7f3a91"
SENTINEL_SELF = "ZQXJK-SENTINEL-ROLE-PRIVATE-4b2e77"

PRIVATE_TITLE = "## Bry 最近訊息"


@pytest.fixture()
def private_history_dir(tmp_path, monkeypatch):
    """把 proxy.CONV_DIR 導向 tmp, 並寫入真實 private history。

    回傳 (conv_dir, agent_id, user_id)。
    """
    conv_dir = tmp_path / "conversations"
    conv_dir.mkdir(parents=True, exist_ok=True)
    # 見 module docstring: CONV_DIR 是 import 期凍結常數, 必須手動導流,
    # 否則本檔會寫進生產 data/conversations/。
    monkeypatch.setattr(proxy, "CONV_DIR", conv_dir)
    monkeypatch.setattr(proxy, "_GROUP_FILE", conv_dir / "group_chat.json")

    history = [
        {"role": "user", "content": SENTINEL_USER},
        {"role": "assistant", "content": SENTINEL_SELF},
    ]
    (conv_dir / f"bryan_{AGENT_ID}_private.json").write_text(
        json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return conv_dir, AGENT_ID, "bryan"


def _group_memory():
    """群聊歷史給一則**非私聊**訊息 —— 修法 3 的 [使用說明] 只在有非 private
    訊息時才注入；給空會讓相關斷言 vacuous pass。"""
    memory = MagicMock()
    memory.get_group_history.return_value = [
        {
            "speaker": "bryan",
            "content": "豬頭你又在偷懶",
            "is_private": False,
            "timestamp": 1_767_000_000,
        },
    ]
    return memory


def _private_memory():
    memory = MagicMock()
    memory.get_recent_with_meta.return_value = []
    memory.get_group_history.return_value = []
    return memory


def _build_group():
    return proxy._build_messages_group(
        agent_id=AGENT_ID,
        soul=SOUL,
        current_input="",
        memory_context="",
        memory=_group_memory(),
        mood=0.0,
        user_id="bryan",
        current_time=CURRENT_TIME,
        bry_latest_ts=BRY_LATEST_TS,
    )


def _build_private():
    return proxy._build_messages_private(
        agent_id=AGENT_ID,
        soul=SOUL,
        current_input="",
        memory_context="",
        memory=_private_memory(),
        mood=0.0,
        user_id="bryan",
        current_time=CURRENT_TIME,
        bry_latest_ts=BRY_LATEST_TS,
    )


def _joined(messages):
    return "\n".join(m["content"] for m in messages)


# ── 反空轉前置：語料必須真的讀得到 ──────────────────────────────────


def test_private_history_is_actually_readable(private_history_dir):
    """🔴 反空轉：若這支紅了, 下面所有「不得出現」斷言都是假的。

    走**真的** `_load_bry_recent()`（內部 `_load_private()` → 讀磁碟檔），
    證明敏感語料在測試環境中確實可被取得。
    """
    _, agent_id, user_id = private_history_dir
    loaded = proxy._load_bry_recent(agent_id, user_id)
    contents = [m.get("content") for m in loaded]
    assert SENTINEL_USER in contents, (
        "敏感語料沒被讀到 —— 後續『群聊不得洩漏』的斷言會是空轉, "
        "先修好 fixture 再说"
    )
    self_loaded = proxy._load_self_recent(agent_id, user_id)
    assert SENTINEL_SELF in [m.get("content") for m in self_loaded]


def test_private_path_still_sees_the_corpus(private_history_dir):
    """私聊路徑**保留**可見性 —— 這是「隔離」而非「刪除功能」的證據。

    若這支紅了，代表有人為了讓群聊乾淨而誤傷私聊 ⇒ 真的退化。
    """
    joined = _joined(_build_private())
    assert SENTINEL_USER in joined or SENTINEL_SELF in joined, (
        "私聊 prompt 應該看得到自己的私域語料；看不到代表 SOUL-BUG-008 "
        "修補連私聊路徑一起誤傷了（零退化不變量被破壞）"
    )


# ── 核心防禦：公開頻道零私域 ───────────────────────────────────────


def test_group_prompt_contains_no_private_corpus(private_history_dir):
    """🔴 核心：敏感私聊語料**絕對不得**出現在群聊 prompt。"""
    joined = _joined(_build_group())
    assert SENTINEL_USER not in joined, (
        "SOUL-BUG-008 重現： Bry 的私聊原文洩漏進公開群聊 prompt"
    )
    assert SENTINEL_SELF not in joined, (
        "SOUL-BUG-008 重現：角色的私聊發言洩漏進公開群聊 prompt"
    )


def test_group_prompt_excludes_bry_band_but_keeps_peer_band(
    private_history_dir, monkeypatch
):
    """🔴 效果層防禦（不只驗呼叫點）：群聊 prompt **不得**出現
    「[你跟 Bry 的關係]」，但「[你跟其他角色的關係]」必須保留。

    為什麼這條不能省：只斷言「呼叫點有傳 `include_bryan=False`」是**形狀**
    檢查。若有人把 helper 內的 `if include_bryan:` 改成恆真（參數變成
    裝飾），呼叫點斷言照樣全綠，但 Bry 的 1:1 親密度 band 又回到公開 prompt
    —— 這正是 MUT-03 變異實驗實測到的**真實覆蓋缺口**。
    """
    import src.soul.relationships as rel_module

    class _FakeStore:
        def get(self, entity_id):
            # Bry 深度信任 band：若被投射出來會是「[你跟 Bry 的關係]」
            if entity_id == rel_module.BRYAN_ENTITY_ID:
                return {"confidence": 0.95}
            return None

        def get_all(self):
            return {
                "bryan": {"confidence": 0.95},
                "agent_mai": {"confidence": 0.8},
            }

    class _FakeManager:
        def get_store(self, agent_id):
            return _FakeStore()

    monkeypatch.setattr(
        rel_module, "get_relationships_manager", lambda: _FakeManager()
    )

    joined = _joined(_build_group())

    assert "[你跟 Bry 的關係]" not in joined, (
        "群聊 prompt 出現 Bry 1:1 親密度 band（深度信任）—— 公開頻道私域外洩"
    )
    assert "深度信任" not in joined
    # 角色之間互相認識本來就是公開資訊，必須保留
    assert "[你跟其他角色的關係]" in joined, (
        "連『你跟其他角色的關係』也被砍掉了 —— 切除邊界擴張到公開資訊"
    )
    assert "agent_mai" in joined


def test_group_prompt_has_no_private_block_title(private_history_dir):
    """私聊標題塊本身不得出現（「## Bry 最近訊息」）。"""
    joined = _joined(_build_group())
    assert PRIVATE_TITLE not in joined


def test_group_never_calls_load_bry_recent(private_history_dir):
    """群聊建構過程**不得**呼叫私域載入器。

    比「輸出不含語料」更強：未來若有人改用別的私域來源，
    這條會先攔住，而不是等到輸出比對才發現。
    """
    calls = []
    real = proxy._load_bry_recent

    def _spy(agent_id, user_id, *args, **kwargs):
        calls.append((agent_id, user_id))
        return real(agent_id, user_id, *args, **kwargs)

    orig = proxy._load_bry_recent
    try:
        proxy._load_bry_recent = _spy
        _build_group()
    finally:
        proxy._load_bry_recent = orig

    assert calls == [], f"群聊路徑不該碰私域載入器，實際呼叫 {len(calls)} 次：{calls}"


# ── 呼叫點層防禦：用 sentinel 證明 helper 根本沒被群聊呼叫 ──────────


def test_group_does_not_inject_attachment_intimacy(private_history_dir, monkeypatch):
    """親密度數字不得進群聊 prompt（用 sentinel 證明 helper 未被呼叫）。"""
    sentinel = "ZQXJK-ATTACHMENT-INTIMACY-a1b2c3"

    def _fake_attachment(agent_id, *args, **kwargs):
        return sentinel

    monkeypatch.setattr(proxy, "_format_attachment_str", _fake_attachment)

    group_joined = _joined(_build_group())
    assert sentinel not in group_joined, (
        "群聊 prompt 出現 Bry 1:1 親密度（M7-3 attachment 段）"
    )

    # 對照組：私聊路徑**仍然**要有 —— 證明 sentinel 本身是會被注入的，
    # 上面的 assertNotIn 才有意義（否則 sentinel 可能永遠不會出現）。
    private_joined = _joined(_build_private())
    assert sentinel in private_joined, (
        "對照組失效：私聊路徑也拿不到 attachment sentinel，"
        "代表這個 sentinel 選得不對（而非群 chat 乾淨）"
    )


def test_group_does_not_inject_temporal_anchor(private_history_dir, monkeypatch):
    """TA-2 的 1:1 私密時間錨點不得進群聊 prompt。"""
    sentinel = "ZQXJK-TEMPORAL-ANCHOR-d4e5f6"

    def _fake_anchor(agent_id, last_interaction_ts, now, event_ts=None, *a, **kw):
        return sentinel

    monkeypatch.setattr(proxy, "_format_temporal_anchor", _fake_anchor)

    assert sentinel not in _joined(_build_group()), (
        "群聊 prompt 出現 [TEMPORAL ANCHOR] 1:1 私密時間錨點"
    )
    assert sentinel in _joined(_build_private()), (
        "對照組失效：私聊路徑拿不到 anchor sentinel，代表 sentinel 選得不對"
    )


def test_group_passes_include_bryan_false(private_history_dir, monkeypatch):
    """群聊呼叫關係投影時必須顯式帶 `include_bryan=False`。

    同時斷言私聊路徑**沒有**帶這個旗標（即走預設 True），守住零退化不變量。
    """
    seen = []

    def _spy(agent_id, *args, **kwargs):
        seen.append((agent_id, args, dict(kwargs)))
        return "[ZQXJK-RELSENTINEL]"

    monkeypatch.setattr(proxy, "_format_relationship_block", _spy)

    _build_group()
    assert len(seen) == 1, f"群聊應恰好呼叫關係投影一次，實際 {len(seen)}"
    group_kwargs = seen[0][2]
    assert group_kwargs.get("include_bryan") is False, (
        f"群聊必須顯式關閉 Bry 1:1 親密度投影，實際 kwargs={group_kwargs}"
    )

    seen.clear()
    _build_private()
    assert len(seen) == 1, f"私聊應恰好呼叫關係投影一次，實際 {len(seen)}"
    private_kwargs = seen[0][2]
    assert not private_kwargs, (
        f"私聊路徑必須走預設（不傳旗標）以保持逐位元不變，實際 kwargs={private_kwargs}"
    )


# ── 保留項：修法 3 的群聊歷史 [使用說明] 不受本次修補影響 ────────────


def test_group_history_framing_survives(private_history_dir):
    """公開資訊的群聊歷史 [使用說明] 必須仍在（避免連帶誤刪）。"""
    joined = _joined(_build_group())
    assert "[使用說明] 以下是群聊中其他人" in joined


def test_group_keeps_silence_duration_line(private_history_dir):
    """🔵 刻意**保留**的公開資訊：沉默時長行必須仍在群聊 prompt。

    「距離 Bry 上次跟你說話已經 N 小時」只是時間現象，不是私密內容
    （Owner 2026-10-17 裁示：只切 1:1 親密度／私聊原文）。這條釘住
    「切除邊界沒有擴張」——防止後來有人把整個時間區塊一併砍掉。
    """
    joined = _joined(_build_group())
    assert "距離 Bry 上次跟你說話已經" in joined, (
        "沉默時長行屬公開時間現象, 不在 SOUL-BUG-008 的切除範圍內"
    )