"""Output contract: layout of a results folder per run.

experiments/results/<run-id>/
    metrics.csv       colonne: t_s, metric, value, tier
    provenance.json   chiavi: REQUIRED_PROVENANCE
    run.log
"""
from pathlib import Path
import json

REQUIRED_FILES = ("metrics.csv", "provenance.json", "run.log")
REQUIRED_PROVENANCE = ("descriptor_hash", "node_inventory", "impairment_log", "started_at",
                       "simulated", "backend", "domains", "federated")


def check_run_dir(path):
    """Returns the list of problems (empty if the folder respects the contract)."""
    p = Path(path)
    errs = [f"manca {f}" for f in REQUIRED_FILES if not (p / f).is_file()]
    prov = p / "provenance.json"
    if prov.is_file():
        data = json.loads(prov.read_text())
        errs += [f"provenance: manca '{k}'" for k in REQUIRED_PROVENANCE if k not in data]
    return errs
