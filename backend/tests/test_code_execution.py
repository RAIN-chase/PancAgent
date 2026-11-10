import io
import json
import os
import subprocess
import tarfile

import pytest
from protocol_optimization_agent.agents import data_exploration as domain
from protocol_optimization_agent.analysis.phenotype.sensitivity import (
    sample_input,
    summarize_samples,
    validate_sensitivity,
)
from protocol_optimization_agent.runtime import code_executor as executor
from protocol_optimization_agent.runtime.context import RunContext


@pytest.fixture
def dataset(tmp_path, monkeypatch):
    h5py = pytest.importorskip("h5py")
    path = tmp_path / "metrics.h5ad"
    with h5py.File(path, "w") as handle:
        handle.attrs["encoding-type"] = "anndata"
        obs = handle.create_group("obs")
        obs.create_dataset("_index", data=[0, 1, 2, 3, 4, 5])
        for key, values in {"sample": [b"a", b"a", b"b", b"b", b"c", b"c"],
                            "rep": [b"r1", b"r1", b"r2", b"r2", b"r3", b"r3"],
                            "study": [b"study"] * 6, "metric": [1., 3., 4., 4., 6., 6.]}.items():
            obs.create_dataset(key, data=values)
        handle.create_group("var").create_dataset("_index", data=[b"INS"])
        handle.create_dataset("X", data=[[0]] * 6)
    row = {"dataset_id": "metrics", "version": "v1", "role": "public_organoid", "status": "ready",
           "h5ad_path": str(path), "size_bytes": path.stat().st_size, "mtime_ns": path.stat().st_mtime_ns}
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps({"datasets": [row]}), encoding="utf-8")
    monkeypatch.setenv("PROTOCOL_DATA_CATALOG", str(catalog))
    request = {"dataset_id": "metrics", "dataset_version": "v1", "endpoint": "existing_metric", "metric_column": "metric",
               "sample_column": "sample", "replicate_column": "rep", "study_column": "study", "scale": "module_score",
               "rationale": "No formal action currently performs replicate omission sensitivity"}
    context = RunContext.from_values(workspace_root=tmp_path, user_id="alice", run_id="code", project_id="project")
    return path, request, context


def test_sample_counts_are_not_replicates_and_values_are_deterministic(dataset):
    path, request, _ = dataset
    payload = sample_input(path, {**request, "seed": 0})
    result = summarize_samples(payload)
    group = result["groups"][0]
    assert payload["n_cells"] == 6 and payload["n_samples"] == 3
    assert group["n_independent_replicates"] == 3
    assert group["mean"] == 4 and group["min_after_omit"] == 3 and group["max_after_omit"] == 5
    validate_sensitivity(result, summarize_samples(payload))
    bad = json.loads(json.dumps(result))
    bad["groups"][0]["n_independent_replicates"] = 6
    with pytest.raises(ValueError):
        validate_sensitivity(bad, result)
    bad = json.loads(json.dumps(result))
    bad["groups"][0]["mean"] = float("nan")
    with pytest.raises(ValueError):
        validate_sensitivity(bad, result)


def test_conflicting_metadata_and_missing_metrics_fail_closed(dataset):
    path, request, _ = dataset
    import h5py
    with h5py.File(path, "r+") as handle:
        handle["obs/rep"][1] = b"r2"
    with pytest.raises(ValueError, match="conflicting"):
        sample_input(path, {**request, "seed": 0})
    with pytest.raises(ValueError, match="absent"):
        sample_input(path, {**request, "metric_column": "imagined", "seed": 0})


def test_default_disabled_never_executes_candidate(dataset, monkeypatch):
    _, request, context = dataset
    monkeypatch.delenv("PROTOCOL_CODE_EXECUTION", raising=False)
    monkeypatch.setattr(domain, "execute_code", lambda *args: pytest.fail("Must not execute"))
    result = domain.execute_sensitivity({"request": request}, context)
    assert result["status"] == "isolation_unavailable"
    assert not list(context.run_root().rglob("candidate.py"))


