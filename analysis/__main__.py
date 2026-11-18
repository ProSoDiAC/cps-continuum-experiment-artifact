import argparse
import sys
from pathlib import Path

from .load_runs import load_runs
from .plots import latency_boxplot, miss_heatmap


def main():
    p = argparse.ArgumentParser(prog="analysis")
    p.add_argument("--results", required=True)
    p.add_argument("--out", default="analysis/out")
    p.add_argument("--include-simulated", action="store_true",
                   help="development only: simulated data must never go into the paper")
    p.add_argument("--rq2", action="store_true", help="final RQ2 analysis (per-run statistics, CIs, table, PDF figures)")
    p.add_argument("--rq3", action="store_true", help="RQ3 analysis (cost of observability, minimal vs full)")
    p.add_argument("--duration-s", type=float, default=300, help="--rq3 only: measured duration of a run (s)")
    p.add_argument("--deadline-ms", type=float, default=250)
    a = p.parse_args()
    df = load_runs(a.results, a.include_simulated)
    if df.empty:
        print("No runs to analyse (simulated runs are excluded without --include-simulated).",
              file=sys.stderr)
        return 1
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    if a.rq3:
        from .rq3 import run as run3
        runs, pr = run3(df, out, a.duration_s)
        print(f"{len(runs)} run -> {out}")
        return 0
    if a.rq2:
        from .rq2 import run
        s = run(df, out, a.deadline_ms)
        print(f"{df['run_id'].nunique()} run, {len(s)} celle (n={s.n.min()}..{s.n.max()}) -> {out}")
        return 0
    latency_boxplot(df, out / "latency_boxplot.png")
    miss_heatmap(df, out / "miss_heatmap.png")
    print(f"{df['run_id'].nunique()} run -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
