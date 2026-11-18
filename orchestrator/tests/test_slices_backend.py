import json
from pathlib import Path

import pytest

from descriptor.validate import load
from orchestrator.backends import get_backend
from orchestrator.slices_adapter import SlicesBackend
from orchestrator.sweep import expand

D = Path(__file__).resolve().parents[2] / "descriptor"
HYBRID, GENT = load(D / "example.yaml"), load(D / "example_gent_only.yaml")


AGENT_ROWS = [{"t_epoch": 1000.5, "cpu_pct": 1.0, "mem_mb": 100.0, "rx_Bps": 10.0, "tx_Bps": 10.0, "rtt_ms": {}},
              {"t_epoch": 1003.0, "cpu_pct": 5.0, "mem_mb": 200.0, "rx_Bps": 1000.0, "tx_Bps": 2000.0,
               "rtt_ms": {"10.10.0.3": 51.0, "10.10.0.9": 7.0}},
              {"t_epoch": 1004.0, "cpu_pct": 7.0, "mem_mb": 210.0, "rx_Bps": 1100.0, "tx_Bps": 2100.0,
               "rtt_ms": {"10.10.0.3": None}},
              {"summary": True, "cpu_s": 0.4, "probes": 12, "samples": 3}]

ROWS = [{"seq": i, "t_s": float(i + 1), "t_epoch": 1000.0 + i + 1, "act_ms": 111.0, "rtt_ms": 112.0, "missed": False,
         "wait_s": 2.0, "queue": 3, "cpu_pct": 1.0} for i in range(6)]


class RecShell:
    """Scripted shell: records the commands and simulates the node responses."""

    def __init__(self, names):
        self.cmds, self.userdata = [], []
        self.names = names

    def run(self, cmd, env=None, timeout=None):
        self.cmds.append(list(cmd))
        line = " ".join(cmd)
        if "--user-data" in cmd:
            self.userdata.append(Path(cmd[cmd.index("--user-data") + 1]).read_text())
        if cmd[:3] == ["slices", "bi", "list-resources"]:
            return json.dumps([{"friendly_name": n, "id": f"r_{n}", "status": "up",
                                "private_ipv4": f"10.10.0.{i + 1}", "expires_at": "x"}
                               for i, n in enumerate(self.names)])
        if "tailscale ip -4" in line:
            return "100.64.1.%d\n" % (len(self.cmds) % 200 + 1)
        if "ping -c " in line:
            return "10 packets transmitted, 10 received, 0% packet loss\nrtt min/avg/max/mdev = 50.1/52.0/58.0/1.5 ms"
        if "chronyc tracking" in line:
            return "System time     : 0.000001234 seconds slow of NTP time"
        if "tailscale ping" in line:
            return "pong from x (100.64.1.9) via 193.1.2.3:41641 in 50ms"
        if cmd[:3] == ["slices", "bi", "scp"] and ":" in cmd[-2] and not cmd[-2].startswith("/"):
            rows = AGENT_ROWS if "fom2-agent" in cmd[-2] else ROWS
            Path(cmd[-1]).write_text("\n".join(json.dumps(r) for r in rows))   # fetch from node
        if cmd[0] == "scp" and ":" in cmd[-2] and not cmd[-2].startswith("/"):
            Path(cmd[-1]).write_text("\n".join(json.dumps(r) for r in ROWS))   # fetch from remote node
        if "components edge" in line and "--out " in line and cmd[0] != "ssh":
            out = line.split("--out ")[1].split()[0].rstrip("'")
            Path(out).write_text("\n".join(json.dumps(r) for r in ROWS))      # edge locale
        return ""


def flat(shell):
    return [" ".join(c) for c in shell.cmds]


def test_refuses_without_flag():
    with pytest.raises(PermissionError):
        get_backend("slices")


