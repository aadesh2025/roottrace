"""`materialize.py` (T6.1) — writing a `{repo_path: content}` mapping onto
disk under `/work` (`docs/07` §7 B10). No container needed."""

from __future__ import annotations

from pathlib import Path

import pytest

from roottrace_sandbox_runner.materialize import PathEscapeError, materialize_tree

pytestmark = pytest.mark.unit


def test_writes_every_file_with_correct_content(tmp_path: Path) -> None:
    materialize_tree(tmp_path, {"a.py": "x = 1\n", "sub/b.py": "y = 2\n"})

    assert (tmp_path / "a.py").read_text(encoding="utf-8") == "x = 1\n"
    assert (tmp_path / "sub" / "b.py").read_text(encoding="utf-8") == "y = 2\n"


def test_a_second_call_removes_files_the_first_call_wrote(tmp_path: Path) -> None:
    materialize_tree(tmp_path, {"a.py": "x = 1\n", "b.py": "y = 2\n"})
    materialize_tree(tmp_path, {"a.py": "x = 1\n"})

    assert (tmp_path / "a.py").exists()
    assert not (tmp_path / "b.py").exists()


def test_pre_existing_untracked_files_are_removed_too(tmp_path: Path) -> None:
    """A stale file left by a previous validation attempt in the same
    working directory must not survive a fresh materialisation."""
    (tmp_path / "leftover.txt").write_text("stale", encoding="utf-8")

    materialize_tree(tmp_path, {"a.py": "x = 1\n"})

    assert not (tmp_path / "leftover.txt").exists()
    assert (tmp_path / "a.py").exists()


def test_a_path_escaping_the_work_dir_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(PathEscapeError, match="resolves outside"):
        materialize_tree(tmp_path, {"../escape.py": "evil = True\n"})


def test_an_absolute_path_escaping_the_work_dir_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(PathEscapeError, match="resolves outside"):
        materialize_tree(tmp_path, {"/etc/passwd": "evil\n"})
