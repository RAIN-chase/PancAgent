from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.errors import GraphRecursionError
from protocol_optimization_agent.agents.data_analysis import DataAnalysisAgent, data_analysis_agent
from protocol_optimization_agent.agents.orchestrator import build_orchestrator_graph
from protocol_optimization_agent.analysis.protocol.query_engine import ProtocolQueryEngine
from protocol_optimization_agent.runtime.archive import RunArchive, audited_operation, fingerprint, redact
from protocol_optimization_agent.runtime.context import RunContext, content_fingerprint, write_run_manifest
from protocol_optimization_agent.runtime.dataset_registry import register_dataset


def _json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _archive(tmp_path: Path) -> RunArchive:
    archive = RunArchive(RunContext.from_values(workspace_root=tmp_path, run_id="r1"))
    archive.start({"api_key": "never-persist-me", "normalization": "log1p"}, thread_id="thread1")
    return archive


def _graph_input(tmp_path: Path, run_id: str, question: str = "hello") -> dict:
    return {
        "messages": [HumanMessage(content=question)],
        "workspace_root": str(tmp_path),
        "run_id": run_id,
        "protocol_output_dir": str(tmp_path / "missing_pa"),
        "data_analysis_output_dir": str(tmp_path / "missing_da"),
        "optimization_output_dir": str(tmp_path / "missing_po"),
    }


def test_run_archive_is_complete_redacted_and_immutable(tmp_path: Path) -> None:
    archive = _archive(tmp_path)
    source = tmp_path / "result.csv"
    source.write_text("gene,mean\nINS,1\n", encoding="utf-8")
    archive.inputs({"data": str(source)})
    with archive.scope(), archive.operation("query_expression", {"gene": "INS", "api_key": "never-persist-me"}) as call:
        archive.snapshot_outputs({"expression": source}, call)
        archive.result(call, {"evidence_refs": [{"gene": "INS"}]})
    created = _json(archive.root / "run_manifest.json")["created_at_utc"]
    archive.finish({"content": "INS expression is 1", "evidence_refs": [{"gene": "INS"}]})
    expected = {
        "config.json",
        "input_manifest.json",
        "model_manifest.json",
        "environment.json",
        "prompts.jsonl",
        "tool_calls.jsonl",
        "evidence.json",
        "final_response.json",
        "final_report.md",
        "audit_report.json",
        "errors.jsonl",
    }
    assert expected <= {path.name for path in archive.root.iterdir()}
    manifest = _json(archive.root / "run_manifest.json")
    assert manifest["created_at_utc"] == created
    assert manifest["status"] == "succeeded"
    assert manifest["duration_seconds"] >= 0
    assert _json(archive.root / "final_response.json")["archive_provenance"]["evidence_refs"] == [{"gene": "INS"}]
    source.write_text("gene,mean\nINS,999\n", encoding="utf-8")
    artifact = manifest["artifacts"][0]
    assert "INS,1" in Path(artifact["path"]).read_text(encoding="utf-8")
    assert "never-persist-me" not in (archive.root / "config.json").read_text(encoding="utf-8")
    assert "never-persist-me" not in (archive.root / "parameters" / f"{call}.json").read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="immutable"):
        archive.record("manifest_task", {})
    before = list((archive.root / "parameters").glob("*.json"))
    with pytest.raises(ValueError, match="immutable"), archive.operation("late_write", {}):
        pass
    assert list((archive.root / "parameters").glob("*.json")) == before


def test_credentials_in_headers_and_exception_text_are_redacted(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "secret_for_test_exception")
    payload = redact(
        {
            "headers": {"x-api-key": "hidden"},
            "token": "hidden",
            "total_tokens": 23,
            "error": "Bad credential secret_for_test_exception",
        }
    )
    assert payload["headers"]["x-api-key"] == "[REDACTED]"
    assert payload["token"] == "[REDACTED]"
    assert payload["total_tokens"] == 23
    assert "secret_for_test_exception" not in payload["error"]
    biology = redact(
        {
            "secretory_beta_score": 0.42,
            "reagent_secretin": 10,
            "client_secret": "hidden",
            "api_key_env": "OPENAI_API_KEY",
        }
    )
    assert biology["secretory_beta_score"] == 0.42
    assert biology["reagent_secretin"] == 10
    assert biology["client_secret"] == "[REDACTED]"
    assert biology["api_key_env"] == "OPENAI_API_KEY"


