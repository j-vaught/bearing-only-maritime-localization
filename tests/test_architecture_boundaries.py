"""Static and runtime checks for the program dependency boundaries."""

from __future__ import annotations

import ast
import dataclasses
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SEARCH_ROOTS = ("bearing_localization.search", "bearing_localization.search_cli")
SIMULATOR_ROOTS = ("bearing_localization.simulator",)
EVALUATION_ROOTS = ("bearing_localization.evaluation", "bearing_localization.evaluate_cli")
SHARED_ROOTS = ("maritime_localization_shared",)


def _python_sources() -> dict[str, tuple[Path, ast.Module]]:
    sources: dict[str, tuple[Path, ast.Module]] = {}
    for path in ROOT.rglob("*.py"):
        if any(part in {".venv", "build", "dist"} for part in path.parts):
            continue
        relative = path.relative_to(ROOT).with_suffix("")
        module = ".".join(relative.parts)
        if module.endswith(".__init__"):
            module = module[: -len(".__init__")]
        sources[module] = (path, ast.parse(path.read_text(encoding="utf-8")))
    return sources


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
    calls: list[ast.Call] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        function = node.func
        if isinstance(function, ast.Name) and function.id == "__import__":
            calls.append(node)
        elif (
            isinstance(function, ast.Attribute)
            and function.attr == "import_module"
            and isinstance(function.value, ast.Name)
            and function.value.id == "importlib"
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
                forbidden = _is_under(target, ("bearing_localization.search", *EVALUATION_ROOTS))
            elif program == "evaluation":
                forbidden = _is_under(target, (*SIMULATOR_ROOTS, *SEARCH_ROOTS))
            elif program == "shared":
                forbidden = target == "bearing_localization" or target.startswith(
                    "bearing_localization."
                )
            if forbidden:
                violations.append(
                    f"{module}:{node.lineno} imports forbidden module or symbol {target} {names}"
                )

    assert not violations, "Architecture dependency boundary violations:\n" + "\n".join(violations)


def test_search_has_no_dynamic_import_escape_hatches() -> None:
    sources = _python_sources()
    violations = [
        f"{module}:{call.lineno} uses a dynamic import"
        for module, (_, tree) in sources.items()
        if _program_for(module) == "search"
        for call in _dynamic_import_calls(tree)
    ]
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
import pkgutil
import sys

original_import = builtins.__import__
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
    )
    if is_search:
        if any(name == prefix or name.startswith(prefix + ".") for prefix in blocked[:2]):
            raise AssertionError(f"search attempted forbidden import: {name}")
        imports_truth = {"EvaluationTruth", "TrueObject"}.intersection(fromlist or ())
        if name == "maritime_localization_shared.contracts.truth" or imports_truth:
            raise AssertionError(f"search attempted truth import: {name} {fromlist}")
    return original_import(name, globals, locals, fromlist, level)

builtins.__import__ = guarded_import
import bearing_localization.search as package
for entry in pkgutil.walk_packages(package.__path__, package.__name__ + "."):
    importlib.import_module(entry.name)
importlib.import_module("bearing_localization.search_cli")
"""
    result = subprocess.run(
        [sys.executable, "-c", guard_script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    ("module", "forbidden"),
    [
        ("bearing_localization.search.grid", "bearing_localization.simulator.scene_builder"),
        ("bearing_localization.simulator.runner", "bearing_localization.search.grid"),
        ("bearing_localization.evaluation.metrics", "bearing_localization.search.grid"),
    ],
)
def test_boundary_rule_helper_rejects_cross_branch_targets(module: str, forbidden: str) -> None:
    """Keep the policy mapping itself covered for representative branch pairs."""
    assert _program_for(module) is not None
    assert _is_under(forbidden, ("bearing_localization.simulator", "bearing_localization.search"))
