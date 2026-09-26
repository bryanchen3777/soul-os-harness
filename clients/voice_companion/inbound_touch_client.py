"""clients/voice_companion/inbound_touch_client.py
Soul OS — INTIMACY-GROWTH-2：VC → 主服務的 inbound TOUCH 通知客戶端。

🔴🔴 **NOT ENABLED**（C5，Owner 已裁定，不得自行放寬）🔴🔴

本模組**只交付程式**：預設 OFF、不部署、不啟用。三個關鍵事實必須寫死在
文件與程式兩處，避免後人誤讀：

  1. **token 只證明「請求來自我方授權的 VC 服務」**，**不證明說話的人是 Bry**。
  2. `asr-ok` / `gen == self._generation` 的檢查**只證明「有一個有效語音進來」**，
     **不構成** Bry 的身分鑑別 ⇒ 這些檢查**不得冒充**身分鑑別。
  3. VC **不得**直接新增對 `agent_emotions` 的寫入（§10）。本模組**完全不碰 DB**，
     它只發一個帶 Bearer token 的 HTTP 通知給主服務的**最小受鑑權端點**
     `POST /internal/vc/inbound_touch`，由主服務決定要不要落帳。

因此本模組的正確定位是：**候選路徑的程式交付**，不是「跨進程閉環已完成」。
在端點回應未被 VC 服務實際收到之前（旗標仍 OFF），閉環**沒有**成立。

設計要點
--------
- `event_id`：每筆通知唯一的冪等鍵（uuid4），可追溯；retry 沿用**同一個**
  event_id，讓伺服器端可去重（伺服器端 ledger 以 `(agent_id, 原到期點)` 為冪等鍵）。
- retry：有界指數退避（`max_attempts` + `base_delay`，上限 `max_delay`）。
  **不無限重試**。
- ACK：只有 HTTP 2xx 才算 ACK。逾時 / 非 2xx / 連線錯誤 ⇒ 未 ACK。
- 降級：`enabled()` 為 False、或 token/URL 未設定、或重試耗盡 ⇒ 回傳
  `DEGRADED`，**絕不**拋例外打斷語音管線（呼叫端是 fire-and-forget）。
  降級路徑**不**改任何本地狀態、**不**碰 DB —— 寧可漏記一次 TOUCH，
  也不製造沒有身分證據的親密度事件（fail-safe 方向與 §2 一致）。

0 新依賴：只用 stdlib `urllib`。不引入 requests / httpx。
"""
from __future__ import annotations

import json
import logging
import os
import socket
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("soul_os.vc.inbound_touch")

#: 旗標：**缺席即 OFF**（與主服務的 `INTIMACY_DECAY_ENABLED` 同一套真值語意）。
#: 🔴 客戶端與伺服器端**各自獨立**開關；兩者皆 ON 才可能送出（雙閘門）。
VC_TOUCH_ENABLED_ENV = "VC_INBOUND_TOUCH_ENABLED"

#: 主服務端點基底（如 http://127.0.0.1:8000）。**不得**由本模組硬寫生產埠。
VC_TOUCH_URL_ENV = "VC_INBOUND_TOUCH_URL"

#: 最小受鑑權用的 Bearer token（對應主服務的 `INTERNAL_VC_TOUCH_TOKEN`）。
VC_TOUCH_TOKEN_ENV = "VC_INBOUND_TOUCH_TOKEN"

TRUTHY_VALUES = frozenset({"1", "true", "yes", "on"})

#: 端點路徑（主服務側，見 scripts/run_server.py）。
INTERNAL_TOUCH_PATH = "/internal/vc/inbound_touch"

DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BASE_DELAY = 0.2
DEFAULT_MAX_DELAY = 2.0
DEFAULT_TIMEOUT = 2.0


def _env_truthy(name: str) -> bool:
    raw = os.environ.get(name)
    if not isinstance(raw, str):
        return False
    return raw.strip().lower() in TRUTHY_VALUES


def enabled() -> bool:
    """VC 通知路徑是否啟用（每次呼叫重讀 env，讓測試能 monkeypatch）。

    🔴 缺席即 OFF。預設 OFF ⇒ 整個模組在生產上**不會發出任何請求**。
    """
    return _env_truthy(VC_TOUCH_ENABLED_ENV)


