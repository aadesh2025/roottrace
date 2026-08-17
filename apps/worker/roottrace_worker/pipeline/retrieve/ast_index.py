"""A local symbol table for one Python file (`03` §S5, strategy B).

**Uses the standard library's `ast`, not Tree-sitter.** `03` §S5 says "Parse
each fetched file with Tree-sitter"; V1's corpus, fixture repository, and
sandbox are Python-only, and `ast.parse` is the boring, zero-dependency,
first-class way to do exactly what strategy B needs for one language.
Tree-sitter's payoff is being uniform across languages, which only starts to
matter at V5 (Go/Java/Ruby, `18` §8) — introducing a compiled-grammar
dependency now, for a single language V1 will ever parse, would be paying
that cost years before it earns anything. Recorded in `03` §S5 as an
implementation note, the same pattern as T4.1's LLM-gateway seam.

What this module builds is deliberately narrow: enough to answer "what
function encloses this line", "what does this function call", "what types
does it reference", and "where does this import point" for one file at a
time. It does not attempt general static analysis — no cross-file type
inference, no dataflow. Strategy B's own resolution (`resolve_definition` in
`strategies.py`) compensates for that narrowness with the gateway's
`search_symbol`, the same way "GitHub code search" would for a human.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class FunctionInfo:
    name: str
    qualname: str  # "ClassName.method" for methods, else same as `name`
    start_line: int
    end_line: int
    node: ast.FunctionDef | ast.AsyncFunctionDef = field(repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class ClassInfo:
    name: str
    start_line: int
    end_line: int


@dataclass(frozen=True, slots=True)
class ImportInfo:
    """One `from module import name` or `import module` binding.

    Two names are carried for a `from`-import because they answer different
    questions: `imported_name` is the identifier the *code* uses (after any
    `as`), for matching against a callee or type name found in a call graph
    walk; `original_name` is the identifier *the module actually exports*,
    for building a candidate file path (`import_resolution.py`) — the two
    diverge under `from clients.tax_client import TaxClient as TC`, and only
    `original_name` names a file that can exist on disk. `original_name` is
    `None` for a bare `import module`, which has no such ambiguity to begin
    with — `module` alone resolves to a file or package.

    Resolution itself (checking a candidate against the fetched repository
    tree) lives in `import_resolution.py`, not here — this is parse output,
    not a decision about the repository's contents.
    """

    imported_name: str  # the local name this binding introduces
    module: str | None  # dotted module, e.g. "clients.tax_client"; None only if malformed
    original_name: str | None  # the name as exported by `module`; None for a bare `import x`
    level: int  # 0 = absolute, >=1 = relative import's leading dots


@dataclass(frozen=True, slots=True)
class ModuleIndex:
    functions: tuple[FunctionInfo, ...]
    classes: tuple[ClassInfo, ...]
    imports: tuple[ImportInfo, ...]


def build_index(content: str) -> ModuleIndex | None:
    """Parse one file's source. `None` on a syntax error — the file is
    skipped, never a crash: retrieval degrading to fewer graph edges is
    acceptable; a stage that dies on one malformed file is not."""
    try:
        tree = ast.parse(content)
    except (SyntaxError, ValueError):
        return None

    functions: list[FunctionInfo] = []
    classes: list[ClassInfo] = []
    imports: list[ImportInfo] = []

    class _Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self._class_stack: list[str] = []

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            classes.append(
                ClassInfo(name=node.name, start_line=node.lineno, end_line=_end_line(node))
            )
            self._class_stack.append(node.name)
            self.generic_visit(node)
            self._class_stack.pop()

        def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
            qualname = f"{self._class_stack[-1]}.{node.name}" if self._class_stack else node.name
            functions.append(
                FunctionInfo(
                    name=node.name,
                    qualname=qualname,
                    start_line=node.lineno,
                    end_line=_end_line(node),
                    node=node,
                )
            )
            # Deliberately no `generic_visit` into nested functions. A local
            # helper defined inside another function is not a symbol anything
            # outside that function can call, so it is not a separate node in
            # the graph — its calls are already covered by analysing the
            # enclosing function's body.

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self._visit_function(node)

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            self._visit_function(node)

        def visit_Import(self, node: ast.Import) -> None:
            for alias in node.names:
                local = alias.asname or alias.name.split(".")[0]
                imports.append(
                    ImportInfo(imported_name=local, module=alias.name, original_name=None, level=0)
                )

        def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
            for alias in node.names:
                local = alias.asname or alias.name
                imports.append(
                    ImportInfo(
                        imported_name=local,
                        module=node.module,
                        original_name=alias.name,
                        level=node.level,
                    )
                )

    _Visitor().visit(tree)
    return ModuleIndex(functions=tuple(functions), classes=tuple(classes), imports=tuple(imports))


def _end_line(node: ast.stmt) -> int:
    return node.end_lineno if node.end_lineno is not None else node.lineno


def find_function(index: ModuleIndex, name: str, *, line: int | None = None) -> FunctionInfo | None:
    """The function or method named `name`. When more than one matches
    (overloaded name across classes, or a module function shadowed by a
    method), the one whose range contains `line` wins; otherwise the first
    by source order, deterministically."""
    matches = [f for f in index.functions if f.name == name]
    if not matches:
        return None
    if line is not None:
        for match in matches:
            if match.start_line <= line <= match.end_line:
                return match
    return matches[0]


def find_class(index: ModuleIndex, name: str) -> ClassInfo | None:
    for cls in index.classes:
        if cls.name == name:
            return cls
    return None


def enclosing_function(index: ModuleIndex, line: int) -> FunctionInfo | None:
    """The innermost function whose range contains `line`. Top-level
    functions and methods are indexed identically (nested helpers are not
    indexed at all — see `build_index`), so "innermost" here only ever means
    "smallest matching range", which is what a method nested inside a class
    body naturally produces relative to any accidental module-level match."""
    candidates = [f for f in index.functions if f.start_line <= line <= f.end_line]
    if not candidates:
        return None
    return min(candidates, key=lambda f: f.end_line - f.start_line)


#: Names that appear as calls constantly and are almost never a repository
#: symbol worth a `search_symbol` round trip — stdlib/builtin methods and
#: dunder hooks. Missing one costs an extra (harmless, if wasted) search call
#: against the fixture transport; against a live GitHub, it is API budget
#: (`08` rate limiting, GC10), which is the reason this exists at all rather
#: than just letting every call resolve or fail on its own.
_CALL_NOISE = frozenset(
    {
        "__init__",
        "__str__",
        "__repr__",
        "__eq__",
        "__len__",
        "append",
        "extend",
        "get",
        "items",
        "keys",
        "values",
        "join",
        "format",
        "split",
        "strip",
        "startswith",
        "endswith",
        "isinstance",
        "getattr",
        "setattr",
        "hasattr",
        "len",
        "str",
        "int",
        "float",
        "bool",
        "dict",
        "list",
        "set",
        "tuple",
        "range",
        "sorted",
        "print",
        "info",
        "debug",
        "warning",
        "error",
        "exception",
    }
)


class _CallAndTypeVisitor(ast.NodeVisitor):
    """Collects call targets and annotation/instantiation type names from one
    function's body, in one walk."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.type_names: list[str] = []

    def visit_Call(self, node: ast.Call) -> None:
        name = _callee_name(node.func)
        if name and name not in _CALL_NOISE:
            self.calls.append(name)
            # PascalCase call names are, by the convention this fixture
            # repository and most Python code follows, a type instantiation
            # (`Decimal(...)`, `Cart(...)`) rather than a function call.
            # Heuristic, stated as one: Python's grammar does not distinguish
            # the two, and this is the same signal a human skimming the code
            # would use.
            if name[:1].isupper():
                self.type_names.append(name)
        self.generic_visit(node)


