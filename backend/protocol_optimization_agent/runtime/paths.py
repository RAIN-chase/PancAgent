"""Repository and package locations shared by runtime archives and code fingerprints."""

from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = PACKAGE_ROOT.parents[1]


def optimization_code_paths() -> dict[str, str]:
    """Fingerprint calculation implementations, never the legacy forwarding files."""
    files = [
        *sorted((PACKAGE_ROOT / "analysis/optimization").rglob("*.py")),
        PACKAGE_ROOT / "analysis/phenotype/representation.py",
        PACKAGE_ROOT / "agents/protocol_optimizing.py",
        *(
            PACKAGE_ROOT / "science" / filename
            for filename in ("dataset.py", "identity.py", "models.py", "state.py", "ingestion.py")
        ),
    ]
    return {f"code:{path.relative_to(PACKAGE_ROOT).as_posix()}": str(path) for path in files}
