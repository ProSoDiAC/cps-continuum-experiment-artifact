# Reproducible Smart-City CPS Experimentation over the Cloud Continuum — Artifact

This repository is the artifact of the paper

> F. O. Mirto, G. Tricomi, L. D'Agati, A. Puliafito, F. Longo, G. Merlino.
> *Reproducible Smart-City CPS Experimentation over the Cloud Continuum: An Experiment Descriptor and a Traffic-Control Case Study.*
> Springer CCIS (to appear).

It contains the experiment descriptor model (schema and validator), the thin orchestrator that executes descriptors, the
traffic-control workload, the descriptors of the experiments reported in the paper, the per-run data of those
experiments and the analysis code that turns the data into the paper's tables.

## Quick start: reproduce the paper's tables

Requires Python ≥ 3.10.

```bash
pip install -r requirements.txt
python3 reproduce.py            # writes to reproduced/ and compares with the published outputs
```

Expected output:

```
OK   RQ1 coefficient of variation (data/rq1/rq1_cv.csv)
OK   RQ2 summary (data/rq2/rq2_summary.csv)
OK   RQ2 paired cloud-fog differences
OK   Table 4 (paper-outputs/rq2_table.tex)
OK   Reference controllers (data/rq2/baseline_fixed.csv)
OK   RQ3 table (paper-outputs/rq3_table.tex)
REGEN rq2_mechanism.pdf (from data/rq2/latency_samples_50ms.csv)
All outputs reproduced.
```

The reference controllers are re-simulated from the demand seeds of the repetitions.

## Repository layout

| Path | Content |
| --- | --- |
| `descriptor/` | JSON schema of the experiment descriptor, validator, example descriptors |
| `orchestrator/` | Thin orchestrator: sweep expansion, backends, WAN impairment (`tc netem`), observability, provenance; unit tests |
| `workload/` | Traffic-control pipeline (detector gateway, state estimator, signal controller, actuator), closed-loop intersection model, per-node observability agent |
| `experiments/descriptors/` | Descriptors of the experiments in the paper (RQ1 single-site and hybrid bindings, RQ2, RQ3) |
| `scripts/run_all.sh` | Script used to run all experiments on the real infrastructure |
| `analysis/` | Analysis code (per-run statistics, bootstrap intervals, tables, figures, offline reference controllers) |
| `data/` | Per-run and aggregate data of RQ1–RQ3, and the demand seed of each repetition |
| `paper-outputs/` | Outputs produced for the paper: Table 4 (`rq2_table.tex`), the RQ2 figure (`rq2_mechanism.pdf`) and the RQ3 table, whose values are reported in the text |
| `reproduce.py` | Regenerates the tables from `data/` and checks them against `paper-outputs/` |

## Data

All data are per run (one row per run) or aggregated over runs. The unit of analysis is the run: samples within a run
are autocorrelated, so intervals are 95% bootstrap percentile intervals over the run means.

| File | Content |
| --- | --- |
| `data/rq1/rq1_per_run.csv` | RQ1: 10 repetitions of one configuration (estimator on the cloud, 50 ms per-direction delay, 1% loss) in two bindings: `rq1_gent` = single-site binding, `rq1_hybrid` = hybrid binding (edge in a second domain) |
| `data/rq1/rq1_cv.csv` | RQ1: mean, standard deviation and coefficient of variation per binding and metric |
| `data/rq2/rq2_per_run.csv` | RQ2: 60 runs (2 placements × 3 delays × 2 loss levels × 5 repetitions) |
| `data/rq2/rq2_summary.csv` | RQ2: per-configuration means with 95% bootstrap intervals (`_lo`, `_hi`) |
| `data/rq2/rq2_paired_vs_fog.csv` | RQ2: cloud − fog differences paired by repetition |
| `data/rq2/latency_samples_50ms.csv` | RQ2: individual end-to-end latency samples (ms) at 50 ms of delay, for Figure 2(a) |
| `data/rq2/baseline_fixed.csv` | RQ2: offline reference controllers (adaptive without latency, fixed-time plans, controller that never acts) |
| `data/rq3/rq3_per_run.csv`, `rq3_paired.csv` | RQ3: `minimal` vs `full` observability level, three paired repetitions |
| `data/rq*/seeds.csv` | Demand seed of each repetition (the seed depends only on the repetition, so configurations are paired) |

