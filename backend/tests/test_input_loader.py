from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest
from protocol_optimization_agent.agents.protocol_analysis import ProtocolAnalysisAgent
from protocol_optimization_agent.analysis.protocol.input_loader import load_protocol_analysis_inputs
from protocol_optimization_agent.analysis.protocol.query_engine import ProtocolQueryEngine
from protocol_optimization_agent.runtime.config import ProtocolOptimizationConfig
from protocol_optimization_agent.runtime.io import read_csv_dicts


def _protocol(pid: str = "p1") -> dict:
    return {"protocol_id": pid, "paper_id": "paper1", "protocol_name": "Formal protocol",
            "comparison_ready": True, "confidence_level": "high", "source_file": "paper1.pdf",
            "paper": {"dataset_id": "GSE1"},
            "preparation": [{"operation": "Expand cells", "evidence_ids": ["prep_evidence"]}],
            "stages": [{"stage_id": "s1", "protocol_id": pid, "stage_index": 1, "stage_name": "Induction",
                        "day_start": 1, "day_end": 1, "duration_days": 1, "timing_convention": "inclusive_reported_days",
                        "medium_base": "RPMI", "key_operations": "Change medium", "evidence_quote": "Stage evidence",
                        "confidence_level": "high", "reagents": [
                            {"original_name": "Activin A", "standardized_name": "Activin A", "concentration": "100",
                             "unit": "ng/ml", "confidence_level": "high", "evidence_quote": "100 ng/ml Activin A",
                             "evidence_ids": ["e1"], "field_evidence_ids": {"concentration": ["e1"]}},
                            {"original_name": "ITS", "concentration": "1:5,000", "unit": None,
                             "evidence_quote": "1:5,000 ITS"},
                            {"original_name": "WNT3a", "concentration": None, "unit": None,
                             "evidence_quote": "50 ng/ml WNT3a"}]}]}


def _write(input_dir: Path, records: list) -> None:
    (input_dir / "protocols.jsonl").write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")


def test_only_formal_jsonl_supplies_records_events_and_source_identity(tmp_path: Path) -> None:
    record = _protocol()
    record["comparison_ready"] = False
    _write(tmp_path, [record])
    for legacy in ("protocol_artifacts.jsonl", "protocol_database.sqlite", "protocol_factor_events.csv",
                   "source_identity_inventory.csv"):
        (tmp_path / legacy).write_text("poisoned legacy file", encoding="utf-8")
    loaded = load_protocol_analysis_inputs(tmp_path)
    protocol = loaded["protocol_records"][0]
    assert loaded["protocol_record_source"] == "protocols.jsonl"
    assert protocol["protocol"]["protocol_name"] == "Formal protocol"
    assert protocol["comparison_ready"] is False
    assert protocol["preparation"] == record["preparation"]
    assert loaded["source_identity_rows"][0]["dataset_id"] == "GSE1"
    events = {e["factor_name"]: e for e in loaded["factor_events"] if e["factor_type"] == "reagent"}
    assert events["Activin A"]["concentration_value"] == 100
    assert events["Activin A"]["concentration_unit"] == "ng/ml"
    assert events["Activin A"]["day_start"] is None
    assert events["Activin A"]["source_record_path"] == "/stages/0/reagents/0"
    assert events["Activin A"]["field_evidence_ids"] == {"concentration": ["e1"]}
    assert events["ITS"]["concentration"] == "1:5,000" and events["ITS"]["concentration_value"] is None
    assert events["WNT3a"]["concentration_value"] is None  # A quote must not replace a missing structured dose.
    assert all(e["input_source"] == "protocols.jsonl" for e in loaded["factor_events"])
    assert any(e["stage_role"] == "preparation" for e in loaded["factor_events"])


def test_missing_formal_file_never_falls_back(tmp_path: Path) -> None:
    (tmp_path / "protocol_artifacts.jsonl").write_text(json.dumps(_protocol()), encoding="utf-8")
    (tmp_path / "protocol_database.sqlite").write_bytes(b"")
    with pytest.raises(FileNotFoundError, match="protocols.jsonl"):
        load_protocol_analysis_inputs(tmp_path)


