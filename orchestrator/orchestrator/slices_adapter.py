"""SLICES backend via slices-cli (+ local nodes reached via Tailscale).

- provision: creates the experiment and the VMs (slices bi create), with cloud-init for chrony and,
  if there is at least one `manual` site (hybrid binding), Tailscale with a dedicated tag.
- apply_impairments: netem via SSH. Every pair of nodes separated by a WAN link of the
  descriptor (connected components without the WAN links) crosses the WAN, even if on the real
  network they are adjacent: every SLICES node delays only the traffic towards the nodes on the
  other side (filter by IP); edge-fog and the control SSH stay untouched. delay_ms is per direction
  (RTT = 2x). The `manual` node (local edge in the hybrid binding) is modelled too, with tc on the
  tailscale0 interface of the local machine and only towards the peers: the local netem is removed
  at teardown. The emulated edge respects the CPU/RAM limits of the descriptor (cgroup via
  systemd-run). The real RTT between the domains (edge-fog) is recorded in the baseline.
  Before applying, the baseline (ping, chrony) is measured with the impairments removed.
- teardown: destroys only the resources created by this instance and deletes the cloud-init.
For safety, real=True (CLI: --real) or dry_run=True (--dry-run), or a fake shell (tests), is
required: without them the backend refuses to start.
With dry_run=True no command is executed: the commands are only recorded in self.commands.
"""
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path

from .backends.base import Backend
from .netem import netem_peer_commands

TS_TAG = "tag:fom2"
DEFAULT_KEY_FILE = "~/.config/fom2/ts_authkey"
DEFAULT_SSH_PUB = "~/.ssh/id_ed25519_slices_fom2.pub"


class Shell:
    """Runs commands; replaceable in tests."""

    def run(self, cmd, env=None, timeout=None):
        r = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=timeout)
        if r.returncode != 0:
            tail = f" ... {str(cmd[-1])[:200]}" if len(cmd) > 6 else ""
            raise RuntimeError(f"command failed ({r.returncode}): {' '.join(map(str, cmd[:6]))}{tail}\n"
                               f"{(r.stderr or r.stdout)[-500:]}")
        return r.stdout


def _redact(cmd):
    return [("--authkey=REDACTED" if str(c).startswith("--authkey=") else c) for c in cmd]


