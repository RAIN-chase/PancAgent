from __future__ import annotations

import json
from importlib import import_module
from importlib.metadata import distribution
from pathlib import Path

from protocol_optimization_agent.interfaces.api import create_app


def test_public_execution_entrypoints_remain_stable() -> None:
    root = Path(__file__).resolve().parents[2]
    expected = json.loads((root / "evals/architecture_baseline/entrypoints.json").read_text(encoding="utf-8"))
    migrated = json.loads((root / "evals/architecture_baseline/migrated_entrypoints.json").read_text(encoding="utf-8"))
    assert set(migrated["graphs"]) == set(expected["graphs"])
    assert set(migrated["cli"]) == set(expected["cli"])
    for filename in ("langgraph.json", "langgraph.authenticated.json"):
        config = json.loads((root / filename).read_text(encoding="utf-8"))
        assert config["graphs"] == migrated["graphs"]
        for target in config["graphs"].values():
            path, symbol = target.split(":")
            assert (root / path).is_file() and symbol == "graph"
        if "auth" in config:
            assert config["auth"]["path"] == migrated["auth"]
    registered = {entry.name: entry.value for entry in distribution("pancreas-protocol-optimization-agent").entry_points
                  if entry.group == "console_scripts"}
    assert registered == migrated["cli"]
    for target in registered.values():
        module, function = target.split(":")
        assert callable(getattr(import_module(module), function))
    routes = {route.path: sorted(route.methods) for route in create_app().routes
              if route.path.startswith(("/protocol-", "/scientific/")) or route.path == "/health"}
    assert routes == expected["api"]
    variables = [line.split("=", 1)[0] for line in (root / ".env.example").read_text(encoding="utf-8").splitlines()
                 if line and not line.startswith("#") and "=" in line]
    assert variables == expected["environment_variables"]
