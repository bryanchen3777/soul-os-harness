"""SIMULATION: does W2 (news_event scoring) make `accepted` go back up?

門檻一的三條腿之一。W2 已於 13:08 部署，但世界感知的評估要等
perception middleware 的輪詢（real time）才有生產證據。

Owner 2026-10-03 規則：需要時間的就跑模擬。

方法（STRICT 0 production mutation）:
  - isolated data_root（tempdir）
  - 合成 WorldEvent（news_event / weather），餵 `src.world.perception` 真實評分路徑
  - 比較 W2 前（news baseline 缺席 → default 0.10）與 W2 後（0.30）的 accepted
  - **同一個事件、同一份 code**，只切 baseline 表內容
"""
from __future__ import annotations

import datetime
import importlib
import json
import pathlib
import sys
import tempfile

REPO = pathlib.Path(r"C:\Users\bbfcc\.local\bin\soul-os-harness")
sys.path.insert(0, str(REPO))

iso = tempfile.mkdtemp(prefix="w2_sim_")
import os  # noqa: E402

os.environ["SOUL_OS_DATA_DIR"] = iso

from src.paths import reset_data_root  # noqa: E402

reset_data_root()

from src.world import perception as P  # noqa: E402


def make_event(etype: str, novel: bool = True):
    """合成 WorldEvent（依 perception 所需的真實建構子）。"""
    import inspect

    sig = inspect.signature(P.WorldEvent.__init__)
    kwargs = {}
    for name, prm in sig.parameters.items():
        if name == "self":
            continue
        if prm.default is not inspect.Parameter.empty:
            continue
        if name in ("type", "event_type"):
            kwargs[name] = etype
        elif name in ("ts", "timestamp", "happened_at"):
            kwargs[name] = "2026-10-03T14:22:00+00:00"
        elif name in ("source", "world_source"):
            kwargs[name] = "news" if etype.endswith("news_event") else "weather"
        elif name in ("novelty_id", "event_id"):
            kwargs[name] = f"nov-{etype}-{'n' if novel else 'd'}"
        elif name == "title":
            kwargs[name] = "某地發生了一則新聞"
        elif name in ("summary", "content", "body"):
            kwargs[name] = "某地發生了一則新聞事件。特朗普宣布了一項協議。"
        else:
            kwargs[name] = ""
    return P.WorldEvent(**kwargs)


def score(ev):
    # 🔴 參數名依 `perception.py:467-476` 真實簽章
    #    （初版誤寫 `user_context_keywords`，正確為 `current_user_context_keywords`）
    s = P.compute_scores(
        event=ev,
        novelty_count=1,
        current_user_context_keywords=[],
    )
    ok, reason = P.should_accept(s, threshold=P.DEFAULT_ACCEPT_THRESHOLD)
    return s, ok, reason


def show(label, etype, baseline):
    """在指定 baseline 下評分。"""
    importlib.reload  # noqa: B018
    saved = P.TYPE_BASELINE_RELEVANCE.get(etype)
    P.TYPE_BASELINE_RELEVANCE[etype] = baseline
    try:
        s, ok, reason = score(make_event(etype))
        print(f"  {label:<26} rel={s.relevance:.2f}  final={s.final():.4f}  "
              f"accepted={ok}")
        print(f"  {'':<26} {reason[:96]}")
        return s.final(), ok
    finally:
        if saved is None:
            P.TYPE_BASELINE_RELEVANCE.pop(etype, None)
        else:
            P.TYPE_BASELINE_RELEVANCE[etype] = saved


print("=" * 78)
print("SIMULATION — W2：news_event 納入 TYPE_BASELINE_RELEVANCE 的效果")
print("=" * 78)
print(f"  DEFAULT_ACCEPT_THRESHOLD = {P.DEFAULT_ACCEPT_THRESHOLD}")
print(f"  現行 TYPE_BASELINE_RELEVANCE['news_event'] = "
      f"{P.TYPE_BASELINE_RELEVANCE.get('news_event')}")
print(f"  現行 weather_temp_change  = "
      f"{P.TYPE_BASELINE_RELEVANCE.get('weather_temp_change')}")
print()

print("── W2 前（news_event 不在表內 → 落 default 0.10）──")
f_before, ok_before = show("news_event (baseline 0.10)", "news_event", 0.10)
print()
print("── W2 後（news_event = 0.30）──")
f_after, ok_after = show("news_event (baseline 0.30)", "news_event", 0.30)
print()
print("── 對照組：Bry 8/7 拍板不得調高的兩項（W2 必須保持 0.05）──")
f_w_t, ok_w_t = show("weather_temp_change 0.05", "weather_temp_change", 0.05)
f_cn, ok_cn = show("celebrity_news 0.05", "celebrity_news", 0.05)

print()
print("=" * 78)
print("結論")
print("=" * 78)
print(f"  news_event  W2 前：final={f_before:.4f} accepted={ok_before}")
print(f"  news_event  W2 後：final={f_after:.4f} accepted={ok_after}")
print(f"  → W2 讓 news_event {'通過門檻 ✅' if ok_after else '仍未通過 ❌'}")
print()
print(f"  weather_temp_change final={f_w_t:.4f} accepted={ok_w_t}"
      f"  （應維持拒絕）{'✅' if not ok_w_t else '❌ 違反 8/7 拍板'}")
print(f"  celebrity_news      final={f_cn:.4f} accepted={ok_cn}"
      f"  （應維持拒絕）{'✅' if not ok_cn else '❌ 違反 8/7 拍板'}")

import shutil  # noqa: E402

shutil.rmtree(iso, ignore_errors=True)
os.environ.pop("SOUL_OS_DATA_DIR", None)
reset_data_root()
