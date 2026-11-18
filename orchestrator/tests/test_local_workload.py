"""Integration: real components + real netem in network namespaces (skipped without root)."""
import statistics as s
from pathlib import Path

import pytest

from descriptor.validate import load
from orchestrator.backends.local import LocalBackend
from orchestrator.netem import netns_available
from orchestrator.sweep import expand

D = load(Path(__file__).resolve().parents[2] / "descriptor" / "example.yaml")
pytestmark = pytest.mark.skipif(not netns_available(), reason="servono root e network namespace")


def _pick(placement, delay, loss):
    return next(c for c in expand(D) if c.placement["state-estimator"] == placement
                and c.wan["delay_ms"] == delay and c.wan["loss_pct"] == loss)


def _median_latency(backend, cfg):
    backend.apply_impairments(cfg)
    samples, status = backend.run_workload(D, cfg, 1)
    assert status == "completed"
    lat = [v for _, m, v, _ in samples if m == "app.e2e_latency"]
    assert len(lat) >= 5
    return s.median(lat)


def test_cloud_placement_pays_the_wan_and_fog_does_not():
    b = LocalBackend(duration_s=6, warmup_s=2)
    b.provision(D)
    try:
        fog = _median_latency(b, _pick("fog", 50, 0))
        cloud = _median_latency(b, _pick("cloud", 50, 0))
    finally:
        b.teardown()
    # fog: 80+30 ms di calcolo; cloud: 40+30 ms + due attraversamenti WAN da 50 ms
    assert 100 <= fog <= 140
    assert cloud - fog >= 40


def test_teardown_removes_namespaces():
    import subprocess
    b = LocalBackend(duration_s=2, warmup_s=1)
    b.provision(D)
    names = list(b.ns.values())
    b.teardown()
    out = subprocess.run("ip netns list", shell=True, capture_output=True, text=True).stdout
    assert not any(n in out for n in names)