def test_request_requires_design_version_and_scale(dataset):
    _, request, context = dataset
    for field in ("replicate_column", "dataset_version", "scale"):
        invalid = {key: value for key, value in request.items() if key != field}
        assert domain.execute_sensitivity({"request": invalid}, context)["status"] == "needs_clarification"
    assert domain.execute_sensitivity({"request": {**request, "scale": "source_scale"}}, context)["status"] == "needs_clarification"
    assert domain.execute_sensitivity({"request": {**request, "h5ad_path": "/private"}}, context)["status"] == "needs_clarification"


def test_bounded_repair_archives_script_seed_fingerprint_and_exploratory_result(dataset, monkeypatch):
    _, request, context = dataset
    monkeypatch.setattr(domain, "isolation_environment", lambda: {"image_id": "sha256:" + "a" * 64})
    calls = []
    def fake(task, budget, environment):
        calls.append(task)
        if len(calls) == 1:
            return {"fake": "candidate output is not evidence"}
        return summarize_samples(json.loads((task / "input.json").read_text()))
    monkeypatch.setattr(domain, "execute_code", fake)
    result = domain.execute_sensitivity({"request": {**request, "candidate_code": "raise RuntimeError('bad')"}}, context)
    assert len(calls) == 2 and result["status"] == "ok"
    assert result["evidence_level"] == "exploratory"
    assert result["result_preview"]["seed"] == 0
    assert result["evidence_refs"][0]["dataset_version"] == "v1"
    assert result["evidence_refs"][0]["sha256"]
    assert "unreviewed" in result["promotion_status"]
    assert list(context.run_root().rglob("input_manifest.json"))
    assert "summarize_samples" in (calls[1] / "candidate.py").read_text()
    assert domain.execute_sensitivity({"request": {**request, "max_repairs": 0}}, context)["status"] == "ok"


def test_failed_code_and_changed_input_never_produce_validated_evidence(dataset, monkeypatch):
    path, request, context = dataset
    monkeypatch.setattr(domain, "isolation_environment", lambda: {"image_id": "sha256:" + "a" * 64})
    calls = []
    def invalid(*args):
        calls.append(1)
        return {}
    monkeypatch.setattr(domain, "execute_code", invalid)
    result = domain.execute_sensitivity({"request": request}, context)
    assert len(calls) == 2 and result["status"] == "analysis_failed"
    def changed(task, *args):
        stat = path.stat()
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
        return summarize_samples(json.loads((task / "input.json").read_text()))
    monkeypatch.setattr(domain, "execute_code", changed)
    assert domain.execute_sensitivity({"request": request}, context)["status"] == "analysis_failed"


def test_isolation_rejects_rootful_or_missing_cgroup_limits(monkeypatch):
    monkeypatch.setenv("PROTOCOL_CODE_EXECUTION", "rootless-docker")
    monkeypatch.setenv("PROTOCOL_CODE_IMAGE", "sha256:" + "a" * 64)
    monkeypatch.setattr(executor, "_docker", lambda *args, **kwargs: subprocess.CompletedProcess([], 0, json.dumps({"OSType": "linux", "SecurityOptions": [], "CgroupVersion": "2"}).encode()))
    with pytest.raises(executor.IsolationUnavailable):
        executor.isolation_environment()


def test_preflight_accepts_only_pinned_credential_free_rootless_image(monkeypatch):
    image = "sha256:" + "a" * 64
    monkeypatch.setenv("PROTOCOL_CODE_EXECUTION", "rootless-docker")
    monkeypatch.setenv("PROTOCOL_CODE_IMAGE", image)
    info = {"OSType": "linux", "SecurityOptions": ["name=rootless", "name=seccomp,profile=builtin"], "CgroupVersion": "2", "MemoryLimit": True,
            "CpuCfsQuota": True, "PidsLimit": True, "ServerVersion": "test"}
    details = {"Id": image, "Os": "linux", "Config": {"Env": ["PATH=/usr/local/bin"]}}
    def fake(*args, **kwargs):
        return subprocess.CompletedProcess([], 0, json.dumps(info if args[0] == "info" else [details]).encode())
    monkeypatch.setattr(executor, "_docker", fake)
    assert executor.isolation_environment()["image_id"] == image
    details["Config"]["Env"] = ["OPENAI_API_KEY=forbidden"]
    with pytest.raises(executor.IsolationUnavailable, match="Credential"):
        executor.isolation_environment()