class SlicesBackend(Backend):
    name = "slices"
    simulated = False

    def __init__(self, shell=None, dry_run=False, ssh_pub=DEFAULT_SSH_PUB,
                 ts_key_file=DEFAULT_KEY_FILE, duration="3h", experiment=None, real=False):
        if not (dry_run or real or shell is not None):
            raise PermissionError("the slices backend creates real resources: use --real (or --dry-run)")
        self.sh = shell or Shell()
        self.dry_run = dry_run
        self.ssh_pub = os.path.expanduser(ssh_pub)
        self.ts_key_file = os.path.expanduser(ts_key_file)
        self.duration = duration
        self.experiment = experiment
        self.commands, self.created, self._userdata = [], [], None
        self.inventory, self.conditions = [], {}
        self.duration_s = self.warmup_s = None
        self._sleep = time.sleep

    # -- util ---------------------------------------------------------------------
    def _env(self):
        return {**os.environ, "SLICES_EXPERIMENT": self.experiment}

    def _run(self, cmd, timeout=None):
        self.commands.append(" ".join(map(str, _redact(cmd))))
        if self.dry_run:
            return ""
        return self.sh.run(cmd, env=self._env(), timeout=timeout)

    def _ssh(self, node, remote, timeout=None):
        return self._run(["slices", "bi", "ssh", node["name"], "--", remote], timeout=timeout)

    def _is_hybrid(self, desc):
        return any(s.get("provider") == "manual" for s in desc["infrastructure"]["sites"])

    def _cloud_init(self, hostname, hybrid):
        lines = ["#cloud-config", "package_update: true", "packages: [chrony, iperf3]"]
        if hybrid:
            key = "REDACTED" if self.dry_run else Path(self.ts_key_file).read_text().strip()
            lines += ["runcmd:",
                      "  - curl -fsSL https://tailscale.com/install.sh | sh",
                      f"  - tailscale up --authkey={key} --advertise-tags={TS_TAG} "
                      f"--hostname={hostname} --accept-dns=false"]
        return "\n".join(lines) + "\n"

    # -- Backend ------------------------------------------------------------------
    def provision(self, desc):
        exp = desc["experiment"]
        self.experiment = self.experiment or f"{exp['id']}-v{exp['version'].replace('.', '-')}"
        hybrid = self._is_hybrid(desc)
        for s in desc["infrastructure"]["sites"]:       # before creating VMs: the manual node must be able to use tc without a password
            if s.get("provider") == "manual" and not self.dry_run:
                node = {"provider": "manual", "host": s.get("host")}
                try:
                    self._exec(node, "sudo -n tc -V >/dev/null && sudo -n systemd-run --version >/dev/null", timeout=30)
                except RuntimeError as e:
                    raise RuntimeError(f"manual node '{s['id']}' ({s.get('host') or 'local'}) requires sudo without "
                                       f"password for tc and systemd-run: {e}") from e
        try:
            self._run(["slices", "experiment", "create", self.experiment, "-d", self.duration,
                       "-D", f"FOM2 {exp['id']}"])
        except RuntimeError as e:
            if "already exists" not in str(e):             # active experiment with the same name: it is reused
                raise
        self.inventory = []
        for s in desc["infrastructure"]["sites"]:
            for i in range(s["nodes"]):
                node = {"id": s["id"], "tier": s["tier"], "site": s["site"], "domain": s["domain"],
                        "provider": s.get("provider", "slices-cli"), "emulated": bool(s.get("emulated")),
                        "flavor": s.get("flavor"), "name": f"{s['id']}-{i}", "limits": s.get("limits"), "host": s.get("host"),
                        "image": s.get("image", "Ubuntu 24.04.1") if s.get("provider", "slices-cli") == "slices-cli" else None}
                if node["provider"] == "slices-cli":
                    self._create(node, s, hybrid)
                self.inventory.append(node)
        if not self.dry_run:
            self._refresh_inventory()
        for n in self.inventory:
            if n["provider"] == "slices-cli":
                self._wait_ready(n)
        return self.inventory

    def _wait_ready(self, node, attempts=40, pause=10):
        """Waits until SSH answers (host keys may appear after `--wait`) and cloud-init finishes."""
        for i in range(attempts):
            try:
                self._run(["slices", "bi", "known-hosts", "add"])
                self._ssh(node, "true", timeout=60)
                break
            except (RuntimeError, subprocess.TimeoutExpired):
                if i == attempts - 1:
                    raise
                self._sleep(pause)
        self._ssh(node, "sudo cloud-init status --wait >/dev/null 2>&1 || true", timeout=900)

    def _create(self, node, s, hybrid):
        cmd = ["slices", "bi", "--infra", s["site"], "create", node["name"],
               "--flavor", s.get("flavor", "tiny"), "--image", s.get("image", "Ubuntu 24.04.1"),
               "-d", self.duration, "--ssh-key-file", self.ssh_pub, "--wait"]
        if not self.dry_run:
            fd, path = tempfile.mkstemp(prefix="fom2-ud-", suffix=".yaml")
            os.chmod(path, 0o600)
            with os.fdopen(fd, "w") as f:
                f.write(self._cloud_init(f"fom2-{node['name']}", hybrid))
            self._userdata = path
            cmd += ["--user-data", path]
        else:
            cmd += ["--user-data", "<cloud-init>"]
        self.created.append(node["name"])       # first: if the command is interrupted, the teardown destroys it
        try:
            self._run(cmd)
        except RuntimeError as e:
            if "already exists" not in str(e):
                self.created.remove(node["name"])
                raise                               # VM left by an interrupted run: it is reused
        finally:
            if self._userdata:
                os.unlink(self._userdata)
                self._userdata = None

    def _refresh_inventory(self):
        data = json.loads(self._run(["slices", "bi", "list-resources", "-f", "json"]))
        by_name = {r["friendly_name"]: r for r in data}
        for node in self.inventory:
            r = by_name.get(node["name"])
            if r:
                node.update({"resource_id": r["id"], "status": r["status"],
                             "private_ipv4": r.get("private_ipv4"), "expires_at": r["expires_at"]})

    def _wan_peers(self, desc):
        """{SLICES node: [nodes on the other side of the WAN]}.

        The WAN links are removed and the connected components of the sites are computed: two nodes in
        different components cross the WAN (even if they are adjacent on the real network).
        The `manual` nodes are modelled too (local netem on the tailscale0 interface).
        """
        links = desc["infrastructure"]["links"]
        comp = {s["id"]: s["id"] for s in desc["infrastructure"]["sites"]}

        def find(x):
            while comp[x] != x:
                x = comp[x]
            return x
        for l in links:
            if l["profile"] != "wan":
                comp[find(l["from"])] = find(l["to"])
        peers = {}
        for n in self.inventory:
            others = [o for o in self.inventory if find(o["id"]) != find(n["id"])]
            if others and any(l["profile"] == "wan" for l in links):
                peers[n["name"]] = others
        return peers

    def _ensure_addrs(self, desc):
        hybrid = self._is_hybrid(desc)
        tiers = [n["tier"] for n in self.inventory]
        if len(tiers) != len(set(tiers)):
            raise NotImplementedError("only one node per tier (addresses per tier)")
        self._addrs = {n["tier"]: self._addr(n, hybrid) for n in self.inventory}

    def apply_impairments(self, cfg, desc=None):
        desc = desc or self._desc
        hybrid = self._is_hybrid(desc)
        self._ensure_addrs(desc)
        peers = self._wan_peers(desc)
        nodes = [n for n in self.inventory if n["name"] in peers]
        dev = "tailscale0" if hybrid else "$(ip route show default | awk '{print $5; exit}')"
        self._shaped = [n for n in nodes if n["provider"] == "manual"] or getattr(self, "_shaped", [])
        for n in nodes:                                   # baseline a netem azzerato
            self._exec(n, f"sudo tc qdisc del dev {dev} root 2>/dev/null || true")
        self.conditions = self.measure_baseline(desc)
        self.conditions["impairment_scope"] = {n["name"]: [o["name"] for o in peers[n["name"]]] for n in nodes}
        log = []
        for node in nodes:
            ips = [self._addrs[o["tier"]] for o in peers[node["name"]]]
            for c in netem_peer_commands(dict(cfg.wan), dev, ips):
                self._exec(node, f"sudo {c}")
                log.append(f"[{node['name']}] {c}")
        return log

    # -- indirizzi, baseline -------------------------------------------------------
    def _node(self, tier):
        return next(n for n in self.inventory if n["tier"] == tier)

    def _local(self, cmd, timeout=None):
        self.commands.append(" ".join(cmd))
        if self.dry_run:
            return ""
        return self.sh.run(cmd, timeout=timeout)

    def _exec(self, node, remote, timeout=None):
        """Runs a shell command on the node (SSH for SLICES, local for manual)."""
        if node["provider"] == "manual":
            if node.get("host"):
                return self._local(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", node["host"], remote],
                                   timeout=timeout)
            return self._local(["bash", "-c", remote], timeout=timeout)
        return self._ssh(node, remote, timeout=timeout)

    def _addr(self, node, hybrid):
        if self.dry_run:
            return "100.64.0.%d" % (1 + self.inventory.index(node))
        if hybrid:
            out = self._exec(node, "tailscale ip -4").split()
            return next(x for x in out if x.count(".") == 3 and x[0].isdigit())
        return node["private_ipv4"]

    def measure_baseline(self, desc):
        """RTT, loss, path and clock offset, with the impairments removed."""
        hybrid = self._is_hybrid(desc)
        cond = {"hybrid": hybrid}
        edge, far = self._node("edge"), self._node("cloud")
        if self.dry_run:
            return cond
        far_ip = self._addr(far, hybrid)
        self._exec(edge, f"ping -c 5 -i 0.2 -q {far_ip} >/dev/null 2>&1 || true")    # warm-up: percorso stabilito
        self._exec(edge, f"ping -c 5 -i 0.2 -q {self._addr(self._node('fog'), hybrid)} >/dev/null 2>&1 || true")
        out = self._exec(edge, f"ping -c 20 -i 0.2 -q {far_ip} | tail -2")
        import re
        m = re.search(r"= ([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+) ms", out)
        loss = re.search(r"([\d.]+)% packet loss", out)
        cond["wan_rtt_baseline_ms"] = {"avg": float(m.group(2)), "mdev": float(m.group(4))} if m else None
        cond["baseline_loss_pct"] = float(loss.group(1)) if loss else None
        fog = self._node("fog")
        out = self._exec(edge, f"ping -c 20 -i 0.2 -q {self._addr(fog, hybrid)} | tail -2")
        m = re.search(r"= ([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+) ms", out)
        cond["edge_fog_rtt_baseline_ms"] = {"avg": float(m.group(2)), "mdev": float(m.group(4))} if m else None
        offsets, sync = {}, {}
        for n in self.inventory:
            out = self._exec(n, "chronyc tracking 2>/dev/null | grep 'System time' || true")
            r = re.search(r"System time\s*:\s*([\d.]+) seconds (slow|fast)", out)
            offsets[n["name"]] = (float(r.group(1)) * (1 if r.group(2) == "fast" else -1)) if r else None
            if not r:                                 # without chrony (e.g. manual node): only the synchronisation status
                sync[n["name"]] = self._exec(n, "timedatectl show -p NTPSynchronized --value 2>/dev/null || true").strip() or None
        cond["clock_offset_s"] = offsets
        if sync:
            cond["ntp_synchronized_no_chrony"] = sync
        loads = {n["name"]: self._loadavg(n) for n in self.inventory}
        cond["background_load"] = {k: v[0] for k, v in loads.items()}
        if any(v[0] is None for v in loads.values()):
            cond["background_load_raw"] = {k: v[1] for k, v in loads.items() if v[0] is None}
        if hybrid:
            cond["tailscale_path"] = self._exec(edge, f"tailscale ping -c 1 {far_ip} 2>&1 | head -1").strip()
        return cond

    def _loadavg(self, node):
        """Load average (1, 5, 15 min) of the node, before the run: recorded background load.

        The parsing looks for the line with three decimal numbers (the ssh output may contain warnings). If it does not
        find it, it returns (None, raw output) to allow diagnosis.
        """
        import re
        out = self._exec(node, "cat /proc/loadavg")
        rows = re.findall(r"^\s*(\d+\.\d+)\s+(\d+\.\d+)\s+(\d+\.\d+)\b", out, flags=re.M)
        return ([float(x) for x in rows[-1]], None) if rows else (None, out[-300:])

    # -- deploy e workload --------------------------------------------------------
    def _push_workload(self):
        if self.dry_run:
            self.commands.append("tar+scp workload/ -> ~/fom2 on every SLICES node")
            return
        repo = Path(__file__).resolve().parents[2]
        fd, tgz = tempfile.mkstemp(prefix="fom2-wl-", suffix=".tgz")
        os.close(fd)
        try:
            self.sh.run(["tar", "czf", tgz, "-C", str(repo), "--exclude=__pycache__", "workload"])
            for n in self.inventory:
                unpack = "mkdir -p ~/fom2 && tar xzf /tmp/workload.tgz -C ~/fom2 && python3 --version"
                if n["provider"] == "slices-cli":
                    self._run(["slices", "bi", "scp", tgz, f"{n['name']}:/tmp/workload.tgz"])
                    self._ssh(n, unpack)
                elif n.get("host"):
                    self._local(["scp", "-o", "BatchMode=yes", tgz, f"{n['host']}:/tmp/workload.tgz"])
                    self._exec(n, unpack)
        finally:
            os.unlink(tgz)

    def deploy(self, desc, cfg):
        if not getattr(self, "_pushed", False):
            self._push_workload()
            self._pushed = True
        self._ensure_addrs(desc)

    def _py(self, node, args, module="components"):
        if node["provider"] == "slices-cli":
            return f"cd ~/fom2 && python3 -m workload.{module} {args}"
        base = "cd ~/fom2 && " if node.get("host") else f"cd {Path(__file__).resolve().parents[2]} && PYTHONPATH=. "
        return f"{base}{self._limits_prefix(node)}python3 -m workload.{module} {args}"

    def _fetch(self, node, remote, local):
        """Downloads a file from the node (SLICES: slices scp; remote manual: scp; local manual: copy)."""
        if node["provider"] == "manual" and node.get("host"):
            self._local(["scp", "-o", "BatchMode=yes", f"{node['host']}:{remote}", local])
        elif node["provider"] == "manual":
            Path(local).write_bytes(Path(remote).read_bytes())
        else:
            self._run(["slices", "bi", "scp", f"{node['name']}:{remote}", local])

    def _start_agents(self, desc, seconds):
        """Starts the observability agent on every node (full level). Returns {node name: remote file}."""
        iface = "tailscale0" if self._is_hybrid(desc) else "auto"
        files = {}
        for n in self.inventory:
            peers = ",".join(self._addrs[o["tier"]] for o in self.inventory if o is not n)
            files[n["name"]] = f"/tmp/fom2-agent-{os.getpid()}-{n['name']}.jsonl"
            args = (f"--out {files[n['name']]} --duration {seconds} --interval {desc['observability']['sampling_s']} "
                    f"--iface {iface} --peers {peers}")
            self._exec(n, f"nohup bash -c '{self._py(n, args, 'agent')}' > /tmp/fom2-agent.log 2>&1 &")
        return files

    def _collect_agents(self, files, warm, dur, edge_epoch0):
        """Stops the agents, downloads the files and converts them into samples (time aligned to the gateway)."""
        import json as _json
        for n in self.inventory:
            self._exec(n, "pkill -TERM -f 'workload[.]agent' || true; sleep 1")
        samples = []
        tier_of = {self._addrs[n["tier"]]: n["tier"] for n in self.inventory}
        for n in self.inventory:
            local = tempfile.mktemp(prefix="fom2-agent-", suffix=".jsonl")
            try:
                self._fetch(n, files[n["name"]], local)
                rows = [_json.loads(l) for l in Path(local).read_text().splitlines() if l.strip()]
            finally:
                Path(local).unlink(missing_ok=True)
            for r in rows:
                if r.get("summary"):
                    samples += [(float(dur), "obs.agent_cpu_s", r["cpu_s"], n["tier"]),
                                (float(dur), "obs.agent_probes", float(r["probes"]), n["tier"])]
                    continue
                t = round(r["t_epoch"] - edge_epoch0 - warm, 3)
                if not 0 <= t <= dur:
                    continue
                samples += [(t, "infra.cpu", r["cpu_pct"], n["tier"]), (t, "infra.mem", r["mem_mb"], n["tier"]),
                            (t, "net.rx_bytes", r["rx_Bps"], n["tier"]), (t, "net.tx_bytes", r["tx_Bps"], n["tier"])]
                samples += [(t, f"net.rtt_probe.{tier_of[ip]}", v, n["tier"])
                            for ip, v in r["rtt_ms"].items() if v is not None and ip in tier_of]
        return samples

    @staticmethod
    def _limits_prefix(node):
        """CPU/RAM limits of the emulated edge as a cgroup (systemd-run --scope); empty if not declared."""
        lim = node.get("limits") or {}
        props = []
        if lim.get("cpus"):
            props.append(f"CPUQuota={int(float(lim['cpus']) * 100)}%")
        if lim.get("mem"):
            props += [f"MemoryMax={str(lim['mem']).upper()}", "MemorySwapMax=0"]
        if not props:
            return ""
        # remote node: sudo (rule restricted to systemd-run) and the process returns to the login user
        who = "sudo " if node.get("host") else ""
        uid = "--uid=$(id -u) --gid=$(id -g) " if node.get("host") else ""
        return f"{who}systemd-run --scope --quiet {uid}" + " ".join(f"-p {x}" for x in props) + " "

    def _rtt_ms(self, desc):
        """Mean edge->cloud RTT with the impairments applied (None if the ping does not answer)."""
        import re
        edge, far = self._node("edge"), self._node("cloud")
        out = self._exec(edge, f"ping -c 10 -i 0.2 -q {self._addr(far, self._is_hybrid(desc))} | tail -2")
        m = re.search(r"= ([\d.]+)/([\d.]+)/", out)
        return float(m.group(2)) if m else None

    def _check_connectivity(self, edge_t, est_t, ctl_t):
        """Checks in a few seconds that every TCP link of the chain is reachable (port by port).

        A link silently blocked (ACL, firewall) would make every workload cycle time out: better
        to stop immediately with the exact direction. It is checked once per placement combination.
        """
        key = (edge_t, est_t, ctl_t)
        done = self.__dict__.setdefault("_conn_ok", set())
        if key in done or self.dry_run:
            self.commands.append(f"connectivity-check {edge_t}->{est_t}->{ctl_t}->{edge_t}") if key not in done else None
            done.add(key)
            return
        for src, dst, port in ((edge_t, est_t, 9101), (est_t, ctl_t, 9102), (ctl_t, edge_t, 9103)):
            ip, a, b = self._addrs[dst], self._node(src), self._node(dst)
            self._exec(b, f"nohup timeout 15 python3 -c \"import socket;s=socket.socket();"
                          f"s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);s.bind(('{ip}',{port}));s.listen(1);"
                          f"s.settimeout(12);s.accept()\" >/dev/null 2>&1 &")
            self._exec(a, "sleep 1")
            try:
                self._exec(a, f"python3 -c \"import socket;socket.create_connection(('{ip}',{port}),timeout=5)\"",
                           timeout=30)
            except RuntimeError as e:
                raise RuntimeError(f"TCP connection {src} ({a['name']}) -> {dst} ({b['name']}) {ip}:{port} not "
                                   f"reachable (ACL or firewall?): {e}") from e
        done.add(key)

    def run_workload(self, desc, cfg, seed):
        from workload.components import COMPUTE_MS
        from .edge_rows import rows_to_samples
        app, ex = desc["application"], desc["execution"]
        warm = self.warmup_s if getattr(self, "warmup_s", None) is not None else ex["warmup_min"] * 60
        dur = self.duration_s if getattr(self, "duration_s", None) is not None else ex["duration_min"] * 60
        et, ct = cfg.placement["state-estimator"], cfg.placement["signal-controller"]
        edge, en, cn = self._node("edge"), self._node(et), self._node(ct)
        remote_out = f"/tmp/fom2-edge-{os.getpid()}.jsonl"
        self._stop_components()
        self._check_connectivity("edge", et, ct)
        rtt_before = self._rtt_ms(desc) if not self.dry_run else None
        agent_files = {}
        try:
            if cfg.obs == "full":                          # full observability: agent on every node
                agent_files = self._start_agents(desc, warm + dur + 600)
            for node, args in (
                    (cn, f"controller --port 9102 --actuator {self._addrs['edge']}:9103 "
                         f"--compute-ms {COMPUTE_MS['signal-controller'][ct]} --bind {self._addrs[ct]}"),
                    (en, f"estimator --port 9101 --controller {self._addrs[ct]}:9102 "
                         f"--compute-ms {COMPUTE_MS['state-estimator'][et]} --bind {self._addrs[et]}")):
                cmd = f"nohup bash -c '{self._py(node, args)}' > /tmp/fom2-{args.split()[0]}.log 2>&1 &"
                self._exec(node, cmd)
            self._exec(edge, "sleep 1")
            timing = app["timing"]
            edge_args = (f"edge --port 9103 --estimator {self._addrs[et]}:9101 --period {timing['control_period_s']} "
                         f"--deadline-ms {timing['deadline_ms']} --duration {warm + dur} --seed {seed} --out {remote_out} --bind {self._addrs['edge']}")
            self._exec(edge, self._py(edge, edge_args), timeout=warm + dur + 120)
            if self.dry_run:
                return [], "dry-run"
            local = tempfile.mktemp(prefix="fom2-rows-", suffix=".jsonl")
            try:
                self._fetch(edge, remote_out, local)
                import json as _json
                rows = [_json.loads(l) for l in Path(local).read_text().splitlines()]
            finally:
                Path(local).unlink(missing_ok=True)
            agent_samples = []
            if agent_files:
                epoch0 = rows[0]["t_epoch"] - rows[0]["t_s"]      # epoch instant of the gateway time zero
                agent_samples = self._collect_agents(agent_files, warm, dur, epoch0)
        finally:
            self._stop_components()
        wanted = set(desc["observability"]["metrics"]) | {"app.deadline_miss", "app.gateway_cpu"}
        samples = rows_to_samples(rows, warm, wanted) + agent_samples
        if "net.rtt" in wanted:
            rtt_after = self._rtt_ms(desc)
            samples += [(t, "net.rtt", v, "net") for t, v in ((0.0, rtt_before), (float(dur), rtt_after))
                        if v is not None]
        return samples, "completed"

    def _stop_components(self):
        for n in self.inventory:
            try:
                self._exec(n, "pkill -f 'workload[.](components|agent)' || true")
            except RuntimeError:
                pass

    def teardown(self):
        for n in getattr(self, "_shaped", []):           # netem on the local machine: it must always be removed
            try:
                self._exec(n, "sudo tc qdisc del dev tailscale0 root 2>/dev/null || true")
            except (RuntimeError, subprocess.TimeoutExpired):
                pass
        self._shaped = []
        for name in self.created:
            try:
                self._run(["slices", "bi", "destroy", "--experiment", self.experiment, name])
            except RuntimeError:
                pass                      # best effort: le VM scadono comunque
        self.created = []