def test_dry_run_hybrid_runs_nothing_and_hides_secrets():
    b = SlicesBackend(dry_run=True)
    inv = b.provision(HYBRID)
    assert [n["name"] for n in inv] == ["edge-me-0", "fog-x-0", "cloud-x-0"]
    creates = [c for c in b.commands if " create " in c and "--infra" in c]
    assert len(creates) == 2                       # the manual node is not created
    assert not any("tskey" in c for c in b.commands)
    assert any("--authkey=REDACTED" in c or "<cloud-init>" in c for c in b.commands)


def test_hybrid_userdata_has_tailscale_and_is_deleted(tmp_path):
    keyf = tmp_path / "key"
    keyf.write_text("tskey-auth-TEST\n")
    sh = RecShell(["fog-x-0", "cloud-x-0"])
    b = SlicesBackend(shell=sh, ts_key_file=str(keyf), experiment="e")
    b.provision(HYBRID)
    assert all("tailscale up --authkey=tskey-auth-TEST" in u and "tag:fom2" in u for u in sh.userdata)
    assert all(not Path(c[c.index("--user-data") + 1]).exists() for c in sh.cmds if "--user-data" in c)
    assert not any("tskey" in x for x in flat(sh))   # never on the command line


def test_provision_adds_known_hosts_after_create(tmp_path):
    sh = RecShell(["edge-me-0", "fog-x-0", "cloud-x-0"])
    SlicesBackend(shell=sh, experiment="e").provision(GENT)
    cmds = flat(sh)
    assert cmds.index("slices bi known-hosts add") > max(i for i, c in enumerate(cmds) if " create " in c)
    assert sum("cloud-init status --wait" in c for c in cmds) == 3


def test_gent_only_has_no_tailscale_and_never_reads_key(tmp_path):
    sh = RecShell(["edge-me-0", "fog-x-0", "cloud-x-0"])
    b = SlicesBackend(shell=sh, ts_key_file=str(tmp_path / "missing"), experiment="e")
    b.provision(GENT)
    assert len(sh.userdata) == 3 and all("tailscale" not in u for u in sh.userdata)
    assert b.inventory[1]["private_ipv4"] == "10.10.0.2"


def test_wan_semantics_every_pair_across_the_wan_is_delayed_gent_only():
    names = ["edge-me-0", "fog-x-0", "cloud-x-0"]
    sh = RecShell(names)
    b = SlicesBackend(shell=sh, ts_key_file=__file__, experiment="e")
    b.provision(GENT)
    b._desc = GENT
    log = b.apply_impairments(expand(GENT)[0])
    scope = b.conditions["impairment_scope"]
    # edge e fog (stessa componente via link lan) vedono il cloud; il cloud vede edge e fog
    assert scope == {"edge-me-0": ["cloud-x-0"], "fog-x-0": ["cloud-x-0"],
                     "cloud-x-0": ["edge-me-0", "fog-x-0"]}
    assert sum("tc filter add" in l and l.startswith("[cloud-x-0") for l in log) == 2
    assert sum("tc filter add" in l and l.startswith("[edge-me-0") for l in log) == 1
    assert all("ip route show default" in l for l in log if "qdisc replace" in l)


def test_hybrid_shapes_the_local_edge_and_removes_it_at_teardown():
    sh = RecShell(["fog-x-0", "cloud-x-0"])
    b = SlicesBackend(shell=sh, ts_key_file=__file__, experiment="e")
    b.provision(HYBRID)
    b._desc = HYBRID
    log = b.apply_impairments(expand(HYBRID)[0])
    scope = b.conditions["impairment_scope"]
    assert scope["edge-me-0"] == ["cloud-x-0"]            # the manual edge is modelled too, only towards cloud
    assert scope["cloud-x-0"] == ["edge-me-0", "fog-x-0"]
    assert all("tailscale0" in l for l in log if "qdisc replace" in l)
    assert sum("tc filter add" in l and l.startswith("[edge-me-0") for l in log) == 1
    assert "edge_fog_rtt_baseline_ms" in b.conditions
    sh.cmds.clear()
    b.teardown()
    assert any("tc qdisc del dev tailscale0 root" in c for c in flat(sh))   # local netem removed


