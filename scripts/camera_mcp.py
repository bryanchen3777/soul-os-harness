"""
scripts/camera_mcp.py — MS-2 camera-mcp：相机单帧视觉感知薄 MCP server（IMPLEMENTATION）

定位（照 docs/MULTIMODAL-PERCEPTION-CONTRACT.md §4，MS-1 D4/D8）：
  独立 stdio MCP server 进程（不 import 进主进程）。仅实现 initialize /
  tools/list / tools/call / shutdown（stdio transport，由 mcp SDK MCPServer
  承接）。

  工具（§4.2 schema）：
    - camera_capture：单帧抓拍（按需单次，Motive 驱动）+ scene_tag 粗分类；
      抓帧后立即释放相机句柄（杜绝背景常驻占用）。不做常开流、不做定时流。

  运行规范（§4.3 硬约束）：
    - 单次调用（single-shot）：一次调用一个结果；无流式/长连接/内部循环。
    - 5s 硬超时：单帧 read 毫秒级；客户端 registry.call 侧另有 5s 超时。
    - 无状态清理：jpg 写入 server 私有 OS temp 目录，调用结束（含异常路径）
      立即删除；进程退出兜底清理；主进程不持有本 server 内部状态。
    - fail-closed 降级：相机不可用/读取失败 → 抛 RuntimeError → MCP isError →
      客户端 ToolRegistry 降级路径处理（不阻塞主循环）。

  感知边界不变量（MS-1 D1，锁死）：
    - 本进程 0 import EventBus / SpeakerToken / LLM。
    - 结果只通过 MCP 返回结构化数据；image_ref 仅供 trace 观察，不注入 prompt
      （避免隐私文字化）；由 Actuator observe 路径以 Ambient Observation 注入
      Perception/Context；严禁直通 USER_MESSAGE。

  scene_tag（§4.2：粗分类，供 D9 语义桶 novelty_id）：
    {empty_room, person, activity, other}——v1 用 opencv 启发式（亮度/方差/
    人脸检测 haarcascade）；pet 检测不在 v1（无视觉 LLM，落到 other/activity）。

  可测试性（非生产行为）：
    - SOUL_CAMERA_MOCK=1：合成灰帧（无相机环境跑通全链路）；
      SOUL_CAMERA_MOCK_TAG=person 等：强制 scene_tag。
    - 生产默认 0 → 真实 cv2.VideoCapture(SOUL_CAMERA_INDEX 或 0)。

  启动（由 ToolRegistry 客户端以 stdio 子进程拉起）：
    <venv>/Scripts/python.exe scripts/camera_mcp.py
"""
from __future__ import annotations

import asyncio
import atexit
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

try:
    from mcp.server.mcpserver import MCPServer
except ImportError:  # pragma: no cover
    sys.stderr.write("mcp SDK 未安裝（pip install mcp）\n")
    raise

try:
    import numpy as np
    import cv2
except ImportError:  # pragma: no cover
    np = None  # type: ignore
    cv2 = None  # type: ignore

# ───────────────────────────────────────────────────────────
# 配置
# ───────────────────────────────────────────────────────────

CAMERA_INDEX = int(os.environ.get("SOUL_CAMERA_INDEX", "0"))
CAMERA_MOCK = os.environ.get("SOUL_CAMERA_MOCK", "0") == "1"
CAMERA_MOCK_TAG = os.environ.get("SOUL_CAMERA_MOCK_TAG", "")

# 私有 OS temp 工作目录（进程级；退出兜底清理）
_TEMP_DIR = Path(tempfile.mkdtemp(prefix="soul-os-camera-mcp-"))

# 人脸检测分类器（懒加载；opencv 自带 haarcascade，加载失败忽略 → 跳过 person 检测）
_FACE_CASCADE: Any = None
_FACE_CASCADE_TRIED = False

# scene_tag 启发式阈值
_EMPTY_BRIGHTNESS_MAX = 24.0   # 平均亮度低于此且方差小 → 空房间/过暗
_EMPTY_STD_MAX = 6.0           # 灰度标准差低于此 → 均匀场景（空房间）
_FACE_SCALE_FACTOR = 1.1
_FACE_MIN_NEIGHBORS = 5


def _cleanup_temp() -> None:
    shutil.rmtree(_TEMP_DIR, ignore_errors=True)


atexit.register(_cleanup_temp)


def _utc_ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


# ───────────────────────────────────────────────────────────
# 采集层（cv2 单帧；mock 合成用于无相机测试）
# ───────────────────────────────────────────────────────────

