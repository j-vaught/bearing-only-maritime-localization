"""Static and runtime checks for the program dependency boundaries."""

from __future__ import annotations

import ast
import dataclasses
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEARCH_ROOTS = (
    "bearing_localization.search",
    "bearing_localization.search_cli",
    "bearing_localization.algorithms",
    "bearing_localization.ransac",
    "bearing_localization.backends",
)
SIMULATOR_ROOTS = ("bearing_localization.simulator",)
EVALUATION_ROOTS = ("bearing_localization.evaluation", "bearing_localization.evaluate_cli")
SHARED_ROOTS = ("maritime_localization_shared",)


def _python_sources(root: Path = ROOT) -> dict[str, tuple[Path, ast.Module]]:
    sources: dict[str, tuple[Path, ast.Module]] = {}
    excluded_dirs = {
        ".agent-work",
        ".git",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "__pycache__",
        "build",
        "dist",
    }
    for directory, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(name for name in dirnames if name not in excluded_dirs)
        directory_path = Path(directory)
        for filename in sorted(filenames):
            if not filename.endswith(".py"):
                continue
            path = directory_path / filename
            relative = path.relative_to(root).with_suffix("")
            module = ".".join(relative.parts)
            if module.endswith(".__init__"):
                module = module[: -len(".__init__")]
            sources[module] = (path, ast.parse(path.read_text(encoding="utf-8")))
    return sources


def test_python_source_discovery_prunes_agent_workarea(tmp_path: Path) -> None:
    source = tmp_path / "maritime_localization_shared" / "example.py"
    source.parent.mkdir()
    source.write_text("value = 1\n", encoding="utf-8")
    generated = tmp_path / ".agent-work" / "cache" / "generated.py"
    generated.parent.mkdir(parents=True)
    generated.write_text("type Alias[T] = list[T]\n", encoding="utf-8")

    sources = _python_sources(tmp_path)

    assert set(sources) == {"maritime_localization_shared.example"}


def _program_for(module: str) -> str | None:
    for label, roots in (
        ("search", SEARCH_ROOTS),
        ("simulator", SIMULATOR_ROOTS),
        ("evaluation", EVALUATION_ROOTS),
        ("shared", SHARED_ROOTS),
    ):
        if any(module == root or module.startswith(root + ".") for root in roots):
            return label
    return None