@pytest.mark.parametrize("records", [[], [[]], [{}], [_protocol(), _protocol()],
                                     [{**_protocol(), "comparison_ready": "false"}],
                                     [{**_protocol(), "stages": "not a list"}]])
def test_invalid_formal_contract_is_rejected(tmp_path: Path, records: list) -> None:
    _write(tmp_path, records)
    with pytest.raises(ValueError):
        load_protocol_analysis_inputs(tmp_path)


def test_nested_reagent_owner_must_match_parent(tmp_path: Path) -> None:
    record = _protocol()
    record["stages"][0]["reagents"][0]["protocol_id"] = "foreign"
    _write(tmp_path, [record])
    with pytest.raises(ValueError, match="does not match"):
        load_protocol_analysis_inputs(tmp_path)


def test_missing_readiness_and_confidence_are_not_upgraded(tmp_path: Path) -> None:
    record = _protocol()
    del record["comparison_ready"]
    del record["confidence_level"]
    _write(tmp_path, [record])
    loaded = load_protocol_analysis_inputs(tmp_path)
    protocol = loaded["protocol_records"][0]
    assert protocol["comparison_ready"] is False
    protocol["comparison_ready"] = True
    agent = ProtocolAnalysisAgent(tmp_path / "out", {"min_protocol_confidence": 0.65})
    assert not agent._select_protocols([protocol])["protocols"][0]["passed_quality_filter"]


def test_formal_file_alone_runs_pa_and_protocol_comparison(tmp_path: Path) -> None:
    left, right = _protocol(), deepcopy(_protocol("p2"))
    right["stages"][0]["reagents"][0]["concentration"] = "0"
    _write(tmp_path, [left, right])
    loaded = load_protocol_analysis_inputs(tmp_path)
    outputs = ProtocolAnalysisAgent(tmp_path / "out", {"min_protocol_confidence": 0.65}).run(
        loaded["protocol_records"], loaded["factor_events"], loaded["source_identity_rows"])
    assert {r["protocol_id"] for r in read_csv_dicts(outputs["protocol_feature_matrix"])} == {"p1", "p2"}
    result = ProtocolQueryEngine(outputs["protocol_feature_matrix"].parent).compare_protocols("p1", "p2")
    assert result["status"] == "ok"
    assert result["feature_differences"]


def test_configuration_refuses_legacy_protocol_sources(tmp_path: Path) -> None:
    config = {"input_dir": str(tmp_path), "output_dir": str(tmp_path / "out"),
              "required_inputs": {"protocol_artifacts_jsonl": "protocol_artifacts.jsonl"}}
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    with pytest.raises(ValueError, match="protocols.jsonl"):
        ProtocolOptimizationConfig.from_file(path)


def test_api_pa_run_archives_only_formal_input_not_sidecar_files(tmp_path: Path, monkeypatch) -> None:
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from protocol_optimization_agent.interfaces.api import create_app

    monkeypatch.setenv("PROTOCOL_ACCESS_MODE", "local")
    monkeypatch.setenv("PROTOCOL_WORKSPACE_ROOT", str(tmp_path / "workspace"))
    _write(tmp_path, [_protocol()])
    (tmp_path / "unrelated.h5ad").write_bytes(b"not protocol input")
    (tmp_path / "protocol_factor_events.csv").write_text("invalid legacy events", encoding="utf-8")
    response = TestClient(create_app()).post("/protocol-analysis/run", json={
        "input_dir": str(tmp_path), "output_dir": str(tmp_path / "out"), "llm": {"provider": "offline"}})
    assert response.status_code == 200, response.text
    run_root = Path(response.json()["run_manifest"]).parent
    manifest = json.loads((run_root / "input_manifest.json").read_text(encoding="utf-8"))
    paths = [Path(item["path"]).name for entry in manifest["entries"] for item in entry["inputs"].values()]
    assert "protocols.jsonl" in paths
    assert "unrelated.h5ad" not in paths and "protocol_factor_events.csv" not in paths
