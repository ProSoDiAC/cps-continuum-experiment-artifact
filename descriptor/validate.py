#!/usr/bin/env python3
"""Valida un experiment descriptor: schema JSON + controlli semantici."""
import json, sys, pathlib
import yaml
from jsonschema import Draft202012Validator

HERE = pathlib.Path(__file__).parent


def semantic_errors(d):
    err = []
    sites = d["infrastructure"]["sites"]
    ids = [s["id"] for s in sites]
    tiers = {s["tier"] for s in sites}
    if len(ids) != len(set(ids)):
        err.append("infrastructure.sites: id duplicati")
    for l in d["infrastructure"]["links"]:
        for end in (l["from"], l["to"]):
            if end not in ids:
                err.append(f"link {l['from']}->{l['to']}: site '{end}' not defined")
    for s in sites:
        if s.get("provider", "slices-cli") == "slices-cli" and s["site"] == "local":
            err.append(f"site {s['id']}: 'local' richiede provider 'manual'")
        if s.get("provider") == "manual" and s["site"] != "local":
            err.append(f"site {s['id']}: provider 'manual' richiede site 'local'")
    profiles = {l["profile"] for l in d["infrastructure"]["links"]}
    for p in d["infrastructure"].get("impairments", {}):
        if p not in profiles:
            err.append(f"impairments.{p}: no link with this profile")
    comps = {c["name"] for c in d["application"]["components"]}
    for c in d["application"]["components"]:
        pl = c["placement"] if isinstance(c["placement"], list) else [c["placement"]]
        for t in pl:
            if t not in tiers:
                err.append(f"componente {c['name']}: tier '{t}' assente in infrastructure")
    for f in d["application"]["dataflows"]:
        for end in (f["from"], f["to"]):
            if end not in comps:
                err.append(f"dataflow {f['from']}->{f['to']}: component '{end}' not defined")
    t = d["application"]["timing"]
    if t["deadline_ms"] > t["control_period_s"] * 1000:
        err.append("timing: deadline_ms supera control_period_s")
    ex = d["execution"]
    if ex["warmup_min"] >= ex["duration_min"]:
        err.append("execution: warmup_min >= duration_min")
    if d["observability"]["clock_sync"] == "none":
        err.append("observability: clock_sync 'none' not allowed (R5)")
    # R6: a condition cannot be both controlled and recorded
    controlled = set(d["infrastructure"].get("impairments", {}))
    for r in d["conditions"]["recorded"]:
        if r in controlled:
            err.append(f"conditions.recorded: '{r}' is also controlled")
    domains = {s["domain"] for s in sites}
    if d["experiment"].get("federated") and len(domains) < 2:
        err.append(f"experiment.federated=true richiede >=2 domini distinti (trovati: {sorted(domains)})")
    return err


def load(path):
    with open(path) as f:
        return yaml.safe_load(f)


def validate(d):
    """Returns the list of errors (schema first, then semantic checks)."""
    with open(HERE / "schema.json") as f:
        schema = json.load(f)
    errs = [f"{'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message}"
            for e in Draft202012Validator(schema).iter_errors(d)]
    return errs or semantic_errors(d)


def main(path):
    errs = validate(load(path))
    for e in errs:
        print("ERROR", e)
    print("OK" if not errs else f"{len(errs)} errori")
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else HERE / "example.yaml"))