def test_parallel_dataset_publication_returns_one_stable_version(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "input.h5ad"
    source.write_bytes(b"dataset")
    with ThreadPoolExecutor(max_workers=3) as pool:
        entries = list(
            pool.map(lambda _: register_dataset(source, tmp_path / "datasets", tmp_path / "registry"), range(3))
        )
    assert len({item["sha256"] for item in entries}) == 1
    monkeypatch.setenv("PROTOCOL_DATASET_REGISTRY", str(tmp_path / "registry"))
    assert fingerprint(Path(entries[0]["path"]))["sha256"] == entries[0]["sha256"]


def test_input_fingerprint_detects_same_size_same_mtime_change(tmp_path: Path) -> None:
    path = tmp_path / "data.h5ad"
    path.write_bytes(b"abc")
    first = fingerprint(path)
    path.write_bytes(b"xyz")
    os.utime(path, ns=(path.stat().st_atime_ns, first["mtime_ns"]))
    assert fingerprint(path)["sha256"] != first["sha256"]
    directory = fingerprint(tmp_path)
    assert directory["files"]["data.h5ad"]["sha256"] == fingerprint(path)["sha256"]


def test_large_file_is_not_skipped_and_registered_version_avoids_rehash(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "large.h5ad"
    # A sparse filesystem allocation exercises the old 100 MiB threshold without a large fixture.
    with source.open("wb") as handle:
        handle.truncate(101 * 1024 * 1024)
    identity = fingerprint(source)
    assert identity["sha256_status"] == "computed"
    assert identity["sha256"]
    receipt = register_dataset(source, tmp_path / "datasets", tmp_path / "registry")
    monkeypatch.setenv("PROTOCOL_DATASET_REGISTRY", str(tmp_path / "registry"))
    monkeypatch.setattr(
        "protocol_optimization_agent.runtime.archive.file_hash", lambda path: pytest.fail("Registered input rehashed")
    )
    registered = fingerprint(Path(receipt["path"]))
    assert registered["sha256"] == identity["sha256"]
    assert registered["sha256_status"] == "registered_readonly_version"
    Path(receipt["path"]).chmod(0o600)
    with pytest.raises(ValueError, match="read-only"):
        fingerprint(Path(receipt["path"]))


def test_changed_input_marks_run_partial(tmp_path: Path) -> None:
    archive = _archive(tmp_path)
    source = tmp_path / "data.h5ad"
    source.write_bytes(b"abc")
    archive.inputs({"data": str(source)})
    source.write_bytes(b"xyz")
    archive.finish({"content": "Potentially inconsistent calculation"})
    assert _json(archive.root / "run_manifest.json")["status"] == "partial"
    assert "InputVersionChanged" in (archive.root / "errors.jsonl").read_text(encoding="utf-8")


def test_cache_content_identity_survives_snapshot_relocation(tmp_path: Path) -> None:
    first = tmp_path / "first.csv"
    second = tmp_path / "second.csv"
    first.write_text("INS,1\n", encoding="utf-8")
    second.write_text("INS,1\n", encoding="utf-8")
    assert content_fingerprint({"expression": str(first)}) == content_fingerprint({"expression": str(second)})
    second.write_text("INS,2\n", encoding="utf-8")
    assert content_fingerprint({"expression": str(first)}) != content_fingerprint({"expression": str(second)})


def test_parallel_tasks_do_not_lose_manifest_or_tool_events(tmp_path: Path) -> None:
    archive = _archive(tmp_path)

    def work(index: int):
        local = RunArchive(archive.context)
        with local.scope(), local.operation("test", {"index": index}):
            write_run_manifest(archive.context, output_dirs={f"agent{index}": str(tmp_path)}, extra={"index": index})

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(work, range(12)))
    manifest = _json(archive.root / "run_manifest.json")
    assert len(manifest["tasks"]) == 12
    assert len(manifest["output_dirs"]) == 12
    log = [json.loads(line) for line in (archive.root / "tool_calls.jsonl").read_text(encoding="utf-8").splitlines()]
    assert sum("arguments_path" in row for row in log) == 12
    assert sum(row.get("status") == "succeeded" for row in log) == 12


def test_internal_failure_and_intermediate_results_are_preserved(tmp_path: Path) -> None:
    archive = _archive(tmp_path)

    @audited_operation
    def calculate(gene: str):
        raise ValueError("Invalid comparison group")

    with archive.scope(), pytest.raises(ValueError):
        calculate("INS")
    archive.finish({"content": "Analysis failed"}, status="failed")
    assert "Invalid comparison group" in (archive.root / "errors.jsonl").read_text(encoding="utf-8")
    assert _json(archive.root / "run_manifest.json")["status"] == "failed"


def test_graph_state_fields_are_complete_and_content_deduplicated(tmp_path: Path) -> None:
    archive = _archive(tmp_path)
    rows = [{"gene": "INS", "expression": 1}]
    first = archive.state_snapshot({"matrix": rows, "step": 1, "ids": {"p2", "p1"}})
    second = archive.state_snapshot({"matrix": rows, "step": 2, "ids": {"p1", "p2"}})
    assert first["field_refs"]["matrix"] == second["field_refs"]["matrix"]
    assert first["field_refs"]["ids"] == second["field_refs"]["ids"]
    assert _json(Path(first["field_refs"]["matrix"]["path"])) == rows
    assert len(list((archive.root / "state_objects").glob("*.json"))) == 4


def test_cached_inputs_and_repeated_plot_outputs_keep_original_versions(tmp_path: Path) -> None:
    archive = _archive(tmp_path)
    source = tmp_path / "source"
    source.mkdir()
    matrix = source / "protocol_feature_matrix.csv"
    original = "protocol_id,reagent_x\np1,1\np2,2\n"
    matrix.write_text(original, encoding="utf-8")
    working = archive.seed(source, "protocol_analysis")
    engine = ProtocolQueryEngine(working)
    with archive.scope():
        engine.plot_feature_heatmap(features=["reagent_x"], filename="same.svg", normalize="minmax")
        engine.plot_feature_heatmap(features=["reagent_x"], filename="same.svg", normalize="none")
    (working / matrix.name).write_text("protocol_id,reagent_x\np1,99\n", encoding="utf-8")
    manifest = _json(archive.root / "run_manifest.json")
    inputs = [item for item in manifest["artifacts"] if item["role"] == "input"]
    assert Path(inputs[0]["path"]).read_text(encoding="utf-8") == original
    plots = [item for item in manifest["artifacts"] if item["name"] == "feature_matrix_heatmap"]
    assert len(plots) == 2
    assert plots[0]["sha256"] != plots[1]["sha256"]
    assert Path(plots[0]["path"]).exists()


class ToolModel(FakeMessagesListChatModel):
    def bind_tools(self, tools):
        return self


def test_graph_rerun_creates_child_and_archives_actual_model_response(tmp_path: Path) -> None:
    model = ToolModel(responses=[AIMessage(content="First answer"), AIMessage(content="Second answer")])
    graph = build_orchestrator_graph(model)
    result = graph.invoke(_graph_input(tmp_path, "first"), {"configurable": {"thread_id": "conversation"}})
    first_root = tmp_path / "users" / "local_user" / "runs" / "first"
    first_response = _json(first_root / "final_response.json")
    assert first_response["stop_reason"] == "evidence_insufficient:invalid_plan"
    assert first_response["additional_kwargs"]["analysis_report"] == _json(first_root / "final_report.json")
    assert first_response["content"] != "First answer"
    second = graph.invoke({**result, "messages": [HumanMessage(content="again")]})
    assert second["run_id"] != result["run_id"]
    second_root = first_root.parent / second["run_id"]
    assert _json(second_root / "run_manifest.json")["parent_run_id"] == "first"
    assert _json(first_root / "final_response.json") == first_response
    prompts = [json.loads(line) for line in (first_root / "prompts.jsonl").read_text(encoding="utf-8").splitlines()]
    assert prompts[0]["messages"][0]["type"] == "system"
    assert prompts[0]["response"]["content"] == "First answer"
    assert prompts[0]["cost"] is None


def test_model_failure_seals_partial_report_and_retains_error(tmp_path: Path) -> None:
    class FailingModel(ToolModel):
        def invoke(self, *args, **kwargs):
            raise RuntimeError("Provider unavailable")

    graph = build_orchestrator_graph(FailingModel(responses=[AIMessage(content="unused")]))
    result = graph.invoke(_graph_input(tmp_path, "failure"))
    assert result["stop_reason"] == "evidence_insufficient:model_error"
    assert result["budget"]["model_usage_known"] is False
    assert result["budget"]["model_cost_known"] is False
    root = tmp_path / "users" / "local_user" / "runs" / "failure"
    assert _json(root / "run_manifest.json")["status"] == "partial"
    assert _json(root / "final_report.json")["stop_reason"] == result["stop_reason"]
    assert "Provider unavailable" in (root / "errors.jsonl").read_text(encoding="utf-8")
    assert "Provider unavailable" in (root / "prompts.jsonl").read_text(encoding="utf-8")


def test_standalone_agent_runs_and_tool_cache_reads_have_separate_archives(tmp_path: Path) -> None:
    agent = DataAnalysisAgent(tmp_path, h5ad_config={"requested_tasks": ["gene_expression"], "query_genes": ["INS"]})
    first = agent.run([])
    second = agent.run([])
    assert first["gene_expression_summary"].parent != second["gene_expression_summary"].parent
    assert first["gene_expression_summary"].exists()
    root = first["gene_expression_summary"].parent.parent
    assert _json(root / "config.json")["effective_calls"][0]["settings"]["h5ad_config"]["query_genes"] == ["INS"]
    assert list((root / "intermediate").glob("*.json"))
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "gene_expression_summary.csv").write_text("gene,status,mean_expression\nINS,ok,1\n", encoding="utf-8")
    response = data_analysis_agent.invoke(
        {
            "question": "INS expression",
            "request": {"operation": "gene_expression", "genes": ["INS"]},
            "data_analysis_output_dir": str(cache),
            "workspace_root": str(tmp_path),
            "run_id": "cached",
        }
    )
    assert response["result_preview"][0]["gene"] == "INS"
    assert response["output_dir"] != str(cache)
    root = tmp_path / "users" / "local_user" / "runs" / "cached"
    assert _json(root / "input_manifest.json")["cache_sources"][0]["source"] == str(cache)
    assert _json(root / "evidence.json")["artifacts"]


