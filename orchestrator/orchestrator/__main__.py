import argparse
import sys

from .runner import DescriptorError, run_sweep


def main():
    p = argparse.ArgumentParser(prog="orchestrator")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="runs the sweep of a descriptor")
    r.add_argument("descriptor")
    r.add_argument("--backend", default="fake")
    r.add_argument("--out", required=True)
    r.add_argument("--seed", type=int, default=0)
    r.add_argument("--repetitions", type=int)
    r.add_argument("--duration-s", type=float, help="local backend only: measured duration in seconds")
    r.add_argument("--warmup-s", type=float, help="local backend only: warm-up in seconds")
    r.add_argument("--real", action="store_true", help="slices backend only: allows creating real resources")
    r.add_argument("--dry-run", action="store_true", help="slices backend only: records the commands without running them")
    r.add_argument("--experiment", help="slices backend only: name of the SLICES experiment (default: derived from the descriptor)")
    r.add_argument("--lease", default="3h", help="slices backend only: lifetime of the experiment and VMs (e.g. 8h)")
    a = p.parse_args()
    try:
        backend = None
        if a.backend == "local":
            from .backends import get_backend
            backend = get_backend("local", duration_s=a.duration_s, warmup_s=a.warmup_s)
        if a.dry_run or a.real:
            from .backends import get_backend
            backend = get_backend(a.backend, **({"dry_run": True} if a.dry_run else {"real": True}),
                                  **({"duration": a.lease, "experiment": a.experiment} if a.backend == "slices" else {}))
        res = run_sweep(a.descriptor, a.backend, a.out, a.seed, a.repetitions, backend=backend)
    except (DescriptorError, ValueError, NotImplementedError, PermissionError) as e:
        print(f"ERROR {e}", file=sys.stderr)
        return 2
    return 1 if res["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
