"""Write the generated root and event tables into docs/namespaces.md (`triggers/reference.markdown`).

Run after changing triggers/reference.py: `uv run python scripts/reference_docs.py`. A test fails while the
file is behind.
"""

from __future__ import annotations

from pathlib import Path

from doomtp_bot.triggers.reference import markdown

DOC = Path(__file__).resolve().parents[1] / "docs" / "namespaces.md"
BEGIN, END = "<!-- reference:begin -->", "<!-- reference:end -->"


def generated(text: str) -> str:
    """`text` with the block between the markers replaced by the current tables."""
    head, rest = text.split(BEGIN, 1)
    _, tail = rest.split(END, 1)
    return f"{head}{BEGIN}\n{markdown()}{END}{tail}"


def main() -> None:
    DOC.write_text(generated(DOC.read_text(encoding="utf-8")), encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