def test_remote_manual_node_goes_through_ssh_with_limits(tmp_path):
    import copy
    desc = copy.deepcopy(HYBRID)
    desc["infrastructure"]["sites"][0]["host"] = "edge-host"
    sh, b, cfg = _prepared(desc, ["fog-x-0", "cloud-x-0"], tmp_path)
    b.warmup_s, b.duration_s = 1, 4
    sh.cmds.clear()
    b.apply_impairments(cfg)
    assert any(c[:1] == ["ssh"] and "edge-host" in c and "tc filter add" in " ".join(c) for c in sh.cmds)
    samples, _ = b.run_workload(desc, cfg, 3)
    run = [c for c in flat(sh) if "components edge" in c][0]
    assert run.startswith("ssh") and "edge-host" in run
    assert "sudo systemd-run --scope --quiet --uid=$(id -u) --gid=$(id -g) -p CPUQuota=400% -p MemoryMax=4G -p MemorySwapMax=0" in run
    assert any(c[0] == "scp" and c[-2].startswith("edge-host:") for c in sh.cmds)       # righe edge scaricate


def test_manual_node_without_passwordless_sudo_fails_before_creating_vms():
    class NoSudo(RecShell):
        def run(self, cmd, env=None, timeout=None):
            if "sudo -n" in " ".join(cmd):
                raise RuntimeError("sudo: a password is required")
            return super().run(cmd, env, timeout)

    sh = NoSudo(["fog-x-0", "cloud-x-0"])
    b = SlicesBackend(shell=sh, ts_key_file=__file__, experiment="e")
    with pytest.raises(RuntimeError, match="sudo without password"):
        b.provision(HYBRID)
    assert not any(c[:3] == ["slices", "experiment", "create"] or "create" in c for c in sh.cmds)


def test_teardown_destroys_only_created_nodes():
    sh = RecShell(["fog-x-0", "cloud-x-0"])
    b = SlicesBackend(shell=sh, ts_key_file=__file__, experiment="e")
    b.provision(HYBRID)
    sh.cmds.clear()
    b.teardown()
    destroyed = [c[-1] for c in sh.cmds if c[:3] == ["slices", "bi", "destroy"]]
    assert sorted(destroyed) == ["cloud-x-0", "fog-x-0"]


def _prepared(desc, names, tmp_path):
    keyf = tmp_path / "key"
    keyf.write_text("tskey-auth-TEST\n")
    sh = RecShell(names)
    b = SlicesBackend(shell=sh, ts_key_file=str(keyf), experiment="e")
    b.provision(desc)
    b._desc = desc
    cfg = expand(desc)[0]
    b.apply_impairments(cfg)
    b.deploy(desc, cfg)
    return sh, b, cfg


def test_baseline_is_measured_before_impairments(tmp_path):
    sh, b, cfg = _prepared(GENT, ["edge-me-0", "fog-x-0", "cloud-x-0"], tmp_path)
    c = b.conditions
    assert c["wan_rtt_baseline_ms"] == {"avg": 52.0, "mdev": 1.5}
    assert c["clock_offset_s"]["fog-x-0"] == pytest.approx(-1.234e-6)
    cmds = flat(sh)
    i_ping = next(i for i, c in enumerate(cmds) if "ping -c 20" in c)
    i_netem = next(i for i, c in enumerate(cmds) if "netem" in c)
    assert i_ping < i_netem


