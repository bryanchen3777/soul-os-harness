"""VC Audit v1 — 隔離 instance 啟動器.

在 run 目錄下啟動一個與生產完全隔離的 Voice Companion instance，用於
REST / LIVE A/B 實驗（見 docs/VC-AUDIT-V1-WORKTICKET.md §6）。

隔離手段（全部不修改 clients/voice_companion/ 的生產原始碼）：

1. **埠**：由 ``--port`` 指定。硬性拒絕 8000 / 8765 / 8766 / 8767。
   加上 ``--host 127.0.0.1``（loopback ⇒ web_server.validate_host_and_token
   不要求 VOICE_WEB_TOKEN）。
2. **log**：``web_server.setup_logging()`` 的路徑是模組層常數
   ``LOG_DIR / f"vc_{agent_id}.log"``，沒有 CLI 覆寫入口。若不處理，
   以 rem profile 啟動的 audit instance 會把紀錄寫進**生產同一個**
   ``clients/voice_companion/logs/vc_agent_rem.log``，造成：
     - 歷史 85 回合證據檔被污染
     - A/B 兩臂的紀錄交錯，無法逐回合對應
   本模組在呼叫 ``main()`` **之前**改寫 ``web_server.LOG_DIR`` 指向本次
   run 目錄。這是從外部重綁定常數，不是修改 logger 行為
   （編碼、格式、level 一律不動）。
3. **config**：由 profile 衍生一份 audit config 到 run 目錄，
   **僅**覆寫 ``web.port`` 與 ``web.https``。衍生檔與其差異一併落盤，
   作為「除埠與 TLS 外完全相同」的證據。

用法（由 runner.py 呼叫，亦可手動）::

    python -m scripts.vc_audit.launch_instance \
        --profile clients/voice_companion/profiles/rem.json \
        --port 8790 --tts rest --run-dir <run_dir>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# 生產埠：任何情況下都不得綁定（AGENTS.md 鐵律 #5）
FORBIDDEN_PORTS = {8000, 8765, 8766, 8767}

# 允許覆寫的 config 鍵白名單：只准動埠與 TLS，其餘一律保持原樣，
# 否則 A/B 的「唯一受控變因」就不成立。
ALLOWED_CONFIG_OVERRIDES = ("web.port", "web.https")


def derive_config(profile_path: Path, port: int, https: bool) -> Dict[str, Any]:
    """讀入 profile，僅覆寫 web.port / web.https。"""
    with open(profile_path, encoding="utf-8") as fh:
        cfg: Dict[str, Any] = json.load(fh)
    before = {
        "web.port": (cfg.get("web") or {}).get("port"),
        "web.https": (cfg.get("web") or {}).get("https"),
    }
    cfg.setdefault("web", {})
    cfg["web"]["port"] = port
    cfg["web"]["https"] = https
    after = {
        "web.port": cfg["web"]["port"],
        "web.https": cfg["web"]["https"],
    }
    cfg["_audit_override_record"] = {"before": before, "after": after}
    return cfg


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="VC Audit v1 isolated instance launcher")
    ap.add_argument("--profile", required=True, help="來源 profile 路徑")
    ap.add_argument("--port", type=int, required=True, help="audit instance 埠")
    ap.add_argument("--tts", required=True, choices=["rest", "live"], help="TTS arm")
    ap.add_argument("--run-dir", required=True, help="本次 run 的輸出目錄")
    ap.add_argument("--https", action="store_true", help="使用 TLS（預設關閉）")
    args = ap.parse_args(argv)

    if args.port in FORBIDDEN_PORTS:
        print(
            f"[REFUSE] port {args.port} 是生產埠，audit instance 不得綁定。"
            f"（禁止清單：{sorted(FORBIDDEN_PORTS)}）",
            file=sys.stderr,
        )
        return 2

    run_dir = Path(args.run_dir).resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    log_dir = run_dir / "log"
    log_dir.mkdir(parents=True, exist_ok=True)

    profile_path = Path(args.profile).resolve()
    cfg = derive_config(profile_path, args.port, args.https)
    derived_path = run_dir / "audit_config.json"
    with open(derived_path, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False, indent=2)

    # 改寫 LOG_DIR 指向本次 run 目錄，避免污染生產 log。
    import clients.voice_companion.web_server as ws  # 延遲載入

    ws.LOG_DIR = log_dir

    # main() 會自行以 --config 載入衍生設定。
    instance_argv = [
        "--config", str(derived_path),
        "--port", str(args.port),
        "--host", "127.0.0.1",
        "--tts", args.tts,
    ]
    if args.https:
        instance_argv.append("--https")

    print(f"[LAUNCH] port={args.port} tts={args.tts} profile={profile_path.name}")
    print(f"[LAUNCH] derived config -> {derived_path}")
    print(f"[LAUNCH] isolated log   -> {log_dir}")
    print(f"[LAUNCH] production logs/vc_agent_rem.log 未被觸碰（LOG_DIR 已改寫）")

    return int(ws.main(instance_argv) or 0)


if __name__ == "__main__":
    raise SystemExit(main())