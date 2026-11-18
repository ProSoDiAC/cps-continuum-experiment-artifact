"""Figures: latency box plot per configuration and heatmap of the deadline miss rate."""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

PLACE, DELAY, LOSS = "placement.state-estimator", "wan.delay_ms", "wan.loss_pct"


def _watermark(ax, df):
    if df["simulated"].any():
        ax.text(0.5, 0.5, "SIMULATED", transform=ax.transAxes, ha="center", va="center",
                fontsize=40, color="red", alpha=0.15, rotation=25)


def latency_boxplot(df, path):
    lat = df[df["metric"] == "app.e2e_latency"]
    keys = sorted(lat.groupby([PLACE, DELAY, LOSS]).groups)
    data = [lat[(lat[PLACE] == p) & (lat[DELAY] == d) & (lat[LOSS] == l)]["value"] for p, d, l in keys]
    fig, ax = plt.subplots(figsize=(10, 4.5))
    ax.boxplot(data, showfliers=False)
    ax.set_xticks(range(1, len(keys) + 1),
                  [f"{p}\n{d}ms\n{l}%" for p, d, l in keys], fontsize=7)
    ax.set_ylabel("end-to-end latency (ms)")
    _watermark(ax, df)
    fig.tight_layout()
    fig.savefig(path, dpi=200)
    plt.close(fig)


def miss_heatmap(df, path):
    miss = df[df["metric"] == "app.deadline_miss"]
    losses = sorted(miss[LOSS].unique())
    fig, axes = plt.subplots(1, len(losses), figsize=(4.5 * len(losses), 3.5), squeeze=False)
    for ax, loss in zip(axes[0], losses):
        piv = (miss[miss[LOSS] == loss].groupby([PLACE, DELAY])["value"].mean()
               .unstack(DELAY))
        im = ax.imshow(piv.values, vmin=0, vmax=max(0.2, float(piv.values.max())), cmap="viridis")
        ax.set_xticks(range(len(piv.columns)), [f"{c}" for c in piv.columns])
        ax.set_yticks(range(len(piv.index)), list(piv.index))
        ax.set_xlabel("WAN delay (ms)")
        ax.set_title(f"loss {loss}%")
        for (i, j), v in __import__("numpy").ndenumerate(piv.values):
            ax.text(j, i, f"{v:.3f}", ha="center", va="center", color="w", fontsize=8)
        _watermark(ax, df)
    fig.colorbar(im, ax=axes[0].tolist(), label="deadline miss rate")
    fig.savefig(path, dpi=200, bbox_inches="tight")
    plt.close(fig)