def test_run_workload_gent_only_uses_private_ips_and_parses_rows(tmp_path):
    sh, b, cfg = _prepared(GENT, ["edge-me-0", "fog-x-0", "cloud-x-0"], tmp_path)
    b.warmup_s, b.duration_s = 2, 4
    samples, status = b.run_workload(GENT, cfg, 7)
    assert status == "completed"
    assert {m for _, m, _, _ in samples} >= {"app.e2e_latency", "app.deadline_miss", "net.rtt"}
    assert sorted(t for t, m, _, _ in samples if m == "net.rtt") == [0.0, 4.0]      # before and after the workload
    assert min(t for t, *_ in samples) == 0.0 and all(t >= 0 for t, *_ in samples)   # warm-up scartato
    cmds = flat(sh)
    assert any("components controller --port 9102 --actuator 10.10.0.1:9103" in c for c in cmds)
    assert any("components edge" in c and "--duration 6" in c and "--seed 7" in c for c in cmds)
    assert any("components estimator" in c and "--bind 10.10.0." in c for c in cmds)       # listens only on its own IP
    assert sum("pkill -f 'workload[.](components|agent)'" in c for c in cmds) >= 2        # stop before and after


def test_baseline_tolerates_nodes_without_chrony(tmp_path):
    class NoChrony(RecShell):
        def run(self, cmd, env=None, timeout=None):
            line = " ".join(cmd)
            if "chronyc tracking" in line and "edge-me-0" in line:
                return ""                              # grep pipeline without match, as on a node without chrony
            if "timedatectl" in line:
                return "yes\n"
            return super().run(cmd, env, timeout)

    sh = NoChrony(["edge-me-0", "fog-x-0", "cloud-x-0"])
    b = SlicesBackend(shell=sh, experiment="e")
    b.provision(GENT)
    b._desc = GENT
    b.apply_impairments(expand(GENT)[0])
    assert b.conditions["clock_offset_s"]["edge-me-0"] is None
    assert b.conditions["clock_offset_s"]["fog-x-0"] is not None
    assert b.conditions["ntp_synchronized_no_chrony"] == {"edge-me-0": "yes"}


def test_blocked_link_stops_the_run_with_the_exact_direction(tmp_path):
    sh, b, cfg = _prepared(GENT, ["edge-me-0", "fog-x-0", "cloud-x-0"], tmp_path)
    b.warmup_s, b.duration_s = 1, 2
    orig = sh.run

    def blocked(cmd, env=None, timeout=None):
        if "create_connection" in " ".join(cmd) and "fog-x-0" in " ".join(cmd):
            raise RuntimeError("timed out")
        return orig(cmd, env, timeout)

    sh.run = blocked
    with pytest.raises(RuntimeError, match=r"fog \(fog-x-0\) -> fog \(fog-x-0\) 10\.10\.0\.2:9102 not reachable"):
        b.run_workload(GENT, cfg, 1)


def test_connectivity_is_checked_once_per_placement(tmp_path):
    sh, b, cfg = _prepared(GENT, ["edge-me-0", "fog-x-0", "cloud-x-0"], tmp_path)
    b.warmup_s, b.duration_s = 1, 2
    b.run_workload(GENT, cfg, 1)
    n = sum("create_connection" in c for c in flat(sh))
    b.run_workload(GENT, cfg, 2)
    assert n == 3 and sum("create_connection" in c for c in flat(sh)) == 3


def test_background_load_parses_noisy_ssh_output_and_keeps_raw_on_failure():
    class Noisy(RecShell):
        def run(self, cmd, env=None, timeout=None):
            line = " ".join(cmd)
            if "cat /proc/loadavg" in line:
                if "fog-x-0" in line:
                    return "Warning: Permanently added 10.10.2.3 (ED25519) to known hosts.\n0.30 0.20 0.10 1/200 999\n"
                if "cloud-x-0" in line:
                    return "garbage\n"
                return "1.00 0.50 0.25 2/300 1234\n"
            return super().run(cmd, env, timeout)

    sh = Noisy(["edge-me-0", "fog-x-0", "cloud-x-0"])
    b = SlicesBackend(shell=sh, experiment="e")
    b.provision(GENT)
    b._desc = GENT
    b.apply_impairments(expand(GENT)[0])
    bl = b.conditions["background_load"]
    assert bl["fog-x-0"] == [0.3, 0.2, 0.1] and bl["edge-me-0"] == [1.0, 0.5, 0.25]
    assert bl["cloud-x-0"] is None and "garbage" in b.conditions["background_load_raw"]["cloud-x-0"]


