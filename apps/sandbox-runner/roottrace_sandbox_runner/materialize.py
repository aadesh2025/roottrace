"""Writes a `{repo_path: content}` mapping onto disk under `/work` (`07`
§7 B10: "`runner.py` reads [the input] at startup and materialises the
working tree into `/work` itself").

Deliberately reusable rather than baked into one call site: G4/G5 (`07`
§6, T6.4) need the *original* tree materialised first, then swapped for
the *patched* tree — the same primitive, called twice with different
file sets, not two different code paths."""

from __future__ import annotations

from pathlib import Path


class PathEscapeError(Exception):
    """A `repo_path` resolved outside `work_dir` — every path in the input
    bundle is AI-authored, and therefore hostile by default (`CLAUDE.md`:
    "fence them, validate them, never execute them"). `../../etc/passwd`
    is rejected here rather than trusted because it was validated upstream
    (`03` §S7's scope allowlist, T5.4) — this is the actual filesystem
    write, and a defence that only exists one layer up is not a defence
    that exists here."""


def materialize_tree(work_dir: Path, files: dict[str, str]) -> None:
    """Overwrites `work_dir` with exactly `files` — every existing regular
    file under `work_dir` not present in `files` is removed first, so a
    second call (the original-then-patched swap G4/G5 will do) cannot leave
    a stale file the first materialisation created but the second no
    longer wants to."""
    resolved_root = work_dir.resolve()
    targets: dict[str, Path] = {}
    for repo_path in files:
        target = (work_dir / repo_path).resolve()
        if target != resolved_root and resolved_root not in target.parents:
            raise PathEscapeError(f"{repo_path!r} resolves outside {work_dir}")
        targets[repo_path] = target

    existing = {p for p in work_dir.rglob("*") if p.is_file()}
    wanted = set(targets.values())
    for stale in existing - wanted:
        stale.unlink()

    for repo_path, content in files.items():
        target = targets[repo_path]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
