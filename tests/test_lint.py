"""Every name a stage uses is defined.

A stage that calls a helper it never imported crashes only on the branch
that reaches it, so an end-to-end run finds it long after the tests passed.
This walks every module's AST with Python's scoping rules and reports any
name loaded that no enclosing scope, the module or the builtins define.
"""

from __future__ import annotations

import ast
import builtins
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODULES = sorted((ROOT / "ytstudio").glob("*.py")) + [ROOT / "claude_engine.py",
                                                    ROOT / "codex_engine.py"]


def _targets(node: ast.AST) -> set[str]:
    """Names bound by an assignment target, a for target, a with item..."""
    out: set[str] = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Store):
            out.add(sub.id)
    return out


_NESTED = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef,
           ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)


def _walk_scope(root: ast.AST):
    """Every node of one scope: a nested scope is yielded (its def binds a
    name here) but not entered (what it binds is its own)."""
    stack = [root]
    while stack:
        node = stack.pop()
        yield node
        if node is root or not isinstance(node, _NESTED):
            stack.extend(ast.iter_child_nodes(node))


def _bound_in(body: list[ast.stmt]) -> set[str]:
    """Names a scope binds through its own statements (not nested scopes)."""
    names: set[str] = set()
    for stmt in body:
        for node in _walk_scope(stmt):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(node.name)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    names.add((alias.asname or alias.name).split(".")[0])
            elif isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign, ast.For,
                                   ast.AsyncFor, ast.With, ast.AsyncWith, ast.NamedExpr)):
                for field in ("targets", "target", "items"):
                    value = getattr(node, field, None)
                    if value is None:
                        continue
                    for t in (value if isinstance(value, list) else [value]):
                        names |= _targets(getattr(t, "optional_vars", None) or t)
            elif isinstance(node, ast.ExceptHandler) and node.name:
                names.add(node.name)
            elif isinstance(node, (ast.Global, ast.Nonlocal)):
                names.update(node.names)
            elif isinstance(node, ast.MatchAs) and node.name:
                names.add(node.name)
            elif isinstance(node, (ast.comprehension,)):
                names |= _targets(node.target)
    return names


def _first_binding_lines(body: list[ast.stmt]) -> dict[str, int]:
    """For each name a scope binds through assignment, the first line that
    binds it (imports and defs count too), ignoring nested scopes."""
    first: dict[str, int] = {}

    def note(name: str, line: int) -> None:
        if name not in first or line < first[name]:
            first[name] = line

    for stmt in body:
        for node in _walk_scope(stmt):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                note(node.name, node.lineno)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    note((alias.asname or alias.name).split(".")[0], node.lineno)
            elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                note(node.id, node.lineno)
            elif isinstance(node, ast.ExceptHandler) and node.name:
                note(node.name, node.lineno)
    return first


