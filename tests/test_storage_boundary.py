"""存储后端边界测试。

「先本地、之后再考虑服务器」的分期决策要求：**把 SQLite 关在 ``store.py`` 之内**。

若其他模块（尤其是界面层 ``app.py``）import sqlite3，将来换服务器数据库时
就必须逐个改动它们 —— 迁移成本会在代码里慢慢积累，直到某天变得很贵。

这条测试把那个成本挡在门外。它用 AST 解析而非文本匹配，
以免注释或字符串里的 "sqlite3" 造成误报。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parent.parent / "lifeng"

#: 唯一允许持有存储后端细节的模块。
BACKEND_OWNER = "store.py"


def _modules() -> list[Path]:
    return sorted(PACKAGE.glob("*.py"))


def test_scanned_something() -> None:
    """防止测试本身因扫不到文件而空转。"""
    assert len(_modules()) >= 5, f"只扫到 {len(_modules())} 个模块，测试失效"


def _top_level_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names


@pytest.mark.parametrize("path", _modules(), ids=lambda p: p.name)
def test_backend_is_encapsulated(path: Path) -> None:
    imported = _top_level_imports(path)

    if path.name == BACKEND_OWNER:
        assert "sqlite3" in imported, (
            f"{BACKEND_OWNER} 应当自己持有 sqlite3 —— 它是后端的唯一归属地"
        )
    else:
        assert "sqlite3" not in imported, (
            f"{path.name} 不应 import sqlite3。\n"
            "存储后端必须关在 store.py 内，调用方只使用 StoreError。\n"
            "否则「先本地、之后再考虑服务器」的迁移成本会扩散到各模块。"
        )
