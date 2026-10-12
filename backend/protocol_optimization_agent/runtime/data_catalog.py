"""Server-owned h5ad directory; listing metadata never loads an expression matrix."""
from __future__ import annotations

import json
import os
from pathlib import Path

from protocol_optimization_agent.runtime.access_control import AccessPolicy, authenticated_mode

ROLES = ("reference_atlas", "public_organoid", "user_scrna")
PUBLIC_FIELDS = ("dataset_id", "name", "role", "collection_id", "protocol_id", "protocol_version",
                 "version", "artifact_id", "species", "tissue", "cell_type_column", "sample_column", "protocol_column",
                 "study_id", "source_url", "description", "n_obs", "n_vars", "size_bytes")


def dataset_catalog(identity: str | None, project_id: str, *, _source: dict | None = None) -> dict:
    if authenticated_mode():
        if not identity:
            raise PermissionError("Authenticated identity required")
        AccessPolicy().require(identity, project_id, "read")
    location = os.getenv("PROTOCOL_DATA_CATALOG")
    if not location:
        return {"schema_version": "data-catalog-v1", "configured": False, "collections": [], "datasets": []}
    source = _source if _source is not None else json.loads(Path(location).read_text(encoding="utf-8"))
    datasets = []
    seen = set()
    for entry in source.get("datasets", []):
        role = entry.get("role")
        if role not in ROLES or not entry.get("dataset_id"):
            raise ValueError("Invalid catalog role or dataset_id")
        if role == "user_scrna" and (entry.get("user_id") != (identity or "local_user") or entry.get("project_id") != project_id):
            continue
        if role != "user_scrna" and entry.get("project_ids") and project_id not in entry["project_ids"]:
            continue
        if entry["dataset_id"] in seen:
            raise ValueError("Duplicate dataset_id in catalog scope")
        seen.add(entry["dataset_id"])
        row = {key: entry[key] for key in PUBLIC_FIELDS if key in entry}
        status = entry.get("status", "pending")
        row["status"] = status if status in {"pending", "transferring", "failed", "ready"} else "pending"
        row["analysis_available"] = False
        # A transfer entry cannot become ready merely because its destination exists.
        if row["status"] == "ready":
            path = Path(entry.get("h5ad_path", ""))
            try:
                if authenticated_mode() and role == "user_scrna":
                    authorized = AccessPolicy().artifact(identity, project_id, entry.get("artifact_id", ""))
                    if authorized != path.resolve():
                        raise ValueError("Catalog and authorized artifact disagree")
                if not path.is_file() or path.suffix.lower() != ".h5ad" or not entry.get("version"):
                    raise ValueError("Missing completed h5ad or input version")
                stat = path.stat()
                if not entry.get("size_bytes") or stat.st_size != entry["size_bytes"] or stat.st_mtime_ns != entry.get("mtime_ns"):
                    raise ValueError("File size differs from published version")
                import h5py

                with h5py.File(path, "r") as handle:
                    if handle.attrs.get("encoding-type") != "anndata" or "obs" not in handle or "var" not in handle or "X" not in handle:
                        raise ValueError("Not a supported AnnData h5ad")
                    for axis, field in (("obs", "n_obs"), ("var", "n_vars")):
                        key = handle[axis].attrs.get("_index", "_index")
                        row[field] = handle[axis][key].shape[0]
                    row["obs_columns"] = list(handle["obs"].keys())
                    row["layers"] = list(handle["layers"].keys()) if "layers" in handle else []
                row["analysis_available"] = True
            except (OSError, ValueError, ImportError, PermissionError, KeyError, TypeError):
                row.update(status="unavailable", availability_note="File/version validation or artifact authorization failed")
        datasets.append(row)
    # Collection metadata is public and contains no user file paths or identities.
    collections = [{key: entry[key] for key in ("collection_id", "role", "name", "expected_count", "status", "description")
                    if key in entry} for entry in source.get("collections", []) if entry.get("role") != "user_scrna"]
    return {"schema_version": "data-catalog-v1", "configured": True,
            "catalog_version": source.get("version", "unknown"), "collections": collections, "datasets": datasets}


def catalog_manifest(dataset_id: str, identity: str, project_id: str, expected_version: str = "") -> list[dict]:
    source = json.loads(Path(os.environ["PROTOCOL_DATA_CATALOG"]).read_text(encoding="utf-8"))
    catalog = dataset_catalog(identity, project_id, _source=source)
    row = next((row for row in catalog["datasets"] if row["dataset_id"] == dataset_id), None)
    if not row or not row["analysis_available"]:
        raise ValueError("Dataset is not accessible or transfer/registration is incomplete")
    if expected_version and expected_version != row.get("version"):
        raise ValueError("Selected dataset version changed; refresh the catalog and confirm the new version")
    if authenticated_mode():
        AccessPolicy().require(identity, project_id, "write")
    entry = next(entry for entry in source["datasets"] if entry["dataset_id"] == dataset_id
                 and entry.get("version") == row.get("version")
                 and (not entry.get("project_ids") or project_id in entry["project_ids"])
                 and (entry.get("role") != "user_scrna" or
                      (entry.get("user_id") == identity and entry.get("project_id") == project_id)))
    # Only the server-owned catalog may supply shared public data paths.
    return [{**entry, "h5ad_path": str(Path(entry["h5ad_path"]).resolve())}]