@dataclass
class TouchResult:
    """一次通知的結果（呼叫端只需看 `state`）。"""

    state: str                      # "ACKED" | "DEGRADED"
    event_id: str
    attempts: int = 0
    status: Optional[int] = None
    reason: str = ""
    detail: str = ""

    @property
    def acked(self) -> bool:
        return self.state == "ACKED"


@dataclass
class InboundTouchClient:
    """把「VC 收到一段有效語音」通知給主服務的**候選**客戶端。

    🔴 再次強調：本客戶端的存在**不等於**身分鑑別已完成，也**不代表**
    閉環已成立。它只是把「有一個有效語音進來」這件事告知主服務；
    主服務端**不得**僅憑此就當成 Bry 的真人 inbound。
    """

    base_url: Optional[str] = None
    token: Optional[str] = None
    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    base_delay: float = DEFAULT_BASE_DELAY
    max_delay: float = DEFAULT_MAX_DELAY
    timeout: float = DEFAULT_TIMEOUT
    #: 可注入的睡眠函式（測試用，避免真的等）。
    sleep_fn: object = field(default=time.sleep)
    #: 可注入的開路器（測試用：模擬送達失敗）。
    opener: object = None

    def _resolve(self):
        base = self.base_url if self.base_url is not None else os.environ.get(VC_TOUCH_URL_ENV)
        tok = self.token if self.token is not None else os.environ.get(VC_TOUCH_TOKEN_ENV)
        return (base or "").strip(), (tok or "").strip()

    def notify_inbound(
        self,
        agent_id: str,
        *,
        event_id: Optional[str] = None,
        channel: str = "voice_companion",
        now: Optional[float] = None,
    ) -> TouchResult:
        """送出一筆 inbound TOUCH 通知。**永不拋例外**。

        Retry 沿用**同一個** event_id（伺服器端可去重）。
        """
        eid = event_id or uuid.uuid4().hex

        if not enabled():
            return TouchResult(
                state="DEGRADED", event_id=eid, reason="FLAG_OFF"
            )

        base, tok = self._resolve()
        if not base or not tok:
            # 降級：缺 URL 或 token ⇒ 不送（**絕不**匿名送）。
            return TouchResult(
                state="DEGRADED",
                event_id=eid,
                reason="NO_ENDPOINT_OR_TOKEN",
            )

        url = base.rstrip("/") + INTERNAL_TOUCH_PATH
        body = json.dumps(
            {
                "agent_id": agent_id,
                "event_id": eid,
                "channel": channel,
                "ts": float(now) if now is not None else time.time(),
            }
        ).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {tok}",
        }

        delay = self.base_delay
        last_status: Optional[int] = None
        last_detail = ""
        for attempt in range(1, max(1, int(self.max_attempts)) + 1):
            try:
                resp = self._post(url, body, headers)
                status = int(getattr(resp, "status", 0) or 0)
                last_status = status
                if 200 <= status < 300:
                    return TouchResult(
                        state="ACKED",
                        event_id=eid,
                        attempts=attempt,
                        status=status,
                    )
                last_detail = f"HTTP {status}"
            except urllib.error.HTTPError as exc:
                last_status = int(getattr(exc, "code", 0) or 0)
                last_detail = f"HTTPError {last_status}"
            except (urllib.error.URLError, socket.timeout, OSError, ValueError) as exc:
                last_detail = f"{type(exc).__name__}: {exc}"

            if attempt < self.max_attempts:
                self.sleep_fn(min(delay, self.max_delay))  # type: ignore[operator]
                delay = min(delay * 2.0, self.max_delay)

        # 降級：重試耗盡 ⇒ 記一行 warning，回 DEGRADED。
        # 不碰 DB、不改本地狀態、不中斷語音管線。
        logger.warning(
            "[VC-TOUCH] degraded agent=%s event=%s attempts=%d last=%s detail=%s",
            agent_id, eid, self.max_attempts, last_status, last_detail,
        )
        return TouchResult(
            state="DEGRADED",
            event_id=eid,
            attempts=self.max_attempts,
            status=last_status,
            reason="RETRIES_EXHAUSTED",
            detail=last_detail,
        )

    def _post(self, url: str, body: bytes, headers: dict):
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        if self.opener is not None:
            return self.opener(req, timeout=self.timeout)  # type: ignore[operator]
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310
            # 讀掉 body，確保連線可重用；內容本身不需要（ACK 只看狀態碼）。
            try:
                resp.read()
            except Exception:
                pass
            return resp
