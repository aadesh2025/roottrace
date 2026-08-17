"""Resolving one `ImportInfo` (`ast_index.py`) to a repository path.

**Tree-verified, never guessed.** `from services import pricing` is
genuinely ambiguous from the import statement alone — `pricing` might be a
submodule (`services/pricing.py`) or a symbol defined in the package's
`__init__.py`. The only way to tell is to check what the repository actually
contains, so every candidate here is checked against the fetched `RepoTree`
before being returned — the same principle `08` §3.2's cascade and T4.2's
`resolve_against_tree` already apply to stack-frame paths, reused for imports.

Relative imports (`from .tax_client import X`, `level >= 1`) are resolved
relative to the *importing file's own directory*, climbing one directory per
level beyond 1 (`level=1` is the current package, `level=2` its parent, and
so on — matching Python's own import semantics). No case in the V1 corpus
exercises this — the fixture repository uses absolute imports throughout —
so it is implemented for correctness against real customer repositories
rather than tested against a fixture that cannot exercise it.
"""

from __future__ import annotations

import posixpath


def resolve_import(
    tree_paths: frozenset[str],
    *,
    importing_file: str,
    module: str | None,
    level: int,
    original_name: str | None,
) -> str | None:
    """The repo-relative path an import points to, or `None` if it is not a
    local file (stdlib, third-party, or genuinely unresolvable).

    `original_name` is the name as the module exports it — `TaxClient` in
    `from clients.tax_client import TaxClient as TC`, never the local alias
    `TC` — since it is the module's file, not the importing file's variable
    name, that has to match something on disk. Pass `None` for a bare
    `import module` statement, which has no such symbol to disambiguate.
    """
    base = _base_package(importing_file, level)
    if base is None:
        return None

    module_parts = module.split(".") if module else []
    module_dir = posixpath.normpath(posixpath.join(base, *module_parts)) if module_parts else base

    candidates: tuple[str, ...]
    if original_name is None:
        candidates = (f"{module_dir}.py", f"{module_dir}/__init__.py")
    else:
        # Ordered by likelihood, not by any information the import statement
        # actually carries — `original_name` could be a submodule or a
        # symbol, and only checking the tree resolves that.
        candidates = (
            f"{module_dir}/{original_name}.py",  # original_name is a submodule
            f"{module_dir}.py",  # the module itself is a single file
            f"{module_dir}/__init__.py",  # original_name is a symbol in the package
        )
    for candidate in candidates:
        normalised = posixpath.normpath(candidate)
        if normalised in tree_paths:
            return normalised
    return None


def _base_package(importing_file: str, level: int) -> str | None:
    """The directory an import is resolved relative to.

    `level=0` (absolute import) resolves from the repository root. `level=1`
    resolves from the importing file's own package directory, `level=2` from
    its parent, and so on — one climb per level beyond the first, matching
    what a leading `.`/`..`/`...` means in `from . import x` / `from .. import x`.
    """
    directory = posixpath.dirname(importing_file)
    if level <= 0:
        return ""
    parts = [part for part in directory.split("/") if part]
    climbs = level - 1
    if climbs > len(parts):
        return None
    remaining = parts[: len(parts) - climbs] if climbs else parts
    return "/".join(remaining)
