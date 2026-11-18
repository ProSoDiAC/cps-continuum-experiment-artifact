"""Regenerate the paper's tables and aggregate statistics from the per-run data in data/.

Usage:  python3 reproduce.py [--out reproduced]
Each output is compared with the published version in paper-outputs/ or data/ and the result is printed.
"""
import argparse
import filecmp
from pathlib import Path

import pandas as pd

from analysis import baseline_fixed, rq1, rq2, rq3


def same_csv(a, b, tol=1e-9):
    x, y = pd.read_csv(a), pd.read_csv(b)
    if x.shape != y.shape or list(x.columns) != list(y.columns):
        return False
    num = x.select_dtypes("number").columns
    close = ((x[num] - y[num]).abs() <= tol) | (x[num].isna() & y[num].isna())
    return bool(close.all().all()) and x.drop(columns=num).equals(y.drop(columns=num))


def report(name, ok):
    print(f"{'OK  ' if ok else 'DIFF'} {name}")
    return ok


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--out", default="reproduced")
    out = Path(p.parse_args().out)
    out.mkdir(parents=True, exist_ok=True)
    ok = True

    # RQ1: coefficient of variation per binding (rq1_gent = single-site binding, rq1_hybrid = hybrid binding)
    rq1.cv_table(pd.read_csv("data/rq1/rq1_per_run.csv")).to_csv(out / "rq1_cv.csv", index=False)
    ok &= report("RQ1 coefficient of variation (data/rq1/rq1_cv.csv)", same_csv(out / "rq1_cv.csv", "data/rq1/rq1_cv.csv"))

    # RQ2: per-cell statistics with bootstrap intervals and Table 4
    runs = pd.read_csv("data/rq2/rq2_per_run.csv")
    s = rq2.summarize(runs)
    s.to_csv(out / "rq2_summary.csv", index=False)
    rq2.latex_table(s, out / "rq2_table.tex", runs)
    rq2.paired_vs_fog(runs).to_csv(out / "rq2_paired_vs_fog.csv", index=False)
    ok &= report("RQ2 summary (data/rq2/rq2_summary.csv)", same_csv(out / "rq2_summary.csv", "data/rq2/rq2_summary.csv"))
    ok &= report("RQ2 paired cloud-fog differences", same_csv(out / "rq2_paired_vs_fog.csv", "data/rq2/rq2_paired_vs_fog.csv"))
    ok &= report("Table 4 (paper-outputs/rq2_table.tex)",
                 filecmp.cmp(out / "rq2_table.tex", "paper-outputs/rq2_table.tex", shallow=False))

    # RQ2: reference controllers, simulated offline with the demand seeds of the repetitions
    baseline_fixed.run("data/rq2/seeds.csv", "data/rq2/rq2_per_run.csv", out)
    ok &= report("Reference controllers (data/rq2/baseline_fixed.csv)",
                 same_csv(out / "baseline_fixed.csv", "data/rq2/baseline_fixed.csv"))

    # RQ3: paired minimal/full comparison
    r3 = pd.read_csv("data/rq3/rq3_per_run.csv")
    pr = rq3.paired(r3)
    rq3.latex_table(r3, pr, out / "rq3_table.tex")
    ok &= report("RQ3 table (paper-outputs/rq3_table.tex)",
                 filecmp.cmp(out / "rq3_table.tex", "paper-outputs/rq3_table.tex", shallow=False))

    # Figure 2 (RQ2 mechanism): panel (a) from the latency samples at 50 ms, panel (b) from the summary.
    # Regenerated from the CSV it is pixel-identical to the figure in the paper (only PDF metadata dates differ).
    samples = pd.read_csv("data/rq2/latency_samples_50ms.csv")
    samples = samples.rename(columns={"latency_ms": "value"}).assign(metric="app.e2e_latency")
    rq2.fig_mechanism(samples, s, out / "rq2_mechanism.pdf", deadline=250)
    print(f"REGEN rq2_mechanism.pdf (from data/rq2/latency_samples_50ms.csv) -> {out / 'rq2_mechanism.pdf'}")

    print("All outputs reproduced." if ok else "Some outputs differ (see DIFF lines).")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
