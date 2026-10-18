"""Every built-in command documents what its result data holds (`CommandSpec.data_schema`), and the docs
can't drift from the code: each key a command puts in its data is named in some schema."""

from __future__ import annotations

import ast
import re
from pathlib import Path

from doomtp_bot import modules
from doomtp_bot.modules import builtin_registry


def test_every_builtin_declares_its_data() -> None:
    missing = [c.spec.name for c in builtin_registry().all() if not c.spec.data_schema]
    assert missing == []


def _data_keys(path: Path) -> set[str]:
    """String keys of the dict literals passed as the data of `Result.success(...)`."""
    keys: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "success"):
            continue
        if len(node.args) < 2:
            continue
        for found in ast.walk(node.args[1]):
            if isinstance(found, ast.Dict):
                keys |= {k.value for k in found.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)}
    return keys


def test_every_data_key_is_documented() -> None:
    documented = " ".join(
        f"{form} {schema}" for c in builtin_registry().all() for form, schema in c.spec.data_schema.items()
    )
    undocumented = {
        f"{path.name}: {key}"
        for path in Path(modules.__file__).parent.glob("*.py")
        for key in _data_keys(path)
        if not re.search(rf"\b{re.escape(key)}\b", documented)
    }
    assert undocumented == set()