def test_container_limits_mount_and_scan_are_not_a_host_execution_path(tmp_path):
    args = executor.container_command(tmp_path, "job", {"image_id": "sha256:" + "a" * 64}, executor.CodeBudget())
    for required in ("--network=none", "--read-only", "--cap-drop=ALL", "--pids-limit=32", "--memory=256m", "--memory-swap=256m"):
        assert required in args
    assert args.count("--mount") == 1 and ",readonly" in args[args.index("--mount") + 1]
    assert not any("docker.sock" in arg or ".env" in arg or "--privileged" in arg for arg in args)
    assert executor.scan_code(domain.recipe_code())["security_boundary"] == "container_not_AST"
    with pytest.raises(ValueError):
        executor.scan_code("import subprocess")
    with pytest.raises(SyntaxError):
        executor.scan_code("invalid python ???")


def archive(name, body=b"{}", kind=tarfile.REGTYPE):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as handle:
        item = tarfile.TarInfo(name)
        item.type = kind
        item.size = len(body) if kind == tarfile.REGTYPE else 0
        handle.addfile(item, io.BytesIO(body) if item.size else None)
    return buffer.getvalue()


@pytest.mark.parametrize("name,kind", [("../escape", tarfile.REGTYPE), ("result.json", tarfile.SYMTYPE),
                                       ("result.json", tarfile.LNKTYPE), ("result.json", tarfile.CHRTYPE)])
def test_output_cannot_escape_via_tar(name, kind):
    with pytest.raises(ValueError):
        executor.decode_output(archive(name, kind=kind))


def test_output_nonfinite_and_size_are_rejected():
    assert executor.decode_output(archive("result.json", b'{"value":2}')) == {"value": 2}
    for body in (b'{"value":NaN}', b"[]", b"x" * (1024 * 1024 + 1)):
        with pytest.raises(ValueError):
            executor.decode_output(archive("result.json", body))


def test_timeout_forces_container_cleanup_and_diagnostics(tmp_path, monkeypatch):
    (tmp_path / "candidate.py").write_text("while True: pass")
    (tmp_path / "input.json").write_text("{}")
    calls = []
    def fake(*args, **kwargs):
        calls.append(args)
        if args[0] == "exec":
            raise subprocess.TimeoutExpired("docker", 1)
        return subprocess.CompletedProcess([], 0, b"", b"")
    monkeypatch.setattr(executor, "_docker", fake)
    with pytest.raises(TimeoutError):
        executor.execute_code(tmp_path, executor.CodeBudget(timeout_seconds=1), {"image_id": "sha256:" + "a" * 64})
    assert calls[-1][:2] == ("rm", "--force")
    record = json.loads((tmp_path / "execution.json").read_text())
    assert record["container_removed"] and record["status"] == "timeout"


def test_uncertain_cleanup_quarantines_further_execution(tmp_path, monkeypatch):
    monkeypatch.setattr(executor, "_cleanup_failed", False)
    (tmp_path / "candidate.py").write_text("pass")
    (tmp_path / "input.json").write_text("{}")
    def failed(*args, **kwargs):
        raise subprocess.CalledProcessError(1, ["docker", *args])
    monkeypatch.setattr(executor, "_docker", failed)
    with pytest.raises(executor.IsolationUnavailable, match="cleanup"):
        executor.execute_code(tmp_path, executor.CodeBudget(), {"image_id": "sha256:" + "a" * 64})
    with pytest.raises(executor.IsolationUnavailable, match="quarantined"):
        executor.isolation_environment()