Per-run columns:

| Column | Meaning |
| --- | --- |
| `run_id` | `<experiment id>-<configuration id>-r<repetition>`; the configuration id is derived from the factor values |
| `placement.state-estimator`, `wan.delay_ms`, `wan.loss_pct` | Factor values: estimator placement, per-direction WAN delay (ms), per-direction WAN loss (%) |
| `lat_median`, `lat_p99` | Median and 99th percentile of the end-to-end control-loop latency (ms), measured on the edge node with a single clock |
| `miss_rate` | Fraction of control decisions that missed the 250 ms deadline (a late decision is not applied) |
| `wait_mean`, `queue_mean` | Mean waiting time (s) and queue length (vehicles) of the simulated intersection |
| `rtt` | WAN round-trip time measured at the start and end of the run (ms) |
| `gateway_cpu`, `agent_cpu_pct_per_node`, `probe_Bps_per_node`, `completeness` | RQ3: CPU of the gateway process (%), CPU of the observability agent (% of a core), probe traffic (B/s), fraction of expected samples received |

**Not included.** The raw time series, provenance records and run logs are not published because they contain
identifiers of the infrastructure (addresses and host names). Instead, `data/rq2/latency_samples_50ms.csv` contains the
individual end-to-end latency samples of the 50 ms delay (6 020 samples, all placements and loss levels), which are the
only raw data needed for panel (a) of Figure 2. With them, `reproduce.py` regenerates the complete figure.

## Running experiments

Tests (no infrastructure needed):

```bash
PYTHONPATH=.:orchestrator python3 -m pytest -q orchestrator/tests
```

Validate a descriptor:

```bash
python3 descriptor/validate.py experiments/descriptors/rq2.yaml
```

Run a sweep with the synthetic backend (seeded model, no infrastructure; every run is flagged `simulated: true` in its
provenance and excluded from analysis unless `--include-simulated` is given; **never use these data as results**):

```bash
PYTHONPATH=.:orchestrator python3 -m orchestrator run experiments/descriptors/rq2.yaml --backend fake --out experiments/sim --seed 1
```

Other backends:

- `local`: runs the workload in Linux network namespaces with real `tc netem` (requires root).
- `slices`: provisions virtual machines on SLICES and runs the workload there. It requires a SLICES account and the
  SLICES command-line client; resources are created only with `--real` (use `--dry-run` to log the commands without
  executing them). For the hybrid binding, the edge node is reached over SSH: replace the placeholder `edge-host` in
  `experiments/descriptors/rq1_hybrid.yaml` and in `scripts/run_all.sh` with your own host.

Each run produces `metrics.csv` (long format: `t_s, metric, value, tier`), `run.log` and `provenance.json` (descriptor
hash, node inventory, applied impairments, seed, code version, backend, recorded conditions). Completed runs are
skipped on re-execution, so sweeps are resumable. To analyse a results directory:

```bash
python3 -m analysis --results experiments/<dir> --out analysis/out --rq2
```

## Notes

- The experiments were run with orchestrator version `3af5030`. The descriptor of the hybrid binding differs from
  the one used for the runs only in the edge host name (replaced by `edge-host`), so its hash differs from the one
  recorded in the original provenance.
- The `workload_profile: sumo-scenario-v1` field of the descriptors is a leftover of an earlier design: the
  intersection is the Python model in `workload/traffic.py`, not SUMO.

## License

Code: Apache License 2.0 (`LICENSE`). Data, descriptors and paper outputs (`data/`, `experiments/descriptors/`,
`paper-outputs/`): Creative Commons Attribution 4.0 International (`LICENSE-DATA`).

## Citation

See `CITATION.cff`.