def _callee_name(func: ast.expr) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _names_in_expr(expr: ast.expr) -> list[str]:
    """Every `Name`/`Attribute` identifier inside an annotation expression —
    covers `Optional[TaxClient]`, `TaxClient | None`, `dict[str, Cart]`."""
    found: list[str] = []
    for node in ast.walk(expr):
        if isinstance(node, ast.Name):
            found.append(node.id)
        elif isinstance(node, ast.Attribute):
            found.append(node.attr)
    return found


def _signature_type_names(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    """Type names in `node`'s own signature — every argument annotation and
    the return annotation. Walked explicitly rather than by visiting `node`
    itself, because visiting `node` would also descend into its body and
    (for a nested helper) its own nested signature, double-counting."""
    names: list[str] = []
    args = node.args
    for arg in (*args.posonlyargs, *args.args, *args.kwonlyargs):
        if arg.annotation is not None:
            names.extend(_names_in_expr(arg.annotation))
    for optional_arg in (args.vararg, args.kwarg):
        if optional_arg is not None and optional_arg.annotation is not None:
            names.extend(_names_in_expr(optional_arg.annotation))
    if node.returns is not None:
        names.extend(_names_in_expr(node.returns))
    return names


def analyze_calls(node: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[list[str], list[str]]:
    """`(callees, type_names)` for one function: every call in its body (and,
    since a locally-defined helper is not indexed separately, in the body of
    any function nested inside it), plus every type named in its own
    signature or instantiated as a call. Deduplicated, order preserved —
    first mention wins, which keeps the graph's edge order matching the order
    a reader encounters names in the source.
    """
    visitor = _CallAndTypeVisitor()
    visitor.type_names.extend(_signature_type_names(node))
    for statement in node.body:
        visitor.visit(statement)

    def dedup(names: list[str]) -> list[str]:
        ordered: list[str] = []
        for name in names:
            if name not in ordered:
                ordered.append(name)
        return ordered

    return dedup(visitor.calls), dedup(visitor.type_names)