def test_network_baseline_warms_up_before_measuring():
    sh = RecShell(["edge-me-0", "fog-x-0", "cloud-x-0"])
    b = SlicesBackend(shell=sh, experiment="e")
    b.provision(GENT)
    b._desc = GENT
    sh.cmds.clear()
    b.apply_impairments(expand(GENT)[0])
    pings = [c for c in flat(sh) if "ping -c" in c]
    assert "ping -c 5" in pings[0] and any("ping -c 20" in c for c in pings)


def test_shell_error_shows_the_remote_command():
    from orchestrator.slices_adapter import Shell
    with pytest.raises(RuntimeError, match="exit 3"):
        Shell().run(["bash", "-c", "a", "b", "c", "d", "e", "exit 3"])


def test_baseline_records_background_load_and_image(tmp_path):
    class Load(RecShell):
        def run(self, cmd, env=None, timeout=None):
            if "/proc/loadavg" in " ".join(cmd):
                return "0.10 0.05 0.01\n"
            return super().run(cmd, env, timeout)

    sh = Load(["edge-me-0", "fog-x-0", "cloud-x-0"])
    b = SlicesBackend(shell=sh, experiment="e")
    inv = b.provision(GENT)
    b._desc = GENT
    b.apply_impairments(expand(GENT)[0])
    assert b.conditions["background_load"]["fog-x-0"] == [0.1, 0.05, 0.01]
    assert all(n["image"] for n in inv)


def test_run_workload_hybrid_runs_edge_locally_on_tailscale_ips(tmp_path):
    sh, b, cfg = _prepared(HYBRID, ["fog-x-0", "cloud-x-0"], tmp_path)
    b.warmup_s, b.duration_s = 1, 4
    samples, status = b.run_workload(HYBRID, cfg, 3)
    assert status == "completed" and samples
    edge_cmd = next(c for c in flat(sh) if "components edge" in c)
    assert edge_cmd.startswith("bash -c") and "100.64.1." in edge_cmd           # locale, IP Tailscale
    assert not any("tskey" in c for c in flat(sh))


def test_dry_run_full_cycle_records_only_commands():
    b = SlicesBackend(dry_run=True)
    b.provision(HYBRID)
    b._desc = HYBRID
    cfg = expand(HYBRID)[0]
    b.apply_impairments(cfg)
    b.deploy(HYBRID, cfg)
    samples, status = b.run_workload(HYBRID, cfg, 1)
    assert (samples, status) == ([], "dry-run")
    assert any("components edge" in c for c in b.commands)


def test_slices_adapter_importable_standalone():
    import subprocess, sys
    r = subprocess.run([sys.executable, "-I", "-c",
                        "import sys; sys.path[:0]=['.', 'orchestrator']; "
                        "from orchestrator.slices_adapter import SlicesBackend"],
                       capture_output=True, text=True, cwd=Path(__file__).resolve().parents[2])
    assert r.returncode == 0, r.stderr


def test_existing_experiment_is_reused():
    class Exists(RecShell):
        def run(self, cmd, env=None, timeout=None):
            if cmd[:3] == ["slices", "experiment", "create"]:
                raise RuntimeError("Bad Request: An active experiment with this name already exists: exp_x")
            return super().run(cmd, env, timeout)

    sh = Exists(["edge-me-0", "fog-x-0", "cloud-x-0"])
    assert len(SlicesBackend(shell=sh, experiment="e").provision(GENT)) == 3


