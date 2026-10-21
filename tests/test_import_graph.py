"""No two packages under doomtp_bot import each other, directly or through others.

A cycle means neither package can be read, tested or changed on its own. Imports made only for type
checking count too: they still tie the two packages' designs together.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).parent.parent / "src" / "doomtp_bot"


def _package_edges() -> dict[str, set[str]]:
    edges: dict[str, set[str]] = {}
    for path in ROOT.rglob("*.py"):
        parts = path.relative_to(ROOT).parts
        if "migrations" in parts:
            continue
        source = parts[0].removesuffix(".py")
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("doomtp_bot."):
                names = [node.module]
            elif isinstance(node, ast.Import):
                names = [alias.name for alias in node.names if alias.name.startswith("doomtp_bot.")]
            else:
                continue
            for name in names:
                target = name.split(".")[1]
                if target != source:
                    edges.setdefault(source, set()).add(target)
    return edges


def _cycles(edges: dict[str, set[str]]) -> list[list[str]]:
    """Every strongly connected group of more than one package (Tarjan)."""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    found: list[list[str]] = []

    def visit(node: str) -> None:
        index[node] = low[node] = len(index)
        stack.append(node)
        for nxt in edges.get(node, ()):
            if nxt not in index:
                visit(nxt)
                low[node] = min(low[node], low[nxt])
            elif nxt in stack:
                low[node] = min(low[node], index[nxt])
        if low[node] == index[node]:
            group = []
            while True:
                member = stack.pop()
                group.append(member)
                if member == node:
                    break
            if len(group) > 1:
                found.append(sorted(group))

    for node in sorted(edges):
        if node not in index:
            visit(node)
    return found


def test_the_cycle_finder_finds_cycles() -> None:
    assert _cycles({"a": {"b"}, "b": {"c"}, "c": {"a"}, "d": {"a"}}) == [["a", "b", "c"]]


def test_no_packages_import_each_other() -> None:
    assert _cycles(_package_edges()) == []
