from __future__ import annotations

import ast
import json
import sqlite3
import subprocess
import sys
from importlib import import_module
from importlib.util import find_spec
from pathlib import Path

from protocol_optimization_agent.runtime.context import RunContext, content_fingerprint
from protocol_optimization_agent.runtime.paths import PACKAGE_ROOT, PROJECT_ROOT, optimization_code_paths
from protocol_optimization_agent.science.models import ProtocolState
from protocol_optimization_agent.science.state import ScientificWorldModel


def test_only_canonical_modules_are_importable() -> None:
    mapping = json.loads((PROJECT_ROOT / "evals/architecture_baseline/module_relocations.json").read_text(encoding="utf-8"))
    for old, new in mapping.items():
        if new in {"agents.protocol_chat", "interfaces.langgraph_server"}:
            assert find_spec(f"protocol_optimization_agent.{new}") is None
            assert find_spec(f"protocol_optimization_agent.{old}") is None
            continue
        assert import_module(f"protocol_optimization_agent.{new}")
        if old == "runtime":
            assert not hasattr(import_module("protocol_optimization_agent.runtime"), "RunContext")
        else:
            assert not (PACKAGE_ROOT / (old.replace(".", "/") + ".py")).exists()
            assert find_spec(f"protocol_optimization_agent.{old}") is None
    for name in ("orchestrator", "scientific_reasoning", "tools", "visualization"):
        assert not (PACKAGE_ROOT / (name + ".py")).exists()
        assert not (PACKAGE_ROOT / name / "__init__.py").exists()


def test_internal_imports_do_not_use_removed_interfaces() -> None:
    mapping = json.loads((PROJECT_ROOT / "evals/architecture_baseline/module_relocations.json").read_text(encoding="utf-8"))
    removed = {"protocol_optimization_agent." + name for name in mapping if name != "runtime"}
    removed.update({"protocol_optimization_agent.orchestrator", "protocol_optimization_agent.scientific_reasoning"})
    for path in PACKAGE_ROOT.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom):
                assert node.module not in removed, path


def test_analysis_has_no_agent_interface_or_database_dependencies() -> None:
    forbidden = (
        "protocol_optimization_agent.agents",
        "protocol_optimization_agent.interfaces",
        "protocol_optimization_agent.science.state",
        "protocol_optimization_agent.extensions",
    )
    for path in (PACKAGE_ROOT / "analysis").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            imported = (
                [node.module or ""]
                if isinstance(node, ast.ImportFrom)
                else ([alias.name for alias in node.names] if isinstance(node, ast.Import) else [])
            )
            assert not any(name.startswith(forbidden) for name in imported), path


def test_core_cold_import_does_not_load_feedback_extension() -> None:
    code = """
import sys
from protocol_optimization_agent.agents.protocol_analysis import ProtocolAnalysisAgent
from protocol_optimization_agent.agents.data_analysis import DataAnalysisAgent
from protocol_optimization_agent.agents.protocol_optimizing import ProtocolOptimizingAgent
from protocol_optimization_agent.science.state import ScientificWorldModel
from protocol_optimization_agent.agents.orchestrator import build_orchestrator_graph
from protocol_optimization_agent.interfaces.api import create_app
create_app(enable_experimental_feedback=False)
assert not any(name.startswith('protocol_optimization_agent.extensions') for name in sys.modules)
"""
    subprocess.run([sys.executable, "-c", code], cwd=PROJECT_ROOT, check=True, capture_output=True, text=True)


def test_orchestrator_contains_no_domain_tools_or_calculations() -> None:
    source = (PACKAGE_ROOT / "agents/orchestrator.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert not (node.module or "").startswith("protocol_optimization_agent.analysis"), node.module
            assert node.module not in {
                "protocol_optimization_agent.agents.protocol_analysis",
                "protocol_optimization_agent.agents.data_analysis",
                "protocol_optimization_agent.agents.protocol_optimizing",
            }
    functions = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}
    assert not {"protocol_analysis_agent", "data_analysis_agent", "protocol_optimizing_agent",
                "_filter_data_result_rows", "_optimization_cache_key"}.intersection(functions)
    for filename in ("langgraph.json", "langgraph.authenticated.json"):
        config = json.loads((PROJECT_ROOT / filename).read_text(encoding="utf-8"))
        assert set(config["graphs"]) == {"orchestrator"}


def test_experimental_api_is_disabled_by_default_and_explicitly_enabled(monkeypatch) -> None:
    from protocol_optimization_agent.interfaces.api import create_app
    monkeypatch.delenv("PROTOCOL_EXPERIMENTAL_FEEDBACK_ENABLED", raising=False)
    default_paths = {route.path for route in create_app().routes}
    assert "/protocol-query/chat" not in default_paths
    assert not any(path.startswith("/scientific/experiments/") for path in default_paths)
    enabled_paths = {route.path for route in create_app(enable_experimental_feedback=True).routes}
    assert "/scientific/experiments/review" in enabled_paths
    monkeypatch.setenv("PROTOCOL_EXPERIMENTAL_FEEDBACK_ENABLED", "1")
    assert {route.path for route in create_app().routes} == enabled_paths
    assert {route.path for route in create_app(enable_experimental_feedback=False).routes} == default_paths


def test_root_and_cache_paths_point_to_actual_implementations(tmp_path: Path) -> None:
    assert PROJECT_ROOT == Path(__file__).resolve().parents[2]
    assert PACKAGE_ROOT == PROJECT_ROOT / "backend/protocol_optimization_agent"
    paths = optimization_code_paths()
    assert paths and all(Path(path).is_file() for path in paths.values())
    assert "code:analysis/optimization/association.py" in paths
    assert "code:analysis/optimization/assessment.py" in paths
    assert "code:science/identity.py" in paths
    assert all("sys.modules[__name__]" not in Path(path).read_text(encoding="utf-8") for path in paths.values())
    source = tmp_path / "calculation.py"
    source.write_text("def score(): return 1\n", encoding="utf-8")
    before = content_fingerprint({"calculation": str(source)})
    source.write_text("def score(): return 2\n", encoding="utf-8")
    assert content_fingerprint({"calculation": str(source)}) != before


def test_pre_migration_sqlite_record_is_read_without_rewriting(tmp_path: Path) -> None:
    context = RunContext.from_values(user_id="legacy", run_id="read_old", workspace_root=tmp_path)
    path = context.user_root() / "research/default/scientific_state.sqlite3"
    path.parent.mkdir(parents=True)
    payload = ProtocolState(
        id="legacy_protocol_id", version=3, protocol_id="legacy_protocol", features={"dose": 0.1}
    ).model_dump()
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE records (kind TEXT, id TEXT, version INTEGER, payload TEXT, PRIMARY KEY(kind,id))")
        db.execute("INSERT INTO records VALUES (?, ?, ?, ?)", ("protocol", payload["id"], 3, json.dumps(payload)))
    before = path.read_bytes()
    store = ScientificWorldModel(context)
    assert store.path == path.resolve()
    assert store.get("protocol", "legacy_protocol_id") == payload
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT version,payload FROM records").fetchone() == (3, json.dumps(payload))
    # Opening the legacy database may add its missing history table, not rewrite records.
    assert before and store.history("protocol", "legacy_protocol_id") == []
