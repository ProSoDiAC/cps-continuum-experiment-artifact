import statistics as s

from workload.traffic import simulate

N = 300


def mean_wait(lat_ms, missed, seeds=range(4)):
    runs = [simulate(N, 5, [lat_ms] * N, [missed] * N, sd) for sd in seeds]
    return s.mean(s.mean(c[0] for c in r[60:]) for r in runs)


def test_deterministic():
    a = simulate(50, 5, [200] * 50, [False] * 50, 7)
    assert a == simulate(50, 5, [200] * 50, [False] * 50, 7)
    assert a != simulate(50, 5, [200] * 50, [False] * 50, 8)


def test_outputs_sane():
    out = simulate(100, 5, [200] * 100, [False] * 100, 1)
    assert len(out) == 100
    assert all(w >= 0 and q >= 0 for w, q in out)


def test_dropped_decisions_starve_the_other_phase():
    assert mean_wait(200, True) > 10 * mean_wait(200, False)


def test_huge_latency_is_not_better_than_low_latency():
    assert mean_wait(4500, False) >= mean_wait(200, False) * 0.95