def undefined_names(tree: ast.Module) -> list[tuple[int, str]]:
    """Loads of a name nothing defines -- and loads of a local that shadows
    an outer name before the function has bound it, which Python raises as
    UnboundLocalError at the call, not at import."""
    found: list[tuple[int, str]] = []
    builtin_names = set(dir(builtins)) | {"__file__", "__name__", "__doc__", "__spec__"}

    def visit(node: ast.AST, scopes: list[set[str]],
              shadow: dict[str, int] | None = None) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            args = node.args
            local = {a.arg for a in args.args + args.posonlyargs + args.kwonlyargs}
            if args.vararg:
                local.add(args.vararg.arg)
            if args.kwarg:
                local.add(args.kwarg.arg)
            body = node.body if isinstance(node.body, list) else [ast.Expr(node.body)]
            params = set(local)
            local |= _bound_in(body)
            # A local that an enclosing scope also defines is a shadow: using
            # it before the first local binding is the UnboundLocalError.
            # A parameter is bound on entry, so it never is.
            bindings = _first_binding_lines(body)
            local_shadow = {name: line for name, line in bindings.items()
                            if any(name in s for s in scopes) and name not in params}
            declared = {n for stmt in body for node_ in ast.walk(stmt)
                        if isinstance(node_, (ast.Global, ast.Nonlocal)) for n in node_.names}
            local_shadow = {n: l for n, l in local_shadow.items() if n not in declared}
            # defaults and decorators are evaluated in the enclosing scope
            for d in list(args.defaults) + list(args.kw_defaults) + list(getattr(node, "decorator_list", [])):
                if d is not None:
                    visit(d, scopes)
            for a in args.args + args.posonlyargs + args.kwonlyargs:
                if a.annotation is not None:
                    pass  # annotations are strings under `from __future__ import annotations`
            inner = scopes + [local]
            for stmt in body:
                visit(stmt, inner, local_shadow)
            return
        if isinstance(node, ast.ClassDef):
            for d in node.decorator_list + node.bases:
                visit(d, scopes, shadow)
            # class bodies do not form a scope for methods: pass the outer chain
            local = _bound_in(node.body)
            for stmt in node.body:
                visit(stmt, scopes + [local])
            return
        if isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
            local: set[str] = set()
            for gen in node.generators:
                local |= _targets(gen.target)
            inner = scopes + [local]
            # what the comprehension binds is bound for its whole body
            inner_shadow = {k: v for k, v in (shadow or {}).items() if k not in local}
            for gen in node.generators:
                visit(gen.iter, inner, inner_shadow)
                for cond in gen.ifs:
                    visit(cond, inner, inner_shadow)
            for field in ("elt", "key", "value"):
                value = getattr(node, field, None)
                if value is not None:
                    visit(value, inner, inner_shadow)
            return
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            if not any(node.id in s for s in scopes) and node.id not in builtin_names:
                found.append((node.lineno, node.id))
            elif shadow and node.id in shadow and node.lineno < shadow[node.id]:
                found.append((node.lineno, f"{node.id} (local shadows an outer name, "
                                           f"bound at line {shadow[node.id]})"))
            return
        for child in ast.iter_child_nodes(node):
            visit(child, scopes, shadow)

    module_scope = _bound_in(tree.body)
    for stmt in tree.body:
        visit(stmt, [module_scope])
    return found


class Checker(unittest.TestCase):
    """The checker itself, on small sources."""

    def check(self, src: str) -> list[str]:
        return [name for _, name in undefined_names(ast.parse(src))]

    def test_an_unimported_helper_is_reported(self):
        self.assertEqual(self.check("def f():\n    return helper(1)\n"), ["helper"])

    def test_a_local_that_shadows_an_import_before_binding_is_reported(self):
        src = ("from m import brief\n"
               "def run():\n"
               "    x = brief('a')\n"
               "    n, brief = 1, 2\n"
               "    return x, brief\n")
        self.assertEqual([n.split(" ")[0] for n in self.check(src)], ["brief"])

    def test_a_local_bound_before_use_is_fine(self):
        src = ("from m import brief\n"
               "def run():\n"
               "    brief = 2\n"
               "    return brief\n")
        self.assertEqual(self.check(src), [])

    def test_a_parameter_named_like_an_outer_name_is_fine(self):
        src = ("from m import brief\n"
               "def run(brief):\n"
               "    return brief\n")
        self.assertEqual(self.check(src), [])

    def test_a_global_declaration_is_fine(self):
        src = ("count = 0\n"
               "def bump():\n"
               "    global count\n"
               "    count = count + 1\n")
        self.assertEqual(self.check(src), [])

    def test_a_loop_variable_used_only_inside_its_loop_is_fine(self):
        src = ("import os\n"
               "def f(rows):\n"
               "    out = []\n"
               "    for os in rows:\n"
               "        out.append(os)\n"
               "    return out\n")
        self.assertEqual(self.check(src), [])


class Lint(unittest.TestCase):
    def test_every_name_used_is_defined(self):
        problems = []
        for path in MODULES:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for line, name in undefined_names(tree):
                problems.append(f"{path.relative_to(ROOT)}:{line}: {name}")
        self.assertEqual(problems, [], "\n" + "\n".join(problems))


if __name__ == "__main__":
    unittest.main()
