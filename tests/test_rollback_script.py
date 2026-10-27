"""`scripts/rollback.sh`, run against a stand-in `docker` that logs each call instead of making it."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "rollback.sh"
BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(BASH is None, reason="needs bash")

# The target answers `db heads`; `test -f` and the pack run exit as the test says.
DOCKER = """#!/usr/bin/env bash
echo "BOT_IMAGE=${BOT_IMAGE:-} $*" >>"$DOCKER_LOG"
case "$*" in
    *"db heads"*) echo "bot=0005 chatlog=0002" ;;
    *"--entrypoint test"*) exit "$HAS_PACK_SCRIPT" ;;
    *starter-pack*) exit "$PACK_EXIT" ;;
esac
"""


def _rollback(tmp_path: Path, *, has_pack_script: bool = True, pack_exit: int = 0) -> tuple[int, list[str]]:
    (tmp_path / "scripts").mkdir()
    shutil.copy(SCRIPT, tmp_path / "scripts" / "rollback.sh")
    (tmp_path / ".env").write_text("BOT_IMAGE=bot:new\n")
    stubs = tmp_path / "bin"
    stubs.mkdir()
    (stubs / "docker").write_text(DOCKER, newline="\n")
    (stubs / "docker").chmod(0o755)
    log = tmp_path / "docker.log"
    env = {k: v for k, v in os.environ.items() if k not in ("BOT_IMAGE", "COMPOSE_FILE")}
    env |= {
        "PATH": f"{stubs}{os.pathsep}{env['PATH']}",
        "DOCKER_LOG": str(log),
        "HAS_PACK_SCRIPT": "0" if has_pack_script else "1",
        "PACK_EXIT": str(pack_exit),
    }
    assert BASH is not None
    done = subprocess.run(
        [BASH, "scripts/rollback.sh", "bot:old"], cwd=tmp_path, env=env, capture_output=True, text=True
    )
    assert (tmp_path / ".env").read_text() == "BOT_IMAGE=bot:old\n"
    return done.returncode, log.read_text().splitlines()


def _index(calls: list[str], needle: str) -> int:
    return next(i for i, call in enumerate(calls) if needle in call)


def test_the_target_reinstalls_its_own_packs_after_the_downgrade(tmp_path: Path) -> None:
    code, calls = _rollback(tmp_path)
    assert code == 0
    downgrade, pack, up = (_index(calls, s) for s in ("db downgrade", "starter-pack", "up -d"))
    assert downgrade < pack < up
    assert calls[downgrade].startswith("BOT_IMAGE= ")  # the current image undoes its own migrations
    assert calls[pack].startswith("BOT_IMAGE=bot:old ") and "--no-deps starter-pack" in calls[pack]


def test_an_image_without_a_pack_script_just_starts(tmp_path: Path) -> None:
    code, calls = _rollback(tmp_path, has_pack_script=False)
    assert code == 0
    assert not any("starter-pack" in call for call in calls)
    assert any("up -d" in call for call in calls)


def test_a_bot_never_signed_in_has_nothing_to_reinstall(tmp_path: Path) -> None:
    assert _rollback(tmp_path, pack_exit=2)[0] == 0


def test_a_failed_pack_still_starts_the_bot_and_says_so(tmp_path: Path) -> None:
    code, calls = _rollback(tmp_path, pack_exit=1)
    assert code == 1
    assert any("up -d" in call for call in calls)
