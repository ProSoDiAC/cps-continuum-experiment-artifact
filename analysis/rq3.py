"""RQ3 analysis: cost of observability (minimal vs full) with a comparison paired by repetition.

Overhead = agent CPU, probe traffic, effect on the end-to-end latency and on the gateway CPU.
Completeness = samples collected by the agents / expected samples (duration / sampling_s) per node.
The traffic seed is shared between the levels of the same repetition, so the difference full - minimal
is paired. With few repetitions the mean and the min-max range of the differences are reported, not a CI.
"""
import numpy as np
import pandas as pd

LEVEL = "observability_level"
ICMP_BYTES = 84            # echo request IPv4 (20 IP + 8 ICMP + 56 dati); la risposta ha la stessa dimensione


def per_run(df, dur_s, sampling_s=1.0):
    rows = []
    for run_id, g in df.groupby("run_id"):
        lat = g[g.metric == "app.e2e_latency"]["value"]
        row = {"run_id": run_id, "repetition": g.repetition.iloc[0], LEVEL: g[LEVEL].iloc[0],
               "lat_median": lat.median(), "lat_p99": lat.quantile(0.99),
               "miss_rate": g[g.metric == "app.deadline_miss"]["value"].mean(),
               "gateway_cpu": g[g.metric == "app.gateway_cpu"]["value"].mean()}
        agent = g[g.metric == "obs.agent_cpu_s"]
        if len(agent):
            row["agent_cpu_pct_per_node"] = 100.0 * agent["value"].mean() / dur_s      # % of a core, mean per node
            probes = g[g.metric == "obs.agent_probes"]["value"]
            row["probe_Bps_per_node"] = probes.mean() * 2 * ICMP_BYTES / dur_s
            cpu = g[g.metric == "infra.cpu"]
            row["completeness"] = float(np.mean([len(x) for _, x in cpu.groupby("tier")]) / (dur_s / sampling_s))
            row["node_cpu_mean"] = cpu["value"].mean()
            row["net_tx_Bps_mean"] = g[g.metric == "net.tx_bytes"]["value"].mean()
        rows.append(row)
    return pd.DataFrame(rows)


def paired(runs, metrics=("lat_median", "lat_p99", "miss_rate", "gateway_cpu")):
    mn = runs[runs[LEVEL] == "minimal"].set_index("repetition")
    fu = runs[runs[LEVEL] == "full"].set_index("repetition")
    common = mn.index.intersection(fu.index)
    out = {"n_pairs": len(common)}
    for m in metrics:
        d = (fu.loc[common, m] - mn.loc[common, m]).to_numpy(float)
        out[m + "_delta_mean"], out[m + "_delta_min"], out[m + "_delta_max"] = d.mean(), d.min(), d.max()
    return pd.Series(out)


def latex_table(runs, pr, path):
    g = runs.groupby(LEVEL)
    lines = [r"\begin{tabular}{lrrrrrr}", r"\toprule",
             r"Level & Median e2e (ms) & p99 e2e (ms) & Gateway CPU (\%) & Agent CPU (\% core) & Probes (B/s) & Complete \\",
             r"\midrule"]
    for lvl, x in g:
        ag = (f"{x.agent_cpu_pct_per_node.mean():.2f} & {x.probe_Bps_per_node.mean():.0f} & "
              f"{100 * x.completeness.mean():.1f}\\%") if lvl == "full" else "-- & -- & --"
        lines.append(f"{lvl} & {x.lat_median.mean():.1f} & {x.lat_p99.mean():.1f} & {x.gateway_cpu.mean():.2f} & {ag} \\\\")
    lines += [r"\midrule",
              f"$\\Delta$ (full $-$ minimal) & {pr.lat_median_delta_mean:+.2f} & {pr.lat_p99_delta_mean:+.2f} & "
              f"{pr.gateway_cpu_delta_mean:+.2f} & & & \\\\",
              r"\bottomrule", r"\end{tabular}"]
    path.write_text("\n".join(lines) + "\n")


def run(df, out, dur_s, sampling_s=1.0):
    runs = per_run(df, dur_s, sampling_s)
    pr = paired(runs)
    runs.to_csv(out / "rq3_per_run.csv", index=False)
    pr.to_csv(out / "rq3_paired.csv", header=["value"])
    latex_table(runs, pr, out / "rq3_table.tex")
    return runs, pr
