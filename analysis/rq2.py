"""Final RQ2 analysis: per-run statistics, bootstrap CIs across runs, LaTeX table and figures for the paper.

Statistical unit = the run (n = repetitions per cell): samples within a run are autocorrelated.
CI = percentile bootstrap (95%) over the run means; with small n they are indicative.
"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

PLACE, DELAY, LOSS = "placement.state-estimator", "wan.delay_ms", "wan.loss_pct"
CELL = [PLACE, DELAY, LOSS]
COLORS = {"fog": "#0072B2", "cloud": "#D55E00"}
STYLE = {0: "-", 1: "--"}
COL_W = 4.8            # larghezza utile llncs (pollici)


def per_run(df, deadline_ms=None):
    """One row per run: median and p99 latency, miss rate, mean waiting time and queue."""
    rows = []
    for run_id, g in df.groupby("run_id"):
        lat = g[g.metric == "app.e2e_latency"]["value"]
        row = {"run_id": run_id, **{k: g[k].iloc[0] for k in CELL}, "repetition": g.repetition.iloc[0],
               "lat_median": lat.median(), "lat_p99": lat.quantile(0.99),
               "miss_rate": g[g.metric == "app.deadline_miss"]["value"].mean(),
               "wait_mean": g[g.metric == "cps.avg_wait_time"]["value"].mean(),
               "queue_mean": g[g.metric == "cps.queue_length"]["value"].mean(),
               "rtt": g[g.metric == "net.rtt"]["value"].mean()}
        rows.append(row)
    return pd.DataFrame(rows)


def boot_ci(x, n_boot=10000, seed=0):
    x = np.asarray(x, float)
    if len(x) < 2:
        return float(x.mean()), np.nan, np.nan
    rng = np.random.default_rng(seed)
    means = rng.choice(x, size=(n_boot, len(x)), replace=True).mean(axis=1)
    return float(x.mean()), *np.percentile(means, [2.5, 97.5])


def summarize(runs, metrics=("lat_median", "lat_p99", "miss_rate", "wait_mean", "queue_mean", "rtt")):
    out = []
    for key, g in runs.groupby(CELL):
        row = dict(zip(CELL, key), n=len(g))
        for m in metrics:
            row[m], row[m + "_lo"], row[m + "_hi"] = boot_ci(g[m])
        row["lat_median_cv"] = g["lat_median"].std(ddof=1) / g["lat_median"].mean() if len(g) > 1 else np.nan
        out.append(row)
    return pd.DataFrame(out).sort_values(CELL).reset_index(drop=True)


def paired_vs_fog(runs, metrics=("wait_mean", "queue_mean", "lat_median")):
    """Cloud - fog difference at equal delay, loss and repetition (same traffic): bootstrap CI across repetitions."""
    key = [DELAY, LOSS, "repetition"]
    fog = runs[runs[PLACE] == "fog"].set_index(key)
    cloud = runs[runs[PLACE] == "cloud"].set_index(key)
    rows = []
    for (d, l), g in cloud.reset_index().groupby([DELAY, LOSS]):
        row = {DELAY: d, LOSS: l, "n": len(g)}
        for m in metrics:
            diff = [r[m] - fog.loc[(d, l, r["repetition"]), m] for _, r in g.iterrows()]
            row[m + "_delta"], row[m + "_delta_lo"], row[m + "_delta_hi"] = boot_ci(diff)
        rows.append(row)
    return pd.DataFrame(rows).sort_values([DELAY, LOSS]).reset_index(drop=True)


def latex_table(s, path, runs=None):
    """RQ2 table; with `runs` the fog rows (insensitive to the delay) are grouped by loss, over all runs of the three delays."""
    lines = [r"\begin{tabular}{@{}llrrrr@{}}", r"\toprule",
             r"Placement & Loss & Delay & Median e2e (ms) & p99 e2e (ms) & Miss rate \\",
             r"\midrule"]

    def row(p, l, d, m):
        return (f"{p} & {l:g}\\% & {d} & "
                f"{m['lat_median']:.1f} [{m['lat_median_lo']:.1f}, {m['lat_median_hi']:.1f}] & "
                f"{m['lat_p99']:.0f} [{m['lat_p99_lo']:.0f}, {m['lat_p99_hi']:.0f}] & "
                f"{m['miss_rate']:.3f} [{m['miss_rate_lo']:.3f}, {m['miss_rate_hi']:.3f}] \\\\")
    for _, r in s[s[PLACE] == "cloud"].iterrows():
        lines.append(row("cloud", r[LOSS], f"{r[DELAY]:g}", r))
    if runs is None:
        for _, r in s[s[PLACE] == "fog"].iterrows():
            lines.append(row("fog", r[LOSS], f"{r[DELAY]:g}", r))
    else:
        for l, g in runs[runs[PLACE] == "fog"].groupby(LOSS):
            # mean over delays per repetition: the 3 runs of a repetition share the demand seed (they are not independent)
            g = g.drop(columns=["run_id"]).groupby(["repetition"], as_index=False).mean(numeric_only=True)
            g[PLACE], g[LOSS], g[DELAY] = "fog", l, 0
            pooled = summarize(g)
            lines.append(row("fog", l, "10--100", pooled.iloc[0]))
    lines += [r"\bottomrule", r"\end{tabular}"]
    path.write_text("\n".join(lines) + "\n")


def _lines(ax, s, metric, ylabel, deadline=None, fs=7):
    for (p, l), g in s.groupby([PLACE, LOSS]):
        g = g.sort_values(DELAY)
        ax.errorbar(g[DELAY], g[metric], yerr=[g[metric] - g[metric + "_lo"], g[metric + "_hi"] - g[metric]],
                    color=COLORS[p], ls=STYLE[int(l > 0)], marker="o", ms=3, capsize=2, lw=1,
                    label=f"{p}, loss {l:g}%")
    if deadline:
        ax.axhline(deadline, color="gray", lw=0.8, ls=":")
        ax.text(s[DELAY].min(), deadline, " deadline", va="bottom", ha="left", fontsize=fs - 1, color="gray")
    ax.set_xlabel("WAN delay per direction (ms)", fontsize=fs)
    ax.set_ylabel(ylabel, fontsize=fs)
    ax.set_xticks(sorted(s[DELAY].unique()))
    ax.tick_params(labelsize=fs)


def fig_latency(s, path, deadline):
    fig, axes = plt.subplots(1, 2, figsize=(COL_W, 2.2))
    _lines(axes[0], s, "lat_median", "median e2e latency (ms)", deadline)
    _lines(axes[1], s, "lat_p99", "p99 e2e latency (ms)", deadline)
    axes[0].legend(fontsize=5.5, frameon=False, loc="center right")
    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)


def fig_cps(s, path):
    fig, axes = plt.subplots(1, 2, figsize=(COL_W, 2.2))
    _lines(axes[0], s, "wait_mean", "mean waiting time (s)")
    _lines(axes[1], s, "queue_mean", "mean queue (vehicles)")
    axes[0].legend(fontsize=5.5, frameon=False)
    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)


def fig_heatmap(s, path):
    losses = sorted(s[LOSS].unique())
    fig, axes = plt.subplots(1, len(losses), figsize=(COL_W, 1.9), squeeze=False, sharey=True)
    vmax = max(0.05, float(s.miss_rate.max()))
    for ax, loss in zip(axes[0], losses):
        piv = s[s[LOSS] == loss].pivot(index=PLACE, columns=DELAY, values="miss_rate")
        im = ax.imshow(piv.values, vmin=0, vmax=vmax, cmap="viridis", aspect="auto")
        ax.set_xticks(range(len(piv.columns)), [f"{c:g}" for c in piv.columns], fontsize=7)
        ax.set_yticks(range(len(piv.index)), list(piv.index), fontsize=7)
        ax.set_xlabel("WAN delay (ms)", fontsize=7)
        ax.set_title(f"loss {loss:g}%", fontsize=8)
        for (i, j), v in np.ndenumerate(piv.values):
            ax.text(j, i, f"{v:.2f}", ha="center", va="center", fontsize=7,
                    color="w" if v < 0.6 * vmax else "k")
    cb = fig.colorbar(im, ax=axes[0].tolist(), label="deadline miss rate", pad=0.02)
    cb.ax.tick_params(labelsize=6)
    cb.set_label("deadline miss rate", fontsize=7)
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)


def fig_tails(df, path, deadline):
    """Cumulative distribution of the latency (pooled samples) for cloud with loss 0 vs 1%."""
    lat = df[df.metric == "app.e2e_latency"]
    fig, axes = plt.subplots(1, 2, figsize=(COL_W, 2.2), sharey=True)
    for ax, d in zip(axes, sorted(lat[DELAY].unique())[1:]):
        for l, ls in ((0, "-"), (1, "--")):
            v = np.sort(lat[(lat[PLACE] == "cloud") & (lat[DELAY] == d) & (lat[LOSS] == l)]["value"].values)
            if len(v):
                ax.plot(v, 1 - np.arange(1, len(v) + 1) / len(v), color=COLORS["cloud"], ls=ls, lw=1,
                        label=f"loss {l:g}%")
        ax.axvline(deadline, color="gray", lw=0.8, ls=":")
        ax.set_yscale("log")
        ax.set_xlim(right=1100)
        ax.set_xlabel("e2e latency (ms)", fontsize=7)
        ax.set_title(f"cloud, {d:g} ms", fontsize=8)
        ax.tick_params(labelsize=7)
    axes[0].set_ylabel("P(latency > x)", fontsize=7)
    axes[0].legend(fontsize=6, frameon=False)
    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)


def fig_mechanism(df, s, path, deadline, delay=50):
    """(a) latency tail at fixed delay: the loss creates the misses; (b) mean waiting time: the misses weigh on the CPS only if frequent."""
    FS = 10  # included at 0.9\linewidth: about 9 pt in the final PDF
    lat = df[df.metric == "app.e2e_latency"]
    fig, axes = plt.subplots(1, 2, figsize=(COL_W, 2.6))
    ax = axes[0]
    for p in ("cloud", "fog"):
        for l in (0, 1):
            v = np.sort(lat[(lat[PLACE] == p) & (lat[DELAY] == delay) & (lat[LOSS] == l)]["value"].values)
            if len(v):
                ax.plot(v, 1 - np.arange(1, len(v) + 1) / len(v), color=COLORS[p], ls=STYLE[l], lw=1,
                        label=f"{p}, loss {l:g}%")
    ax.axvline(deadline, color="gray", lw=0.8, ls=":")
    ax.text(deadline + 10, 7e-4, "deadline", fontsize=FS - 1, color="gray", va="bottom", ha="left")
    v = lat[(lat[PLACE] == "cloud") & (lat[DELAY] == delay) & (lat[LOSS] == 1)]["value"].values
    if len(v):
        miss = float((v > deadline).mean())
        ax.plot([deadline], [miss], "o", color=COLORS["cloud"], ms=3)
        ax.annotate(f"{miss:.1%} beyond\ndeadline", (deadline, miss), xytext=(deadline + 100, miss * 5),
                    fontsize=FS, color=COLORS["cloud"], arrowprops=dict(arrowstyle="-", lw=0.5, color=COLORS["cloud"]))
    ax.set_yscale("log")
    ax.set_xlim(60, 1000)
    ax.set_xlabel("e2e latency (ms)", fontsize=FS)
    ax.set_ylabel("P(latency > x)", fontsize=FS)
    ax.set_title(f"(a) WAN delay {delay:g} ms", fontsize=FS)
    ax.tick_params(labelsize=FS)
    handles, labels = ax.get_legend_handles_labels()
    _lines(axes[1], s, "wait_mean", "mean waiting time (s)", fs=FS)
    axes[1].set_xlabel("WAN delay (ms)", fontsize=FS)
    axes[1].set_title("(b) cyber-physical impact", fontsize=FS)
    fig.legend(handles, labels, fontsize=FS, frameon=False, loc="lower center", ncol=2)
    fig.tight_layout(rect=(0, 0.15, 1, 1))
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)


def run(df, out, deadline):
    runs = per_run(df)
    s = summarize(runs)
    runs.to_csv(out / "rq2_per_run.csv", index=False)
    s.to_csv(out / "rq2_summary.csv", index=False)
    latex_table(s, out / "rq2_table.tex", runs)
    paired_vs_fog(runs).to_csv(out / "rq2_paired_vs_fog.csv", index=False)
    fig_latency(s, out / "rq2_latency.pdf", deadline)
    fig_cps(s, out / "rq2_cps.pdf", )
    fig_heatmap(s, out / "rq2_miss_heatmap.pdf")
    fig_tails(df, out / "rq2_tails.pdf", deadline)
    fig_mechanism(df, s, out / "rq2_mechanism.pdf", deadline)
    return s
