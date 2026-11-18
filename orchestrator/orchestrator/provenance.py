"""Capture of the provenance of a run."""
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path


# only the code that produces the data: data, paper and documents do not make a run "dirty"
CODE_PATHS = ("orchestrator", "workload", "descriptor", "analysis")


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def code_version():
    try:
        root = Path(__file__).resolve().parents[2]
        rev = subprocess.run(["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, timeout=5)
        dirty = subprocess.run(["git", "-C", str(root), "status", "--porcelain", "--",
                                *CODE_PATHS],
                               capture_output=True, text=True, timeout=5).stdout.strip()
        return (rev.stdout.strip() or "unknown") + ("+dirty" if dirty else "")
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def domains_of(inventory):
    return sorted({n["domain"] for n in inventory})


def build(descriptor_path, backend, inventory, impairment_log, cfg, repetition, seed, status, desc=None):
    d = desc or {}
    return {
        "descriptor_hash": file_hash(descriptor_path),
        "node_inventory": inventory,
        "impairment_log": impairment_log,
        "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "domains": domains_of(inventory),
        "federated": len(domains_of(inventory)) > 1,
        "simulated": backend.simulated,
        "backend": backend.name,
        "config_id": cfg.config_id,
        "factors": cfg.factors,
        "observability_level": cfg.obs,
        "placement": cfg.placement,
        "wan": cfg.wan,
        "repetition": repetition,
        "seed": seed,
        "status": status,
        "conditions": getattr(backend, "conditions", {}),
        "code_version": code_version(),
        "timing": d.get("application", {}).get("timing"),
        "execution": {k: v for k, v in d.get("execution", {}).items() if k != "termination"} or None,
    }


def write(path, data):
    Path(path).write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