def test_orchestrator_archives_exploration_without_promoting_it_to_fact(dataset, monkeypatch):
    from langchain_core.messages import HumanMessage
    from protocol_optimization_agent.agents import orchestrator
    from protocol_optimization_agent.science.reporting import ground_conclusions
    from protocol_optimization_agent.science.state import ScientificWorldModel

    _, request, context = dataset
    monkeypatch.setattr(orchestrator, "_build_chat_model_from_env", lambda: None)
    monkeypatch.setattr(domain, "isolation_environment", lambda: {"image_id": "sha256:" + "a" * 64})
    monkeypatch.setattr(domain, "execute_code", lambda task, *args: summarize_samples(json.loads((task / "input.json").read_text())))
    question = "对已有指标做留一重复敏感性分析"
    result = orchestrator.build_orchestrator_graph().invoke({**context.tool_inputs(),
        "messages": [HumanMessage(content=question)],
        "protocol_output_dir": str(context.workspace_root / "pa"), "data_analysis_output_dir": str(context.workspace_root / "da"),
        "optimization_output_dir": str(context.workspace_root / "po"),
        "request": {"plan": {"question": question, "goal_type": "endpoint_analysis", "objects": ["metrics"],
            "target_metrics": ["existing_metric"], "steps": [{"id": "sensitivity", "action_id": "data.exploratory_sensitivity",
                "inputs": {"request": request}, "expected_outputs": ["numeric_results", "validation"]}]}}})
    assert result["stop_reason"] == "completed"
    report = result["messages"][-1].additional_kwargs["analysis_report"]
    assert report["conclusions"][0]["evidence_level"] == "exploratory"
    assert report["sections"]["exploratory_associations"]
    assert "single_study_driven:study" in report["confidence_caveats"]
    assert report["quantitative_bindings"]
    claim = {"claim": "Not an established fact.", "explanation": "Bounded sensitivity.", "evidence_level": "fact",
             "supporting_refs": [{"source_type": "result", "source_id": result["step_results"][0]["result_ref"]["result_id"], "field_path": ""}],
             "opposing_refs": [], "objects": [], "metrics": [], "limitations": []}
    with pytest.raises(ValueError, match="cannot be promoted"):
        ground_conclusions([claim], result["step_results"], {}, report["acceptance"], context.run_root())
    records = ScientificWorldModel(context, context.project_id).snapshot()["records"]["evidence"]
    assert records[0]["observations"]["classification"] == "exploratory_tool_observation"


@pytest.mark.skipif(os.getenv("PROTOCOL_RUN_CODE_INTEGRATION") != "1", reason="Requires an operator-provisioned rootless Linux Docker daemon")
def test_real_container_enforces_readonly_network_and_environment(tmp_path, monkeypatch):
    environment = executor.isolation_environment()
    monkeypatch.setenv("PANCAGENT_ISOLATION_TEST_SECRET", "must-not-enter-container")
    code = '''import json
checks = {}
for name, path in [('input', '/task/input.json'), ('root', '/forbidden')]:
    try:
        open(path, 'w').write('bad')
        checks[name] = False
    except OSError:
        checks[name] = True
try:
    __import__('socket').create_connection(('1.1.1.1', 80), timeout=0.5)
    checks['network'] = False
except OSError:
    checks['network'] = True
checks['secret_absent'] = __import__('os').getenv('PANCAGENT_ISOLATION_TEST_SECRET') is None
json.dump(checks, open('/out/result.json', 'w'))
'''
    (tmp_path / "candidate.py").write_text(code)
    (tmp_path / "runner.py").write_text(executor.RUNNER)
    (tmp_path / "input.json").write_text("{}")
    tmp_path.chmod(0o755)
    for path in tmp_path.iterdir():
        path.chmod(0o444)
    result = executor.execute_code(tmp_path, executor.CodeBudget(), environment)
    assert result == {"input": True, "root": True, "network": True, "secret_absent": True}
