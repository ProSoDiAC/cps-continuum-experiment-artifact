"""Sweep expansion: descriptor -> list of configurations."""
import hashlib
import itertools
import json
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    config_id: str
    placement: dict   # component -> tier (all components, already resolved)
    wan: dict         # delay_ms, jitter_ms, loss_pct (valori singoli)
    factors: dict     # only the varied factors, e.g. {"placement.state-estimator": "fog", "wan.delay_ms": 50}
    obs: str = "minimal"   # observability level: minimal (application metrics only) | full (+ agents on the nodes)


def _as_list(v):
    return v if isinstance(v, list) else [v]


def expand(d):
    """Prodotto cartesiano di placement a lista, delay_ms e loss_pct."""
    comps = {c["name"]: _as_list(c["placement"]) for c in d["application"]["components"]}
    wan = d["infrastructure"].get("impairments", {}).get("wan", {})
    wan_axes = {k: _as_list(wan.get(k, 0)) for k in ("delay_ms", "loss_pct")}
    jitter = wan.get("jitter_ms", 0)

    axes = {f"placement.{n}": v for n, v in comps.items()}
    axes["observability.level"] = _as_list(d["observability"].get("level", "minimal"))
    axes.update({f"wan.{k}": v for k, v in wan_axes.items()})
    names = list(axes)
    configs = []
    for combo in itertools.product(*(axes[n] for n in names)):
        values = dict(zip(names, combo))
        factors = {n: values[n] for n in names if len(axes[n]) > 1}
        cid = hashlib.sha1(json.dumps(factors, sort_keys=True).encode()).hexdigest()[:8]
        configs.append(Config(
            config_id=cid,
            placement={n: values[f"placement.{n}"] for n in comps},
            wan={"delay_ms": values["wan.delay_ms"], "jitter_ms": jitter,
                 "loss_pct": values["wan.loss_pct"]},
            factors=factors,
            obs=values["observability.level"],
        ))
    return configs