def test_framework_recursion_failure_is_archived(tmp_path: Path) -> None:
    graph = build_orchestrator_graph(ToolModel(responses=[AIMessage(content="unused")]))
    with pytest.raises(GraphRecursionError):
        graph.invoke(_graph_input(tmp_path, "recursion"), {"recursion_limit": 1})
    root = tmp_path / "users" / "local_user" / "runs" / "recursion"
    assert _json(root / "run_manifest.json")["status"] == "failed"
    assert "GraphRecursionError" in (root / "errors.jsonl").read_text(encoding="utf-8")


def test_archive_verifier_detects_final_answer_tampering(tmp_path: Path, monkeypatch) -> None:
    from protocol_optimization_agent.interfaces.run_archive_cli import main

    archive = _archive(tmp_path)
    archive.finish({"content": "Original answer"})
    monkeypatch.setattr("sys.argv", ["run-archive", "verify", "--run-dir", str(archive.root)])
    main()
    (archive.root / "final_report.md").write_text("Changed answer", encoding="utf-8")
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 1


def test_query_cli_archives_private_collection_and_preserves_json_output(tmp_path: Path, monkeypatch, capsys) -> None:
    from protocol_optimization_agent.interfaces.protocol_query_cli import main

    source = tmp_path / "source"
    source.mkdir()
    matrix = source / "protocol_feature_matrix.csv"
    matrix.write_text("protocol_id,comparison_ready,reagent_x\np1,true,1\n", encoding="utf-8")
    checksum = fingerprint(matrix)["sha256"]
    monkeypatch.setenv("PROTOCOL_WORKSPACE_ROOT", str(tmp_path / "workspace"))
    monkeypatch.setattr("sys.argv", ["protocol-query", "--output-dir", str(source), "collection"])
    main()
    output = capsys.readouterr()
    response = json.loads(output.out)
    assert response["n_protocols"] == 1
    working = Path(response["output_dir"])
    assert working != source
    assert _json(working.parent / "final_response.json")["result"] == response
    assert "run_dir=" in output.err
    assert fingerprint(matrix)["sha256"] == checksum
    assert _json(working.parent / "run_manifest.json")["status"] == "succeeded"


def test_pa_cli_model_initialization_error_seals_failed_run(tmp_path: Path, monkeypatch) -> None:
    from protocol_optimization_agent.interfaces import protocol_analysis_cli as protocol_analysis_cli

    monkeypatch.setattr("sys.argv", ["protocol-analysis", "--output-dir", str(tmp_path)])

    def fail(_config):
        raise ValueError("model initialization failed")

    monkeypatch.setattr(protocol_analysis_cli, "build_llm_client", fail)
    with pytest.raises(ValueError, match="model initialization"):
        protocol_analysis_cli.main()
    manifest = next(tmp_path.rglob("run_manifest.json"))
    assert _json(manifest)["status"] == "failed"
    assert "model initialization failed" in (manifest.parent / "errors.jsonl").read_text(encoding="utf-8")
    assert (manifest.parent / "bundle_manifest.json").exists()
