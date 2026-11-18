"""Simulated backend: seeded synthetic model. It does NOT produce scientific results."""
import random

from .base import Backend
from workload.traffic import simulate
from ..netem import netem_commands

# Parameters of the synthetic model (ms). Declared in one place, not hidden.
MODEL = {
    "compute_ms": {                      # computation cost per component and tier
        "detector-gw": {"edge": 10},
        "state-estimator": {"fog": 80, "cloud": 40},
        "signal-controller": {"fog": 30, "cloud": 15, "edge": 120},
        "actuator": {"edge": 10},
    },
    "default_compute_ms": 30,
    "lan_ms": 3,                         # one hop between different non-cloud tiers
    "retransmit_ms": 300,                # penalty for loss on a WAN crossing
    "compute_noise": 0.05,               # sigma del rumore moltiplicativo
}


class FakeBackend(Backend):
    name = "fake"
    simulated = True

    def provision(self, desc):
        self.inventory = [
            {"id": s["id"], "tier": s["tier"], "site": s["site"], "domain": s["domain"],
             "nodes": s["nodes"],
             "flavor": s.get("flavor"), "emulated": True}
            for s in desc["infrastructure"]["sites"]
        ]
        return self.inventory

    def apply_impairments(self, cfg):
        return netem_commands(cfg.wan)

    def deploy(self, desc, cfg):
        pass

    def teardown(self):
        pass

    def _crossings(self, a, b):
        """Number of WAN and LAN crossings between two tiers."""
        if a == b:
            return 0, 0
        return (1, 0) if "cloud" in (a, b) else (0, 1)

    def _latencies(self, desc, cfg, rng, n):
        """End-to-end latency (ms) of the control cycle, for n cycles."""
        app, wan, out = desc["application"], cfg.wan, []
        for _ in range(n):
            lat = 0.0
            for name, tier in cfg.placement.items():
                base = MODEL["compute_ms"].get(name, {}).get(tier, MODEL["default_compute_ms"])
                lat += base * max(0.1, rng.gauss(1.0, MODEL["compute_noise"]))
            for f in app["dataflows"]:
                wan_n, lan_n = self._crossings(cfg.placement[f["from"]], cfg.placement[f["to"]])
                lat += lan_n * MODEL["lan_ms"]
                for _ in range(wan_n):
                    lat += max(0.0, rng.gauss(wan["delay_ms"], wan["jitter_ms"]))
                    if rng.random() < wan["loss_pct"] / 100:
                        lat += MODEL["retransmit_ms"]
            out.append(lat)
        return out

    def run_workload(self, desc, cfg, seed):
        rng = random.Random(seed)
        app, ex, obs = desc["application"], desc["execution"], desc["observability"]
        period = app["timing"]["control_period_s"]
        deadline = app["timing"]["deadline_ms"]
        warmup = int(ex["warmup_min"] * 60 / period)
        total = int(ex["duration_min"] * 60 / period)
        stop_on_miss = any("deadline_miss_rate" in str(c)
                           for c in ex.get("termination", {}).get("on", []))
        lats = self._latencies(desc, cfg, rng, total)
        missed = [l > deadline for l in lats]
        # early termination: miss rate > 20% after at least 20 measured cycles
        status, end = "completed", total
        if stop_on_miss:
            m = n = 0
            for k in range(warmup, total):
                n += 1
                m += missed[k]
                if n >= 20 and m / n > 0.2:
                    status, end = "terminated_deadline_miss", k + 1
                    break
        traffic = simulate(end, period, lats, missed, seed + 1)   # rng separato
        wanted = set(obs["metrics"]) | {"app.deadline_miss"}
        samples = []
        for k in range(warmup, end):
            t = (k - warmup) * period
            row = {
                "app.e2e_latency": lats[k],
                "app.deadline_miss": float(missed[k]),
                "net.rtt": 2 * cfg.wan["delay_ms"] + rng.gauss(0, cfg.wan["jitter_ms"]),
                "infra.cpu": min(100.0, 20 + 0.05 * lats[k] + rng.gauss(0, 2)),
                "cps.avg_wait_time": traffic[k][0],
                "cps.queue_length": float(traffic[k][1]),
            }
            for m_name, v in row.items():
                if m_name in wanted:
                    samples.append((t, m_name, round(v, 3), "all"))
        return samples, status
