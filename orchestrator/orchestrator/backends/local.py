"""Local backend: three network namespaces (edge, fog, cloud) with real components and real netem.

edge --(lan)-- fog --(wan, netem simmetrico)-- cloud
The fog acts as the router. No host interface is touched. Single domain ("local-lab"),
therefore never federated: it is used to validate the workload before using SLICES.
"""
import json
import os
import subprocess
import time
from pathlib import Path

from .base import Backend
from ..edge_rows import rows_to_samples
from ..netem import netem_commands, netns_available
from workload.components import COMPUTE_MS

REPO = Path(__file__).resolve().parents[3]
PORTS = {"estimator": 9101, "controller": 9102, "edge": 9103}
# address through which tier `src` reaches tier `dst`
ADDR = {("edge", "edge"): "10.98.1.1", ("edge", "fog"): "10.98.1.2", ("edge", "cloud"): "10.98.2.2",
        ("fog", "edge"): "10.98.1.1", ("fog", "fog"): "10.98.1.2", ("fog", "cloud"): "10.98.2.2",
        ("cloud", "edge"): "10.98.1.1", ("cloud", "fog"): "10.98.2.1", ("cloud", "cloud"): "10.98.2.2"}


def _sh(cmd, check=True):
    return subprocess.run(cmd, shell=True, check=check, capture_output=True, text=True)


class LocalBackend(Backend):
    name = "local"
    simulated = False

    def __init__(self, duration_s=None, warmup_s=None):
        self.duration_s, self.warmup_s = duration_s, warmup_s
        pid = os.getpid()
        self.ns = {t: f"fom2{t[0]}{pid}" for t in ("edge", "fog", "cloud")}
        self.procs, self.up = [], False

    def _x(self, tier, cmd, check=True):
        return _sh(f"ip netns exec {self.ns[tier]} {cmd}", check)

    def provision(self, desc):
        if not netns_available():
            raise PermissionError("servono root e network namespace (ip netns)")
        n = os.getpid() % 100000
        try:
            for ns in self.ns.values():
                _sh(f"ip netns add {ns}")
            for (a, da, ia, b, db, ib, net) in (
                    ("edge", f"ve{n}", "10.98.1.1", "fog", f"vf{n}", "10.98.1.2", "10.98.1"),
                    ("fog", f"vg{n}", "10.98.2.1", "cloud", f"vc{n}", "10.98.2.2", "10.98.2")):
                _sh(f"ip link add {da} type veth peer name {db}")
                _sh(f"ip link set {da} netns {self.ns[a]}")
                _sh(f"ip link set {db} netns {self.ns[b]}")
                for tier, dev, ip in ((a, da, ia), (b, db, ib)):
                    _sh(f"ip -n {self.ns[tier]} addr add {ip}/24 dev {dev}")
                    _sh(f"ip -n {self.ns[tier]} link set {dev} up")
                    _sh(f"ip -n {self.ns[tier]} link set lo up")
            self.wan_devs = [("fog", f"vg{n}"), ("cloud", f"vc{n}")]
            self._x("edge", "ip route add default via 10.98.1.2")
            self._x("cloud", "ip route add default via 10.98.2.1")
            self._x("fog", "sysctl -qw net.ipv4.ip_forward=1")
        except Exception:
            self.teardown()
            raise
        self.up = True
        return [{"id": s["id"], "tier": s["tier"], "site": "local-lab", "domain": "local-lab",
                 "nodes": s["nodes"], "emulated": True, "flavor": s.get("flavor")}
                for s in desc["infrastructure"]["sites"]]

    def apply_impairments(self, cfg):
        log = []
        for tier, dev in self.wan_devs:
            for c in netem_commands(cfg.wan, dev):
                self._x(tier, c)
                log.append(f"[{self.ns[tier]}] {c}")
        return log

    def deploy(self, desc, cfg):
        pass

    def _spawn(self, tier, args):
        env = {**os.environ, "PYTHONPATH": str(REPO)}
        p = subprocess.Popen(["ip", "netns", "exec", self.ns[tier], "python3", "-m",
                              "workload.components", *args], cwd=REPO, env=env,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.procs.append(p)

    def _stop(self):
        for p in self.procs:
            p.terminate()
        for p in self.procs:
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
        self.procs = []

    def run_workload(self, desc, cfg, seed):
        app, ex = desc["application"], desc["execution"]
        warm = self.warmup_s if self.warmup_s is not None else ex["warmup_min"] * 60
        dur = self.duration_s if self.duration_s is not None else ex["duration_min"] * 60
        period, deadline = app["timing"]["control_period_s"], app["timing"]["deadline_ms"]
        et, ct = cfg.placement["state-estimator"], cfg.placement["signal-controller"]
        out = Path(f"/tmp/fom2-edge-{os.getpid()}.jsonl")
        self._stop()
        try:
            self._spawn(ct, ["controller", "--port", str(PORTS["controller"]),
                             "--actuator", f"{ADDR[(ct, 'edge')]}:{PORTS['edge']}",
                             "--compute-ms", str(COMPUTE_MS["signal-controller"][ct])])
            self._spawn(et, ["estimator", "--port", str(PORTS["estimator"]),
                             "--controller", f"{ADDR[(et, ct)]}:{PORTS['controller']}",
                             "--compute-ms", str(COMPUTE_MS["state-estimator"][et])])
            time.sleep(1.0)
            edge = ["edge", "--port", str(PORTS["edge"]),
                    "--estimator", f"{ADDR[('edge', et)]}:{PORTS['estimator']}",
                    "--period", str(period), "--deadline-ms", str(deadline),
                    "--duration", str(warm + dur), "--seed", str(seed), "--out", str(out)]
            r = subprocess.run(["ip", "netns", "exec", self.ns["edge"], "python3", "-m",
                                "workload.components", *edge], cwd=REPO,
                               env={**os.environ, "PYTHONPATH": str(REPO)},
                               capture_output=True, text=True, timeout=warm + dur + 60)
            if r.returncode != 0:
                raise RuntimeError(f"edge failed: {r.stderr[-300:]}")
            rows = [json.loads(l) for l in out.read_text().splitlines()]
        finally:
            self._stop()
            out.unlink(missing_ok=True)
        wanted = set(desc["observability"]["metrics"]) | {"app.deadline_miss"}
        samples = rows_to_samples(rows, warm, wanted)
        return samples, "completed"

    def teardown(self):
        self._stop()
        for ns in self.ns.values():
            _sh(f"ip netns del {ns}", check=False)
        self.up = False
