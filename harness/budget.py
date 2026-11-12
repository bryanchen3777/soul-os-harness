# -*- coding: utf-8 -*-
"""harness.budget — SOEB 呼叫方集中限额记账（SA-HARNESS-BUDGET-1）。

归属原则：单发 / 累计 / 发数三类限额**只活在 harness**（调用方），
**不改 Soul 生产行为**：不碰 ``src/llm/proxy.py``、``src/soul_access/*``、
``scripts/soeb/models.json`` 默认值。

精确匹配（无回落）：
  profile key = (channel, api_model, task_sha256, effort)。
  只有 budgets.json 里**逐字节相等**的已验组合才给额度；
  其它组合一律 ``BudgetError`` 拒绝，**禁回落到默认额度**。

记账纪律：
  - 单一授权 ID 独占一本账本（ledger 文件内的 ``auth_id`` 持有锁；
    不同 ID 在账本未终结前一律拒绝）。
  - 发送前**先持久化预留**（reserve 落盘 + fsync 后才允许发送）。
  - 首尾生成 / 工具 LLM / 重试**全部计数**；本票禁自动重试加额
    （失败即结算或标 UNKNOWN 并停止，不自动补发）。
  - 按协议 completion/output 结算**含 reasoning**（保守口径：
    ``billed = output + reasoning``，宁可多算不可少算）；
    缺 usage / 超时 / 存证失败 ⇒ 预留保留、标 UNKNOWN 并停止后续发送。
  - 发前存脱敏 request / manifest，发后存关联 ID / CallResult /
    usage / finish，**全部留在 harness 输出目录**（不得写入 Soul、
    不得写入 ``data/``）。
  - 工具名 / 参数 / 原始结果 / 预算日志**禁入 Soul**（B 边界不变；
    本模块不 import 任何 ``src.*``）。

本模块只用标准库：无网络、无凭证读取、无行程操作。
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


#: 已验示例额度（SA-BUDGET-1 三发实测 precedent；见 verification/sa-budget-1/ledger.json）。
#: 注意：这只是 budgets.json 里某个 profile 的**取值**，不是代码里的默认值。
#: 代码里没有任何默认额度 —— 查不到就是拒绝。
VERIFIED_SINGLE_SHOT_MAX_COMPLETION = 8000
VERIFIED_TOTAL_MAX_COMPLETION = 24000
VERIFIED_MAX_SHOTS = 3


class BudgetError(RuntimeError):
    """限额拒绝：显式配置缺失或超限。调用方必须直接拒绝发送。"""


@dataclass(frozen=True)
class ProfileKey:
    """精确匹配键。四元组必须与 budgets.json 条目逐字节相等。"""

    channel: str
    api_model: str
    task_sha256: str
    effort: str | None = None


def canonical_task_sha256(messages: list[dict[str, str]]) -> str:
    """任务杂凑：effective messages 的 canonical JSON sha256。"""
    return hashlib.sha256(
        json.dumps(messages, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def load_budgets(path: str | Path) -> dict[str, Any]:
    """载入 budgets.json 并做结构校验。不猜、不填默认值。"""
    p = Path(path)
    if not p.exists():
        raise BudgetError(f"找不到 budgets 配置：{p}")
    with open(p, encoding="utf-8") as fh:
        doc = json.load(fh)
    if not isinstance(doc, dict) or "profiles" not in doc:
        raise BudgetError(f"{p} 缺少顶层 'profiles' 映射")
    profiles = doc["profiles"]
    if not isinstance(profiles, dict) or not profiles:
        raise BudgetError(f"{p} 的 'profiles' 为空或不是映射")
    for name, entry in profiles.items():
        for field in (
            "channel",
            "api_model",
            "task_sha256",
            "single_shot_max_completion",
            "total_max_completion",
            "max_shots",
        ):
            if field not in entry:
                raise BudgetError(f"profile {name!r} 缺少字段 {field!r}（禁回落补默认）")
    return doc


def resolve_profile(doc: dict[str, Any], key: ProfileKey) -> tuple[str, dict[str, Any]]:
    """精确匹配 profile。无模糊、无前缀、无默认回落。

    匹配顺序：先按 ``profile`` 名逐个比对四元组，全等才命中。
    ``effort`` 栏：``None`` 与 ``""`` 视为同一空值，其余逐字节比对。
    """
    profiles = doc["profiles"]
    want_effort = key.effort if key.effort else None
    for name, entry in profiles.items():
        got_effort = entry.get("effort")
        got_effort = got_effort if got_effort else None
        if (
            entry.get("channel") == key.channel
            and entry.get("api_model") == key.api_model
            and entry.get("task_sha256") == key.task_sha256
            and got_effort == want_effort
        ):
            return name, entry
    raise BudgetError(
        "无显式预算配置（禁回落）："
        f"channel={key.channel!r} api_model={key.api_model!r} "
        f"task_sha256={key.task_sha256[:12]}… effort={key.effort!r}。"
        "请在 budgets.json 显式配置该组合后再跑。"
    )


def settle_billed_tokens(
    usage: dict[str, Any] | None,
) -> tuple[int | None, int | None, bool]:
    """按协议摊平后的 usage 结算（含 reasoning，保守口径）。

    回传 ``(billed_completion, billed_total, unknown)``：
      - ``unknown=True`` ⇔ usage 缺失或 output 缺失 ⇒ 调用方必须保留预留、
        标 UNKNOWN 并停止后续发送（不得当 0 处理）。
      - 正常时 ``billed_completion = output + reasoning``（任一缺省按 0 计，
        但 output 缺省已在上条判为 unknown）。
    """
    if not isinstance(usage, dict):
        return None, None, True
    out = usage.get("output_tokens")
    if out is None:
        return None, None, True
    try:
        out_i = int(out)
    except (TypeError, ValueError):
        return None, None, True
    reasoning = usage.get("reasoning_tokens") or 0
    try:
        r_i = int(reasoning)
    except (TypeError, ValueError):
        return None, None, True
    billed_completion = out_i + r_i
    total = usage.get("total_tokens")
    try:
        billed_total = int(total) if total is not None else out_i + int(usage.get("input_tokens") or 0)
    except (TypeError, ValueError):
        billed_total = billed_completion
    return billed_completion, billed_total, False


def validate_redacted_headers(headers: dict[str, str]) -> None:
    """发前存证检查：凭证明文字段不得出现明文疑似密钥。

    只认“是否仍是 <redacted:…> 形状”，不认密钥值。
    命中明文即 ``BudgetError``（拒绝发送）。
    """
    from scripts.soeb.client import is_sensitive_name  # harness 侧调用层，非 Soul

    for name, value in (headers or {}).items():
        if not is_sensitive_name(name) or not isinstance(value, str):
            continue
        _, _, token = value.partition(" ")
        check = token if token else value
        if check and not check.startswith("<redacted"):
            raise BudgetError(f"发前存证拒绝：header {name!r} 仍是明文（禁写入存证）")


class Ledger:
    """单授权 ID 独占账本。文件即锁，预留先落盘。

    文件结构：``{"auth_id": str, "halted": bool, "reservations": [...]}``。
    每个 reservation：``{"id","shot","pre_tokens","status","billed_completion",
    "billed_total","assoc":{},"reason":"","ts"}``，
    status ∈ {reserved, settled, UNKNOWN}。
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._data: dict[str, Any] = {"auth_id": None, "halted": False, "reservations": []}
        if self.path.exists():
            with open(self.path, encoding="utf-8") as fh:
                loaded = json.load(fh)
            if isinstance(loaded, dict):
                self._data = {
                    "auth_id": loaded.get("auth_id"),
                    "halted": bool(loaded.get("halted", False)),
                    "reservations": list(loaded.get("reservations") or []),
                }

    # ── 持久化（同步 + fsync；预留必须先落盘才允许发送）──
    def _persist(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(self._data, fh, ensure_ascii=False, indent=2)
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except OSError:
                pass
        os.replace(tmp, self.path)

    @property
    def auth_id(self) -> str | None:
        return self._data["auth_id"]

    @property
    def halted(self) -> bool:
        return bool(self._data["halted"])

    def acquire(self, auth_id: str) -> None:
        """独占锁：空账本认主；已有主且不同 ID ⇒ 拒绝（禁 force 越权）。"""
        if not auth_id:
            raise BudgetError("auth_id 不得为空（单授权独占锁要求显式身份）")
        holder = self._data["auth_id"]
        if holder is None:
            self._data["auth_id"] = auth_id
            self._persist()
            return
        if holder != auth_id:
            raise BudgetError(
                f"账本已被 {holder!r} 独占持有，{auth_id!r} 无权写入（禁 force 越权）"
            )

    def used_shots(self) -> int:
        return sum(1 for r in self._data["reservations"] if r["status"] in ("reserved", "settled", "UNKNOWN"))

    def used_completion(self) -> int:
        total = 0
        for r in self._data["reservations"]:
            if r["status"] == "settled" and isinstance(r.get("billed_completion"), int):
                total += r["billed_completion"]
            elif r["status"] in ("reserved", "UNKNOWN") and isinstance(r.get("pre_tokens"), int):
                total += r["pre_tokens"]
        return total

    def check_fits(
        self,
        *,
        single_shot_max_completion: int,
        total_max_completion: int,
        max_shots: int,
        planned_max_tokens: int,
        planned_new_shots: int = 1,
    ) -> None:
        """超限直接拒绝（发前判，不发送）。"""
        if self._data["halted"]:
            raise BudgetError("账本已 HALT（此前出现 UNKNOWN），停止后续发送")
        if planned_max_tokens > single_shot_max_completion:
            raise BudgetError(
                f"单发超限：本发 max_tokens={planned_max_tokens} > "
                f"single_shot_max_completion={single_shot_max_completion}，拒绝发送"
            )
        if self.used_shots() + planned_new_shots > max_shots:
            raise BudgetError(
                f"发数超限：已用 {self.used_shots()} + 本批 {planned_new_shots} > "
                f"max_shots={max_shots}，拒绝发送"
            )
        if self.used_completion() + planned_max_tokens > total_max_completion:
            raise BudgetError(
                f"累计超限：已用 {self.used_completion()} + 本发 {planned_max_tokens} > "
                f"total_max_completion={total_max_completion}，拒绝发送"
            )

    def reserve(self, *, shot_id: str, pre_tokens: int) -> str:
        """发送前预留并立即落盘。回传 reservation id。"""
        if self._data["halted"]:
            raise BudgetError("账本已 HALT，拒绝新的预留")
        rid = f"rsv-{len(self._data['reservations']) + 1:04d}-{shot_id}"
        self._data["reservations"].append(
            {
                "id": rid,
                "shot": shot_id,
                "pre_tokens": int(pre_tokens),
                "status": "reserved",
                "billed_completion": None,
                "billed_total": None,
                "assoc": {},
                "reason": "",
                "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
        )
        self._persist()
        return rid

    def settle(
        self,
        rid: str,
        *,
        billed_completion: int,
        billed_total: int,
        assoc: dict[str, Any] | None = None,
    ) -> None:
        for r in self._data["reservations"]:
            if r["id"] == rid:
                r["status"] = "settled"
                r["billed_completion"] = int(billed_completion)
                r["billed_total"] = int(billed_total)
                r["assoc"] = dict(assoc or {})
                self._persist()
                return
        raise BudgetError(f"结算失败：找不到预留 {rid!r}")

    def mark_unknown(self, rid: str, *, reason: str) -> None:
        """缺 usage / 超时 / 存证失败：预留保留、标 UNKNOWN 并 HALT（停发）。"""
        for r in self._data["reservations"]:
            if r["id"] == rid:
                r["status"] = "UNKNOWN"
                r["reason"] = reason
                self._persist()
                break
        self._data["halted"] = True
        self._persist()

    def summary(self) -> dict[str, Any]:
        return {
            "auth_id": self._data["auth_id"],
            "halted": self._data["halted"],
            "used_shots": self.used_shots(),
            "used_completion": self.used_completion(),
            "reservations": list(self._data["reservations"]),
        }


__all__ = [
    "BudgetError",
    "Ledger",
    "ProfileKey",
    "VERIFIED_MAX_SHOTS",
    "VERIFIED_SINGLE_SHOT_MAX_COMPLETION",
    "VERIFIED_TOTAL_MAX_COMPLETION",
    "canonical_task_sha256",
    "load_budgets",
    "resolve_profile",
    "settle_billed_tokens",
    "validate_redacted_headers",
]
