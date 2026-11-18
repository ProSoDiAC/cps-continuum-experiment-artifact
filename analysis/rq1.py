"""RQ1 (repeatability): coefficient of variation across repetitions, per binding and metric.

Input: per-run CSV (binding, run_id, repetition, per-run metrics). Output: rq1_cv.csv.
Uso: python3 -m analysis.rq1 data/rq1/rq1_per_run.csv out/
"""
import sys
from pathlib import Path

import pandas as pd

METRICS = ("lat_median", "lat_p99", "miss_rate", "wait_mean", "queue_mean", "rtt")


def cv_table(runs):
    rows = []
    for binding, g in runs.groupby("binding"):
        for m in METRICS:
            x = g[m]
            rows.append({"binding": binding, "metric": m, "n": len(x), "mean": x.mean(), "sd": x.std(ddof=1),
                         "cv": x.std(ddof=1) / x.mean(), "min": x.min(), "max": x.max()})
    return pd.DataFrame(rows)


if __name__ == "__main__":
    runs = pd.read_csv(sys.argv[1])
    out = Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    t = cv_table(runs)
    t.to_csv(out / "rq1_cv.csv", index=False)
    print(t.to_string(index=False))