def _resolved_imports(module: str, tree: ast.Module) -> list[tuple[str, ast.AST, tuple[str, ...]]]:
    """Return absolute module targets, including relative imports, with from-names."""
    found: list[tuple[str, ast.AST, tuple[str, ...]]] = []
    package = (
        module
        if module in {"bearing_localization", "maritime_localization_shared"}
        else module.rpartition(".")[0]
    )
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend((alias.name, node, ()) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package.split(".")
                remove = node.level - 1
                if remove:
                    base = base[:-remove]
                target = ".".join((*base, node.module or "")).rstrip(".")
            else:
                target = node.module or ""
            names = tuple(alias.name for alias in node.names)
            found.append((target, node, names))
    return found


def _is_under(target: str, roots: tuple[str, ...]) -> bool:
    return any(target == root or target.startswith(root + ".") for root in roots)


def _dynamic_import_calls(tree: ast.Module) -> list[ast.Call]:
    importlib_names = {"importlib"}
    import_module_names = {"import_module"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            importlib_names.update(
                alias.asname or alias.name.split(".")[0]
                for alias in node.names
                if alias.name == "importlib"
            )
        elif isinstance(node, ast.ImportFrom) and node.module == "importlib":
            import_module_names.update(
                alias.asname or alias.name for alias in node.names if alias.name == "import_module"
            )

    calls: list[ast.Call] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        function = node.func
        if isinstance(function, ast.Name) and function.id == "__import__":
            calls.append(node)
        elif isinstance(function, ast.Name) and function.id in import_module_names:
            calls.append(node)
        elif (
            isinstance(function, ast.Attribute)
            and function.attr == "import_module"
            and isinstance(function.value, ast.Name)
            and function.value.id in importlib_names
        ):
            calls.append(node)
    return calls


def test_program_packages_do_not_import_other_program_internals() -> None:
    sources = _python_sources()
    violations: list[str] = []
    for module, (_, tree) in sources.items():
        program = _program_for(module)
        for target, node, names in _resolved_imports(module, tree):
            forbidden = False
            if program == "search":
                forbidden = _is_under(target, (*SIMULATOR_ROOTS, *EVALUATION_ROOTS))
                forbidden |= target == "maritime_localization_shared.contracts.truth"
                forbidden |= "EvaluationTruth" in names or "TrueObject" in names
            elif program == "simulator":
                forbidden = _is_under(target, (*SEARCH_ROOTS, *EVALUATION_ROOTS))
            elif program == "evaluation":
                forbidden = _is_under(target, (*SIMULATOR_ROOTS, *SEARCH_ROOTS))
            elif program == "shared":
                forbidden = target == "bearing_localization" or target.startswith(
                    "bearing_localization."
                )
            if forbidden:
                if not isinstance(node, (ast.Import, ast.ImportFrom)):
                    raise TypeError("Import collector returned a non-import AST node.")
                violations.append(
                    f"{module}:{node.lineno} imports forbidden module or symbol {target} {names}"
                )

    assert not violations, "Architecture dependency boundary violations:\n" + "\n".join(violations)


def test_search_has_no_dynamic_import_escape_hatches() -> None:
    sources = _python_sources()
    violations: list[str] = []
    forbidden_roots = (*SIMULATOR_ROOTS, *EVALUATION_ROOTS)
    for module, (_, tree) in sources.items():
        if _program_for(module) != "search":
            continue
        for call in _dynamic_import_calls(tree):
            target = (
                call.args[0].value if call.args and isinstance(call.args[0], ast.Constant) else None
            )
            forbidden = not isinstance(target, str)
            if isinstance(target, str):
                forbidden = _is_under(target, forbidden_roots) or target == (
                    "maritime_localization_shared.contracts.truth"
                )
            if forbidden:
                violations.append(f"{module}:{call.lineno} has an unsafe dynamic import")
    assert not violations, "Dynamic imports bypass static dependency checks:\n" + "\n".join(
        violations
    )


def test_estimator_observation_contract_is_truth_free_allowlist() -> None:
    from maritime_localization_shared.contracts.observation import MeasurementSet, Observation

    assert {field.name for field in dataclasses.fields(Observation)} == {
        "bearing_deg",
        "bearing_sigma_deg",
        "object_type",
        "angular_half_width_deg",
    }
    assert {field.name for field in dataclasses.fields(MeasurementSet)} == {
        "observations",
        "trial_id",
        "timestamp_utc",
        "schema_version",
    }

    forbidden_terms = {"truth", "identity", "object_id", "distance", "range", "observer", "pose"}
    names = {
        field.name.lower()
        for contract in (Observation, MeasurementSet)
        for field in dataclasses.fields(contract)
    }
    assert not {
        field_name for field_name in names if any(term in field_name for term in forbidden_terms)
    }


def test_search_imports_under_runtime_boundary_guard() -> None:
    """Load the search package in isolation while rejecting forbidden imports."""
    guard_script = r"""
import builtins
import importlib
import inspect
import pkgutil
import sys

original_import = builtins.__import__
original_import_module = importlib.import_module
blocked = (
    "bearing_localization.simulator",
    "bearing_localization.evaluation",
    "bearing_localization.search",
)

def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
    caller = (globals or {}).get("__package__") or ""
    is_search = (
        caller == "bearing_localization.search"
        or caller.startswith("bearing_localization.search.")
        or caller == "bearing_localization.search_cli"
        or caller in {
            "bearing_localization.algorithms",
            "bearing_localization.ransac",
            "bearing_localization.backends",
        }
    )
    if is_search:
        if any(name == prefix or name.startswith(prefix + ".") for prefix in blocked[:2]):
            raise AssertionError(f"search attempted forbidden import: {name}")
        imports_truth = {"EvaluationTruth", "TrueObject"}.intersection(fromlist or ())
        if name == "maritime_localization_shared.contracts.truth" or imports_truth:
            raise AssertionError(f"search attempted truth import: {name} {fromlist}")
    return original_import(name, globals, locals, fromlist, level)

def guarded_import_module(name, package=None):
    caller = inspect.currentframe().f_back.f_globals.get("__package__") or ""
    is_search = (
        caller == "bearing_localization.search"
        or caller.startswith("bearing_localization.search.")
        or caller == "bearing_localization.search_cli"
        or caller in {
            "bearing_localization.algorithms",
            "bearing_localization.ransac",
            "bearing_localization.backends",
        }
    )
    if is_search and (
        any(name == prefix or name.startswith(prefix + ".") for prefix in blocked[:2])
        or name == "maritime_localization_shared.contracts.truth"
    ):
        raise AssertionError(f"search dynamically imported forbidden module: {name}")
    return original_import_module(name, package)

builtins.__import__ = guarded_import
importlib.import_module = guarded_import_module
import bearing_localization.search as package
for entry in pkgutil.walk_packages(package.__path__, package.__name__ + "."):
    importlib.import_module(entry.name)
importlib.import_module("bearing_localization.search_cli")
legacy_search_modules = (
    "bearing_localization.algorithms",
    "bearing_localization.ransac",
    "bearing_localization.backends",
)
for name in legacy_search_modules:
    importlib.import_module(name)
"""
    result = subprocess.run(
        [sys.executable, "-c", guard_script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
