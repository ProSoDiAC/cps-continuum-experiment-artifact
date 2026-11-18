"""tc/netem commands for the network impairments (only generation here, no execution)."""


def _params(wan):
    parts = []
    if wan["delay_ms"]:
        parts += ["delay", f"{wan['delay_ms']}ms"]
        if wan.get("jitter_ms"):
            parts.append(f"{wan['jitter_ms']}ms")
    if wan["loss_pct"]:
        parts += ["loss", f"{wan['loss_pct']}%"]
    return parts


def netem_peer_commands(wan, dev, peers):
    """netem only on the traffic towards `peers` (prio + u32 filters); the rest passes unchanged.

    Unfiltered traffic goes to band 3 (priomap all set to 2), without delay.
    """
    peers = [peers] if isinstance(peers, str) else list(peers)
    params = _params(wan)
    if not params or not peers:
        return [f"tc qdisc del dev {dev} root"]
    cmds = [
        f"tc qdisc replace dev {dev} root handle 1: prio bands 3 priomap " + " ".join(["2"] * 16),
        f"tc qdisc add dev {dev} parent 1:1 handle 10: netem " + " ".join(params),
    ]
    cmds += [f"tc filter add dev {dev} protocol ip parent 1: prio 1 u32 match ip dst {p}/32 flowid 1:1"
             for p in peers]
    return cmds


def netem_commands(wan, dev="eth0"):
    """Returns the tc commands that apply delay/jitter/loss on the WAN link."""
    parts = ["tc", "qdisc", "replace", "dev", dev, "root", "netem"]
    if wan["delay_ms"]:
        parts += ["delay", f"{wan['delay_ms']}ms"]
        if wan.get("jitter_ms"):
            parts.append(f"{wan['jitter_ms']}ms")
    if wan["loss_pct"]:
        parts += ["loss", f"{wan['loss_pct']}%"]
    if len(parts) == 7:                       # no impairment: restore
        return [f"tc qdisc del dev {dev} root"]
    return [" ".join(parts)]


# --- isolated local lab (network namespace + veth) ---------------------------
# It never touches the host interfaces: it creates two namespaces connected by a veth,
# applies netem on both ends (delay_ms = delay per direction, RTT = 2x)
# and removes everything on exit.
import os
import re
import subprocess
from contextlib import contextmanager


def _sh(cmd, check=True):
    return subprocess.run(cmd, shell=True, check=check, capture_output=True, text=True)


def netns_available():
    if os.geteuid() != 0:
        return False
    probe = f"nlprobe{os.getpid()}"
    ok = _sh(f"ip netns add {probe}", check=False).returncode == 0
    if ok:
        _sh(f"ip netns del {probe}", check=False)
    return ok


class NetemLab:
    def __init__(self):
        n = os.getpid()
        self.a, self.b = f"nla{n}", f"nlb{n}"
        self.va, self.vb = f"va{n % 100000}", f"vb{n % 100000}"
        self.ip_a, self.ip_b = "10.99.0.1", "10.99.0.2"

    def up(self):
        try:
            _sh(f"ip netns add {self.a}")
            _sh(f"ip netns add {self.b}")
            _sh(f"ip link add {self.va} type veth peer name {self.vb}")
            _sh(f"ip link set {self.va} netns {self.a}")
            _sh(f"ip link set {self.vb} netns {self.b}")
            for ns, dev, ip in ((self.a, self.va, self.ip_a), (self.b, self.vb, self.ip_b)):
                _sh(f"ip -n {ns} addr add {ip}/24 dev {dev}")
                _sh(f"ip -n {ns} link set {dev} up")
        except Exception:
            self.down()
            raise
        return self

    def down(self):
        for ns in (self.a, self.b):
            _sh(f"ip netns del {ns}", check=False)

    def apply(self, wan):
        """Applies the impairments in both directions. Returns the log of the commands."""
        log = []
        for ns, dev in ((self.a, self.va), (self.b, self.vb)):
            for c in netem_commands(wan, dev):
                _sh(f"ip netns exec {ns} {c}")
                log.append(f"[{ns}] {c}")
        return log

    def ping(self, count=20, interval=0.05):
        """Measured RTT (ms) and loss (%) from a to b."""
        r = _sh(f"ip netns exec {self.a} ping -c {count} -i {interval} -q {self.ip_b}", check=False)
        rtt = re.search(r"= ([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+) ms", r.stdout)
        loss = re.search(r"([\d.]+)% packet loss", r.stdout)
        return {"avg_ms": float(rtt.group(2)) if rtt else None,
                "loss_pct": float(loss.group(1)) if loss else 100.0}


@contextmanager
def netem_lab():
    lab = NetemLab().up()
    try:
        yield lab
    finally:
        lab.down()
