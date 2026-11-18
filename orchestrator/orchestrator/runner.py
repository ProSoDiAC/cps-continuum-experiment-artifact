"""Esecuzione dello sweep: descriptor -> cartelle di run secondo il contratto."""
import hashlib
import random
import shutil
from pathlib import Path

from descriptor.validate import load, validate

from . import collector, provenance
from .backends import get_backend
from .contract import check_run_dir
from .sweep import expand


class DescriptorError(Exception):
    pass


def derive_seed(seed, config_id, repetition):
    """Seed of the run. The traffic (plant) uses config_id="plant": same demand in all configurations
    of the same repetition (common random numbers), so the CPS KPIs are compared pairwise."""
    h = hashlib.sha256(f"{seed}/{config_id}/{repetition}".encode()).hexdigest()
    return int(h[:8], 16)


def run_sweep(descriptor_path, backend_name, out_dir, seed=0, repetitions=None, log=print,
              backend=None):
    desc = load(descriptor_path)
    errs = validate(desc)
    if errs:
        raise DescriptorError("invalid descriptor:\n  " + "\n  ".join(errs))
    backend = backend or get_backend(backend_name)
    backend._desc = desc
    reps = repetitions or desc["execution"]["repetitions"]
    out = Path(out_dir)
    exp_id = desc["experiment"]["id"]
    configs = expand(desc)

    out.mkdir(parents=True, exist_ok=True)
    inventory = backend.provision(desc)
    done = skipped = failed = 0
    try:
        for rep in range(reps):                  # repetitions in the outer loop, configurations in order
            order = list(configs)                # seeded random: infrastructure drift is not
            random.Random(derive_seed(seed, "order", rep)).shuffle(order)   # confounded with a configuration
            for cfg in order:
                run_id = f"{exp_id}-{cfg.config_id}-r{rep:02d}"
                final = out / run_id
                if final.is_dir() and not check_run_dir(final):
                    skipped += 1
                    continue
                tmp = out / f".{run_id}.tmp"
                shutil.rmtree(tmp, ignore_errors=True)
                tmp.mkdir()
                try:
                    log_cfg = backend.apply_impairments(cfg)
                    backend.deploy(desc, cfg)
                    run_seed = derive_seed(seed, "plant", rep)
                    samples, status = backend.run_workload(desc, cfg, run_seed)
                    collector.write_metrics(tmp / "metrics.csv", samples)
                    collector.write_log(tmp / "run.log", [
                        f"run {run_id} backend={backend.name} seed={run_seed} status={status}",
                        *log_cfg])
                    provenance.write(tmp / "provenance.json", provenance.build(
                        descriptor_path, backend, inventory, log_cfg, cfg, rep, run_seed, status, desc))
                    shutil.rmtree(final, ignore_errors=True)
                    tmp.rename(final)
                    done += 1
                except Exception as e:                      # a failed run does not stop the sweep
                    shutil.rmtree(tmp, ignore_errors=True)
                    failed += 1
                    log(f"FALLITA {run_id}: {e}")
    finally:
        backend.teardown()
    log(f"configurations={len(configs)} new runs={done} skipped={skipped} failed={failed}")
    return {"configs": len(configs), "done": done, "skipped": skipped, "failed": failed}
