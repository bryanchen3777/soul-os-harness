"""
scripts/_audit_async_leaks.py
Soul OS — 异步泄漏审计 v2（找 fire-and-forget / 未 await 协程）

只扫生产代码（排除 _backup_* / _backup* 目录）。用父节点遍历精确检测：
1. create_task / ensure_future 调用点（含 loop.create_task 变体），标记是否保存引用
2. asyncio.gather / asyncio.wait / asyncio.wait_for / asyncio.to_thread /
   run_in_executor 未 await 的调用
3. 本地 async 函数调用但结果未 await 且未保存（协程被丢弃）

用法: .venv\Scripts\python.exe scripts\_audit_async_leaks.py
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOTS = [Path("src"), Path("scripts")]
EXCLUDE_DIRS = {"_backup", "_backup_d_retry_20260725", "_backup_g_revert_20260725",
                "_backup_stage0_20260718_160049", "__pycache__", ".pytest_cache"}

CREATE_TASK_NAMES = {"create_task", "ensure_future"}
GATHER_NAMES = {"gather", "wait", "wait_for", "to_thread", "run_in_executor"}


def parent_chain(tree: ast.AST, node: ast.AST) -> list[ast.AST]:
    """返回 node 的祖先链（从最近的父开始）。"""
    chain = []
    for p in ast.walk(tree):
        for child in ast.iter_child_nodes(p):
            if child is node:
                chain.append(p)
                chain.extend(parent_chain(tree, p))
                return chain
    return chain


def is_awaited(tree: ast.AST, node: ast.AST) -> bool:
    for p in parent_chain(tree, node):
        if isinstance(p, ast.Await):
            return True
    return False


def is_saved(tree: ast.AST, node: ast.AST) -> bool:
    """调用结果是否被赋值保存（Assign / AnnAssign / AugAssign / 作为参数传入）。"""
    for p in parent_chain(tree, node):
        if isinstance(p, ast.Assign):
            for t in p.targets:
                if t is node:
                    return True
        if isinstance(p, ast.AnnAssign) and p.value is node:
            return True
        if isinstance(p, ast.AugAssign) and p.target is node:
            return True
        if isinstance(p, ast.Call) and p is not node:
            # 作为另一个调用的参数（如 create_managed_task(coro)）→ 被包装
            return True
        if isinstance(p, (ast.Return, ast.Yield, ast.YieldFrom)):
            return True
    return False


def analyze_file(path: Path) -> list[str]:
    try:
        src = path.read_text(encoding="utf-8")
        tree = ast.parse(src)
    except Exception as e:
        return [f"{path}: PARSE ERROR {e}"]
    local_async = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            local_async.add(node.name)
    findings: list[str] = []
    rel = str(path).replace("\\", "/")

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        line = getattr(node, "lineno", 0)
        func = node.func
        fname = None
        if isinstance(func, ast.Attribute):
            fname = func.attr
        elif isinstance(func, ast.Name):
            fname = func.id
        if fname is None:
            continue

        if fname in CREATE_TASK_NAMES:
            saved = is_saved(tree, node)
            awaited = is_awaited(tree, node)
            tag = "SAVED" if saved else ("AWAITED" if awaited else "FIRE-AND-FORGET?")
            findings.append(f"{rel}:{line}: {fname} [{tag}] {ast.unparse(node)[:110]}")

        elif fname in GATHER_NAMES:
            if not is_awaited(tree, node) and not is_saved(tree, node):
                findings.append(f"{rel}:{line}: {fname} NOT awaited {ast.unparse(node)[:110]}")

        elif fname in local_async:
            if not is_awaited(tree, node) and not is_saved(tree, node):
                findings.append(f"{rel}:{line}: async {fname}() NOT awaited {ast.unparse(node)[:110]}")

    return findings


def main() -> None:
    all_findings: list[str] = []
    for root in ROOTS:
        for path in sorted(root.rglob("*.py")):
            if any(part in EXCLUDE_DIRS for part in path.parts):
                continue
            all_findings.extend(analyze_file(path))
    if not all_findings:
        print("CLEAN: no async leaks found")
        return
    print(f"=== {len(all_findings)} findings ===")
    for f in all_findings:
        print(f)


if __name__ == "__main__":
    main()
