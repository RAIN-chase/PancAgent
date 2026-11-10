"""Create a tiny synthetic AnnData and publish it through the real catalog CLI."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import anndata as ad
import numpy as np
import pandas as pd


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="New directory outside the checkout")
    args = parser.parse_args()
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    path = root / "synthetic.h5ad"
    counts = np.array([[2, 0], [4, 1], [6, 0], [8, 1]], dtype=np.int32)
    ad.AnnData(
        X=counts,
        obs=pd.DataFrame(
            {"cell_type": ["demo_a", "demo_b"] * 2, "sample_id": ["s1", "s1", "s2", "s2"]},
            index=["c1", "c2", "c3", "c4"],
        ),
        var=pd.DataFrame(index=["DEMO1", "DEMO2"]),
    ).write_h5ad(path)
    catalog = root / "catalog.json"
    catalog.write_text(json.dumps({
        "schema_version": "data-catalog-v1", "version": "tutorial-v1", "collections": [],
        "datasets": [{
            "dataset_id": "tutorial-synthetic", "name": "Synthetic tutorial v1", "role": "user_scrna",
            "user_id": "local_user", "project_id": "default", "protocol_id": "tutorial_a",
            "cell_type_column": "cell_type", "sample_column": "sample_id",
            "description": "Synthetic software fixture; not biological evidence.",
            "status": "pending", "h5ad_path": str(path),
        }],
    }, indent=2), encoding="utf-8")
    subprocess.run([sys.executable, "-m", "protocol_optimization_agent.interfaces.run_archive_cli",
                    "publish-catalog", "--catalog", str(catalog), "--dataset-id", "tutorial-synthetic"], check=True)
    print(f"PROTOCOL_DATA_CATALOG={catalog}")


if __name__ == "__main__":
    main()
