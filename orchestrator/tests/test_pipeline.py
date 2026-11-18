import filecmp
from pathlib import Path

import pytest
import yaml

from analysis.load_runs import load_runs
from orchestrator.contract import check_run_dir
from orchestrator.runner import DescriptorError, run_sweep
from orchestrator.sweep import expand
from descriptor.validate import load

EXAMPLE = Path(__file__).resolve().parents[2] / "descriptor" / "example.yaml"
quiet = lambda *_: None


def test_sweep_has_12_configs():
    cfgs = expand(load(EXAMPLE))
    assert len(cfgs) == 12
    assert len({c.config_id for c in cfgs}) == 12


def test_runs_follow_contract(tmp_path):
    res = run_sweep(EXAMPLE, "fake", tmp_path, seed=1, repetitions=1, log=quiet)
    assert res["done"] == 12 and res["failed"] == 0
    dirs = [d for d in tmp_path.iterdir() if d.is_dir()]
    assert len(dirs) == 12
    assert all(check_run_dir(d) == [] for d in dirs)
    assert not list(tmp_path.glob(".*.tmp"))


def test_resume_skips_complete_runs(tmp_path):
    run_sweep(EXAMPLE, "fake", tmp_path, seed=1, repetitions=1, log=quiet)
    res = run_sweep(EXAMPLE, "fake", tmp_path, seed=1, repetitions=1, log=quiet)
    assert res["done"] == 0 and res["skipped"] == 12


def test_same_seed_same_metrics(tmp_path):
    a, b, c = tmp_path / "a", tmp_path / "b", tmp_path / "c"
    for out, seed in ((a, 1), (b, 1), (c, 2)):
        run_sweep(EXAMPLE, "fake", out, seed=seed, repetitions=1, log=quiet)
    same = diff = 0
    for d in a.iterdir():
        same += filecmp.cmp(d / "metrics.csv", b / d.name / "metrics.csv", shallow=False)
        diff += not filecmp.cmp(d / "metrics.csv", c / d.name / "metrics.csv", shallow=False)
    assert same == 12 and diff == 12


def test_invalid_descriptor_creates_nothing(tmp_path):
    d = yaml.safe_load(EXAMPLE.read_text())
    d["application"]["components"][0]["placement"] = "hpc"
    bad = tmp_path / "bad.yaml"
    bad.write_text(yaml.safe_dump(d))
    out = tmp_path / "out"
    with pytest.raises(DescriptorError):
        run_sweep(bad, "fake", out, log=quiet)
    assert not out.exists()


def test_slices_backend_refuses_without_explicit_flag(tmp_path):
    """Safety: the real backend never starts by mistake (not even in tests)."""
    with pytest.raises(PermissionError):
        run_sweep(EXAMPLE, "slices", tmp_path / "o", log=quiet)
    assert not (tmp_path / "o").exists()


def test_analysis_excludes_simulated_by_default(tmp_path):
    run_sweep(EXAMPLE, "fake", tmp_path, seed=1, repetitions=1, log=quiet)
    assert load_runs(tmp_path).empty
    assert not load_runs(tmp_path, include_simulated=True).empty


def test_repetitions_are_interleaved_in_seeded_random_order(tmp_path):
    from orchestrator.backends.fake import FakeBackend
    order = []

    class Spy(FakeBackend):
        def apply_impairments(self, cfg):
            order.append(cfg.config_id)
            return super().apply_impairments(cfg)

    run_sweep(EXAMPLE, "fake", tmp_path, seed=3, repetitions=2, log=quiet, backend=Spy())
    n = len(order) // 2
    assert sorted(order[:n]) == sorted(order[n:]) and len(set(order[:n])) == n   # each rep covers all configs
    assert order[:n] != order[n:]                                                 # different order across reps


def test_plant_seed_is_shared_across_configs_but_not_across_repetitions(tmp_path):
    import json
    run_sweep(EXAMPLE, "fake", tmp_path, seed=5, repetitions=2, log=quiet)
    seeds = {}
    for d in tmp_path.iterdir():
        p = json.loads((d / "provenance.json").read_text())
        seeds.setdefault(p["repetition"], set()).add(p["seed"])
    assert all(len(v) == 1 for v in seeds.values())      # common random numbers across configurations
    assert seeds[0] != seeds[1]


def test_code_version_ignores_data_and_paper_changes():
    from orchestrator import provenance
    assert "paper" not in provenance.CODE_PATHS and "experiments" not in provenance.CODE_PATHS