def test_existing_vm_is_reused_and_torn_down():
    class Exists(RecShell):
        def run(self, cmd, env=None, timeout=None):
            if cmd[:3] == ["slices", "bi", "--infra"] and "edge-me-0" in cmd:
                raise RuntimeError("Conflict: edge-me-0 already exists in experiment e")
            return super().run(cmd, env, timeout)

    sh = Exists(["edge-me-0", "fog-x-0", "cloud-x-0"])
    b = SlicesBackend(shell=sh, experiment="e")
    assert len(b.provision(GENT)) == 3
    b.teardown()
    assert any("destroy" in c and "edge-me-0" in c for c in flat(sh))


def test_wait_ready_retries_until_ssh_answers():
    class Flaky(RecShell):
        fails = 3

        def run(self, cmd, env=None, timeout=None):
            if cmd[:3] == ["slices", "bi", "ssh"] and cmd[-1] == "true" and Flaky.fails:
                Flaky.fails -= 1
                raise RuntimeError("Host key verification failed.")
            return super().run(cmd, env, timeout)

    sh = Flaky(["edge-me-0", "fog-x-0", "cloud-x-0"])
    b = SlicesBackend(shell=sh, experiment="e")
    b._sleep = lambda s: None
    b.provision(GENT)
    assert sum(c == "slices bi known-hosts add" for c in flat(sh)) >= 4    # re-run on every attempt


def test_wait_ready_gives_up_eventually():
    class Dead(RecShell):
        def run(self, cmd, env=None, timeout=None):
            if cmd[:3] == ["slices", "bi", "ssh"]:
                raise RuntimeError("Host key verification failed.")
            return super().run(cmd, env, timeout)

    b = SlicesBackend(shell=Dead(["edge-me-0", "fog-x-0", "cloud-x-0"]), experiment="e")
    b._sleep = lambda s: None
    with pytest.raises(RuntimeError):
        b.provision(GENT)


def test_full_observability_starts_agents_and_aligns_their_samples(tmp_path):
    import copy
    desc = copy.deepcopy(GENT)
    desc["observability"]["level"] = ["minimal", "full"]
    sh, b, _ = _prepared(desc, ["edge-me-0", "fog-x-0", "cloud-x-0"], tmp_path)
    b.warmup_s, b.duration_s = 2, 4
    cfgs = expand(desc)
    assert {c.obs for c in cfgs} == {"minimal", "full"}
    full = next(c for c in cfgs if c.obs == "full")
    minimal = next(c for c in cfgs if c.obs == "minimal" and c.placement == full.placement)
    sh.cmds.clear()
    m_samples, _ = b.run_workload(desc, minimal, 7)
    assert not any("workload.agent" in c for c in flat(sh))                 # minimal: no agent
    assert not any(m.startswith(("infra.", "obs.")) for _, m, _, _ in m_samples)
    sh.cmds.clear()
    samples, _ = b.run_workload(desc, full, 7)
    starts = [c for c in flat(sh) if "workload.agent" in c and "--out" in c]
    assert len(starts) == 3 and all("--interval 1" in c and "--peers" in c for c in starts)
    by = {m: [(t, v, tier) for t, mm, v, tier in samples if mm == m] for m in {m for _, m, _, _ in samples}}
    assert sorted(t for t, _, _ in by["infra.cpu"]) == [1.0, 1.0, 1.0, 2.0, 2.0, 2.0]   # row at t<0 discarded, 3 nodes
    assert {tier for _, _, tier in by["infra.cpu"]} == {"edge", "fog", "cloud"}
    assert any(m.startswith("net.rtt_probe.") for m in by)                  # probes towards a known peer, not towards the unknown one
    assert all(v == 0.4 for _, v, _ in by["obs.agent_cpu_s"]) and len(by["obs.agent_probes"]) == 3
    assert "app.gateway_cpu" in by and "infra.cpu" in by