def _capture_frame_real() -> "np.ndarray":
    if cv2 is None:  # pragma: no cover
        raise RuntimeError("opencv 未安裝")
    # 打开相机 → 单帧 → finally 立即释放句柄（杜绝背景常驻占用）
    cap = cv2.VideoCapture(CAMERA_INDEX)
    try:
        ok, frame = cap.read()
        if not ok or frame is None:
            raise RuntimeError(f"camera read failed (index={CAMERA_INDEX})")
        return frame
    finally:
        cap.release()


def _capture_frame_mock() -> "np.ndarray":
    if np is None or cv2 is None:  # pragma: no cover
        raise RuntimeError("numpy/opencv 未安裝（mock 抓幀需要）")
    # 640x480 灰帧（模拟空房间画面）
    return np.full((480, 640, 3), 96, dtype=np.uint8)


def _capture_frame() -> "np.ndarray":
    if CAMERA_MOCK:
        return _capture_frame_mock()
    return _capture_frame_real()


def _load_face_cascade() -> Any:
    global _FACE_CASCADE, _FACE_CASCADE_TRIED
    if _FACE_CASCADE_TRIED:
        return _FACE_CASCADE
    _FACE_CASCADE_TRIED = True
    if cv2 is None:
        return None
    try:
        cascade_path = os.path.join(
            cv2.data.haarcascades, "haarcascade_frontalface_default.xml"
        )
        _FACE_CASCADE = cv2.CascadeClassifier(cascade_path)
        if _FACE_CASCADE.empty():  # pragma: no cover
            _FACE_CASCADE = None
    except Exception:  # pragma: no cover
        _FACE_CASCADE = None
    return _FACE_CASCADE


# ───────────────────────────────────────────────────────────
# scene_tag 粗分类（§4.2；启发式，无视觉 LLM）
# ───────────────────────────────────────────────────────────

def classify_scene(frame: "np.ndarray", tag_hint: str = "") -> str:
    """scene_tag ∈ {empty_room, person, activity, other}（v1 粗分类）。

    - tag_hint 非空 → 直接采用（调用方可指定 room/door/desk 等上下文桶）。
    - 人脸检测命中 → person。
    - 平均亮度很低（过暗）或灰度方差很小（均匀场景）→ empty_room。
    - Canny 边缘密度高 → activity。
    - 其余 → other。
    """
    if tag_hint and tag_hint.strip():
        return tag_hint.strip()[:24]
    if np is None or cv2 is None:  # pragma: no cover
        return "other"
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    mean_b = float(gray.mean())
    std_b = float(gray.std())

    cascade = _load_face_cascade()
    if cascade is not None:
        faces = cascade.detectMultiScale(
            gray, scaleFactor=_FACE_SCALE_FACTOR, minNeighbors=_FACE_MIN_NEIGHBORS
        )
        if len(faces) > 0:
            return "person"

    if mean_b < _EMPTY_BRIGHTNESS_MAX or std_b < _EMPTY_STD_MAX:
        return "empty_room"

    edges = cv2.Canny(gray, 80, 200)
    edge_ratio = float((edges > 0).mean())
    if edge_ratio > 0.03:
        return "activity"
    return "other"


# ───────────────────────────────────────────────────────────
# MCP Server 组装
# ───────────────────────────────────────────────────────────

def build_server() -> MCPServer:
    server = MCPServer(
        "soul-os-camera",
        instructions="camera-mcp：相机单帧视觉感知（Ambient Observation）。"
                     "抓拍按需单次，捕捉后立即释放相机句柄。",
    )

    @server.tool()
    async def camera_capture(tag_hint: str = "") -> Dict[str, Any]:
        """Capture a single frame from the camera and describe the scene briefly.
        相机单帧抓拍并返回场景摘要（视觉感知）。按需单次，捕捉后立即释放相机句柄。
        """
        frame = await asyncio.to_thread(_capture_frame)
        if CAMERA_MOCK and CAMERA_MOCK_TAG:
            scene_tag = CAMERA_MOCK_TAG[:24]
        else:
            scene_tag = classify_scene(frame, tag_hint)
        image_ref = f"frame_{_utc_ts()}.jpg"
        image_path = _TEMP_DIR / image_ref
        try:
            if cv2 is not None:
                cv2.imwrite(str(image_path), frame)
        finally:
            # 无状态清理：调用结束（含异常路径）立即删除；image_ref 仅供 trace
            image_path.unlink(missing_ok=True)
        return {
            "image_ref": image_ref,
            "scene_tag": scene_tag,
            "captured_at": _utc_ts(),
        }

    return server


def main() -> None:
    build_server().run(transport="stdio")


if __name__ == "__main__":
    main()