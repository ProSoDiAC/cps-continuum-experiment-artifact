"""Loads the runs into a DataFrame. Simulated runs are excluded by default."""
import json
from pathlib import Path

import pandas as pd


def load_runs(results_dir, include_simulated=False):
    frames = []
    for d in sorted(Path(results_dir).glob("*")):
        prov_path = d / "provenance.json"
        if not prov_path.is_file() or not (d / "metrics.csv").is_file():
            continue
        prov = json.loads(prov_path.read_text())
        if prov["simulated"] and not include_simulated:
            continue
        if prov["status"] != "completed":
            continue
        df = pd.read_csv(d / "metrics.csv")
        df["run_id"] = d.name
        df["config_id"] = prov["config_id"]
        df["repetition"] = prov["repetition"]
        df["simulated"] = prov["simulated"]
        df["observability_level"] = prov.get("observability_level", "minimal")
        df["status"] = prov["status"]
        for k, v in prov["factors"].items():
            df[k] = v
        frames.append(df)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
