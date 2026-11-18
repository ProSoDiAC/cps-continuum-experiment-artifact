"""Offline reference for the RQ2 CPS: ideal adaptive controller, fixed-time plan, controller that never acts.

Uses the same model of the intersection as the real workload (workload.traffic) with the same seeds of the repetitions
(the demand seed depends only on the repetition), without new runs on the infrastructure. The controller
adaptive with latency L and missed decisions reproduces the behaviour of `actuate` (decision after the deadline:
not applied); the fixed plan changes phase every G seconds, ignoring the queues.
Validation: comparison with the measured RQ2 means for the same repetitions.
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd

from workload.traffic import DT, Intersection, choose_phase

PERIOD_S, WARMUP_S, DURATION_S = 1.0, 30, 300


def run_plant(seed, policy, latency_ms=0.0, miss_prob=0.0, green_s=None):
    """policy: 'adaptive' (choose_phase with latency and misses) | 'fixed' (switch every green_s) | 'never' (no action)."""
    plant = Intersection(seed)
    rng = np.random.default_rng(seed)
    pending, out, t, last_switch = None, [], 0.0, 0.0
    steps = int(round(PERIOD_S / DT))
    for _ in range(WARMUP_S + DURATION_S):
        if policy == "adaptive":
            target = choose_phase(plant.lengths(), plant.phase)
            if target != plant.phase and rng.random() >= miss_prob:
                pending = (t + latency_ms / 1000.0, target)
        elif policy == "fixed" and t - last_switch >= green_s:
            pending = (t, "EW" if plant.phase == "NS" else "NS")
            last_switch = t
        for _ in range(steps):
            if pending and t >= pending[0]:
                plant.switch(pending[1], t)
                pending = None
            plant.step(t)
            t += DT
        out.append(plant.window(t))
    w = np.array(out[WARMUP_S:])
    return float(w[:, 0].mean()), float(w[:, 1].mean())


def rep_seeds(results):
    """Demand seed per repetition: from a run folder (provenance.json) or from a CSV (repetition, seed)."""
    if str(results).endswith(".csv"):
        s = pd.read_csv(results).sort_values("repetition")
        return [int(x) for x in s.seed]
    seeds = {}
    for f in Path(results).glob("*/provenance.json"):
        d = json.load(open(f))
        seeds[d["repetition"]] = d["seed"]
    return [seeds[k] for k in sorted(seeds)]


def run(results, measured_csv, out):
    seeds = rep_seeds(results)
    meas = pd.read_csv(measured_csv)
    P, D, L = "placement.state-estimator", "wan.delay_ms", "wan.loss_pct"

    def measured(place, delay, loss):
        m = meas[(meas[P] == place) & (meas[D] == delay) & (meas[L] == loss)].sort_values("repetition")
        return m.wait_mean.values, m.queue_mean.values

    cases = [
        ("adaptive, no latency (ideal)", dict(policy="adaptive"), None),
        ("adaptive, fog latency (112 ms)", dict(policy="adaptive", latency_ms=111.7), ("fog", 10, 0)),
        ("adaptive, cloud 50 ms, 1% loss", dict(policy="adaptive", latency_ms=175.6, miss_prob=0.043), ("cloud", 50, 1)),
        ("no action (all decisions late)", dict(policy="never"), ("cloud", 100, 0)),
    ] + [(f"fixed-time, {g} s per phase", dict(policy="fixed", green_s=g), None) for g in (15, 30, 45, 60)]
    rows = []
    for name, kw, ref in cases:
        sim = np.array([run_plant(s, **kw) for s in seeds])
        row = {"case": name, "sim_wait": sim[:, 0].mean(), "sim_queue": sim[:, 1].mean()}
        if ref:
            mw, mq = measured(*ref)
            n = min(len(mw), len(sim))
            row.update(meas_wait=mw[:n].mean(), meas_queue=mq[:n].mean(),
                       wait_abs_err=np.abs(sim[:n, 0] - mw[:n]).mean())
        rows.append(row)
    t = pd.DataFrame(rows)
    t.to_csv(Path(out) / "baseline_fixed.csv", index=False)
    return t
