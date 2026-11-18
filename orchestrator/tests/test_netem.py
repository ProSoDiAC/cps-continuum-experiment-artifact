import subprocess

import pytest

from orchestrator.netem import netem_commands, netem_lab, netns_available


def test_commands_generation():
    assert netem_commands({"delay_ms": 50, "jitter_ms": 5, "loss_pct": 1}) == [
        "tc qdisc replace dev eth0 root netem delay 50ms 5ms loss 1%"]
    assert netem_commands({"delay_ms": 0, "jitter_ms": 0, "loss_pct": 0}) == [
        "tc qdisc del dev eth0 root"]


needs_netns = pytest.mark.skipif(not netns_available(), reason="servono root e network namespace")


@needs_netns
def test_real_netem_rtt_is_twice_the_one_way_delay():
    with netem_lab() as lab:
        base = lab.ping(5)["avg_ms"]
        lab.apply({"delay_ms": 20, "jitter_ms": 0, "loss_pct": 0})
        rtt = lab.ping(8)["avg_ms"]
    assert base < 5
    assert 38 <= rtt <= 50


@needs_netns
def test_lab_cleans_up_namespaces():
    with netem_lab() as lab:
        names = (lab.a, lab.b)
    out = subprocess.run("ip netns list", shell=True, capture_output=True, text=True).stdout
    assert not any(n in out for n in names)


@needs_netns
def test_peer_filter_delays_only_the_peer():
    from orchestrator.netem import netem_peer_commands, _sh
    with netem_lab() as lab:
        _sh(f"ip -n {lab.b} addr add 10.99.0.3/24 dev {lab.vb}")             # second address, not filtered
        for c in netem_peer_commands({"delay_ms": 30, "jitter_ms": 0, "loss_pct": 0}, lab.va, [lab.ip_b]):
            _sh(f"ip netns exec {lab.a} {c}")
        delayed = lab.ping(5)["avg_ms"]
        r = _sh(f"ip netns exec {lab.a} ping -c 5 -i 0.05 -q 10.99.0.3")
        other = float(r.stdout.split("= ")[1].split("/")[1])
    assert delayed >= 28           # outgoing delay from a (30 ms in one direction)
    assert other < 5               # unfiltered traffic: unchanged
