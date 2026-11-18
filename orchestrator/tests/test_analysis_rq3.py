import pandas as pd

from analysis import rq3


def _run(run_id, rep, level, lat, gw, agent=None):
    rows = [(i, "app.e2e_latency", lat, "edge") for i in range(10)]
    rows += [(i, "app.deadline_miss", 0.0, "edge") for i in range(10)]
    rows += [(i, "app.gateway_cpu", gw, "edge") for i in range(10)]
    if agent:
        for tier in ("edge", "fog", "cloud"):
            rows += [(i, "infra.cpu", 5.0, tier) for i in range(9)]            # 9 of 10 expected: completeness 90%
            rows += [(10, "obs.agent_cpu_s", agent, tier), (10, "obs.agent_probes", 20.0, tier)]
            rows += [(i, "net.tx_bytes", 100.0, tier) for i in range(9)]
    df = pd.DataFrame(rows, columns=["t_s", "metric", "value", "tier"])
    df["run_id"], df["repetition"], df[rq3.LEVEL] = run_id, rep, level
    return df


def test_overhead_completeness_and_paired_difference():
    df = pd.concat([_run("a0", 0, "minimal", 175.0, 0.2), _run("b0", 0, "full", 176.0, 0.5, agent=0.5),
                    _run("a1", 1, "minimal", 175.5, 0.2), _run("b1", 1, "full", 177.0, 0.7, agent=0.5)])
    runs = rq3.per_run(df, dur_s=10.0)
    full = runs[runs[rq3.LEVEL] == "full"].iloc[0]
    assert abs(full.agent_cpu_pct_per_node - 5.0) < 1e-9                         # 0,5 s su 10 s = 5% di un core
    assert abs(full.probe_Bps_per_node - 20 * 2 * 84 / 10) < 1e-9
    assert abs(full.completeness - 0.9) < 1e-9
    pr = rq3.paired(runs)
    assert pr.n_pairs == 2
    assert abs(pr.lat_median_delta_mean - 1.25) < 1e-9 and pr.lat_median_delta_min == 1.0 and pr.lat_median_delta_max == 1.5
    assert abs(pr.gateway_cpu_delta_mean - 0.4) < 1e-9
