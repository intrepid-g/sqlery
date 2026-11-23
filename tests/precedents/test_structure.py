"""Structural precedents: every party binds, every clause is cited and in force."""

import ast
import importlib
import inspect
from pathlib import Path

import sqlery.contract as contract_pkg

ROOT = Path(__file__).resolve().parents[2]
CONTRACT_DIR = Path(contract_pkg.__file__).parent
SRC = ROOT / "src" / "sqlery"
PRECEDENTS = Path(__file__).parent
PARTIES = sorted(p.stem for p in CONTRACT_DIR.glob("*.py") if p.stem != "__init__")


def clauses(party: str) -> list[str]:
    module = importlib.import_module(f"sqlery.contract.{party}")
    return [
        name
        for name, fn in inspect.getmembers(module, inspect.isfunction)
        if fn.__module__ == module.__name__ and not name.startswith("_")
    ]


def called_names(files) -> set[str]:
    names: set[str] = set()
    for f in files:
        for node in ast.walk(ast.parse(f.read_text())):
            if isinstance(node, ast.Call):
                fn = node.func
                names.add(fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", ""))
    return names


def test_the_contract_has_the_seven_parties():
    assert PARTIES == [
        "config",
        "database",
        "dead_worker_reaper",
        "lease_holder",
        "queue_lock",
        "worker",
        "zombie_sweep",
    ]


def test_every_party_module_binds_with_at_least_one_clause():
    assert [p for p in PARTIES if not clauses(p)] == []


def test_every_party_has_a_precedent_file():
    assert [p for p in PARTIES if not (PRECEDENTS / f"test_{p}.py").exists()] == []


def test_every_clause_is_called_by_a_precedent():
    cited = called_names(p for p in PRECEDENTS.glob("test_*.py") if p.name != "test_structure.py")
    uncited = [f"{p}.{c}" for p in PARTIES for c in clauses(p) if c not in cited]
    assert uncited == []


def clause_calls() -> dict[str, set[str]]:
    """Each clause name mapped to the names it calls."""
    calls: dict[str, set[str]] = {}
    for party in PARTIES:
        module = importlib.import_module(f"sqlery.contract.{party}")
        for name in clauses(party):
            source = inspect.getsource(getattr(module, name))
            calls[name] = {
                n.func.id if isinstance(n.func, ast.Name) else getattr(n.func, "attr", "")
                for n in ast.walk(ast.parse(source))
                if isinstance(n, ast.Call)
            }
    return calls


def in_force(roots: set[str], calls: dict[str, set[str]]) -> set[str]:
    """Clauses reachable from operative calls, directly or through other clauses."""
    reached, todo = set(), [c for c in calls if c in roots]
    while todo:
        name = todo.pop()
        if name not in reached:
            reached.add(name)
            todo.extend(c for c in calls.get(name, ()) if c in calls)
    return reached


def test_every_clause_is_in_force_from_operative_source():
    operative = called_names(f for f in SRC.rglob("*.py") if CONTRACT_DIR not in f.parents)
    calls = clause_calls()
    reached = in_force(operative, calls)
    assert sorted(set(calls) - reached) == []


def test_a_clause_only_another_orphan_calls_is_not_in_force():
    calls = {"verdict": {"sub"}, "sub": set(), "orphan": {"helper"}, "helper": set()}
    assert in_force({"verdict"}, calls) == {"verdict", "sub"}


def test_clauses_do_no_io():
    banned = {"open", "print", "kill", "connect", "execute", "now", "utcnow", "sleep", "getenv"}
    offenders = []
    for party in PARTIES:
        for node in ast.walk(ast.parse((CONTRACT_DIR / f"{party}.py").read_text())):
            if isinstance(node, ast.Call):
                fn = node.func
                name = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", "")
                if name in banned:
                    offenders.append(f"{party}:{name}")
    assert offenders == []
