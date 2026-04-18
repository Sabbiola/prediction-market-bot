"""Executable architectural boundary tests (REC-08).

These tests enforce the hexagonal-architecture layering rules expressed in
``config/agents.yaml`` ("forbidden" fields) and the architecture document.
They scan every Python source file in each layer and assert that no module
imports from a layer it is forbidden to depend on.

Layer hierarchy (outermost depends on innermost, never the reverse):
  ui
  ├─ orchestration / orchestrator
  ├─ services
  ├─ agents          ← business logic
  ├─ infrastructure  ← external adapters (DB, HTTP, …)
  └─ domain          ← pure value objects & contracts

Enforced rules:
  * ``domain``         must NOT import from agents | infrastructure | ui | services
  * ``agents``         must NOT import from infrastructure | ui
  * ``infrastructure`` must NOT import from agents | ui
  * ``ui``             must NOT import from agents directly
                       (must go through interfaces / orchestration ports)

Rationale: keeping these boundaries prevents coupling between layers,
enables testing each layer in isolation, and makes the dependency flow
explicit and verifiable in CI.
"""

from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path
from typing import NamedTuple

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SRC_ROOT = Path(__file__).parent.parent.parent / "src" / "prediction_market_bot"


def _all_python_files(package_dir: Path) -> list[Path]:
    return sorted(package_dir.rglob("*.py"))


def _imported_top_packages(source: str) -> set[str]:
    """Return the set of top-level ``prediction_market_bot.<subpackage>`` imports."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name.split(".")[0] + "." + (alias.name.split(".")[1] if len(alias.name.split(".")) > 1 else ""))
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.module.startswith("prediction_market_bot."):
                parts = node.module.split(".")
                if len(parts) >= 2:
                    imports.add(f"prediction_market_bot.{parts[1]}")
    return imports


def _violations(
    layer_dir: Path,
    *,
    forbidden_subpackages: tuple[str, ...],
) -> list[str]:
    """Return list of violation strings (``file: imported_pkg``)."""
    found: list[str] = []
    for py_file in _all_python_files(layer_dir):
        try:
            source = py_file.read_text(encoding="utf-8")
        except OSError:
            continue
        imported = _imported_top_packages(source)
        for pkg in forbidden_subpackages:
            full = f"prediction_market_bot.{pkg}"
            if full in imported:
                rel = py_file.relative_to(_SRC_ROOT.parent)
                found.append(f"{rel}: imports {full}")
    return found


# ---------------------------------------------------------------------------
# Boundary tests
# ---------------------------------------------------------------------------


def test_domain_does_not_import_agents() -> None:
    domain_dir = _SRC_ROOT / "domain"
    viols = _violations(domain_dir, forbidden_subpackages=("agents",))
    assert not viols, (
        "domain layer must not import from agents:\n" + "\n".join(viols)
    )


def test_domain_does_not_import_infrastructure() -> None:
    domain_dir = _SRC_ROOT / "domain"
    viols = _violations(domain_dir, forbidden_subpackages=("infrastructure",))
    assert not viols, (
        "domain layer must not import from infrastructure:\n" + "\n".join(viols)
    )


def test_domain_does_not_import_ui() -> None:
    domain_dir = _SRC_ROOT / "domain"
    viols = _violations(domain_dir, forbidden_subpackages=("ui",))
    assert not viols, (
        "domain layer must not import from ui:\n" + "\n".join(viols)
    )


def test_domain_does_not_import_services() -> None:
    domain_dir = _SRC_ROOT / "domain"
    viols = _violations(domain_dir, forbidden_subpackages=("services",))
    assert not viols, (
        "domain layer must not import from services:\n" + "\n".join(viols)
    )


def test_agents_do_not_import_infrastructure() -> None:
    agents_dir = _SRC_ROOT / "agents"
    viols = _violations(agents_dir, forbidden_subpackages=("infrastructure",))
    assert not viols, (
        "agents layer must not import from infrastructure directly "
        "(use interfaces/ports instead):\n" + "\n".join(viols)
    )


def test_agents_do_not_import_ui() -> None:
    agents_dir = _SRC_ROOT / "agents"
    viols = _violations(agents_dir, forbidden_subpackages=("ui",))
    assert not viols, (
        "agents layer must not import from ui:\n" + "\n".join(viols)
    )


def test_infrastructure_does_not_import_agents() -> None:
    infra_dir = _SRC_ROOT / "infrastructure"
    viols = _violations(infra_dir, forbidden_subpackages=("agents",))
    assert not viols, (
        "infrastructure layer must not import from agents:\n" + "\n".join(viols)
    )


def test_infrastructure_does_not_import_ui() -> None:
    infra_dir = _SRC_ROOT / "infrastructure"
    viols = _violations(infra_dir, forbidden_subpackages=("ui",))
    assert not viols, (
        "infrastructure layer must not import from ui:\n" + "\n".join(viols)
    )


def test_ui_does_not_import_agents_directly() -> None:
    """The UI layer must not import from ``agents`` directly.

    UI code must route through ``orchestration`` / ``services`` / ``interfaces``
    so that agents remain independently testable and the dependency flow stays
    clean.  This is the most critical boundary because it prevents UI logic from
    accumulating business-logic coupling.
    """
    ui_dir = _SRC_ROOT / "ui"
    viols = _violations(ui_dir, forbidden_subpackages=("agents",))
    assert not viols, (
        "ui layer must not import from agents directly — use orchestration or "
        "interfaces instead:\n" + "\n".join(viols)
    )
