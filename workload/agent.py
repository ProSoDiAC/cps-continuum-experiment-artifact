"""Per-node observability agent (standard library only).

Every `interval` seconds it samples system CPU, memory, interface network bytes and the RTT
towards the peers (single ping), and writes a JSON line. At the end of the run it writes a summary line with its
own cost (CPU seconds, probes sent), i.e. the overhead of observability.
    python3 -m workload.agent --out F --duration S --interval 1 --iface auto --peers 10.0.0.2,10.0.0.3
"""
import argparse
import json
import os
import re
import signal
import subprocess
import threading
import time


def cpu_times():
    f = open("/proc/stat").readline().split()[1:]
    vals = [int(x) for x in f]
    idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
    return sum(vals), idle


def mem_used_mb():
    m = {}
    for line in open("/proc/meminfo"):
        k, v = line.split(":", 1)
        m[k] = int(v.split()[0])
    return round((m["MemTotal"] - m.get("MemAvailable", m["MemFree"])) / 1024.0, 1)


def default_iface():
    for line in open("/proc/net/route").read().splitlines()[1:]:
        f = line.split()
        if f[1] == "00000000":
            return f[0]
    return "lo"


def net_bytes(iface):
    for line in open("/proc/net/dev"):
        if line.strip().startswith(iface + ":"):
            f = line.split(":", 1)[1].split()
            return int(f[0]), int(f[8])
    return 0, 0


def probe(ip):
    try:
        r = subprocess.run(["ping", "-c", "1", "-W", "1", ip], capture_output=True, text=True, timeout=3)
        m = re.search(r"time=([\d.]+) ms", r.stdout)
        return float(m.group(1)) if m else None
    except (OSError, subprocess.SubprocessError):
        return None


def main():
    a = argparse.ArgumentParser(prog="workload.agent")
    a.add_argument("--out", required=True)
    a.add_argument("--duration", type=float, required=True)
    a.add_argument("--interval", type=float, default=1.0)
    a.add_argument("--iface", default="auto")
    a.add_argument("--peers", default="")
    a = a.parse_args()
    iface = default_iface() if a.iface == "auto" else a.iface
    peers = [p for p in a.peers.split(",") if p]
    out = open(a.out, "w")
    stop = threading.Event()                   # SIGTERM/SIGINT: stops and writes the summary with its own cost
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    start = time.monotonic()
    t_prev, idle_prev = cpu_times()
    rx_prev, tx_prev = net_bytes(iface)
    probes, k = 0, 0
    while True:
        k += 1
        wake = start + k * a.interval
        if wake - start > a.duration or stop.wait(max(0.0, wake - time.monotonic())):
            break
        t, idle = cpu_times()
        rx, tx = net_bytes(iface)
        rtt = {}
        for ip in peers:
            rtt[ip] = probe(ip)
            probes += 1
        out.write(json.dumps({
            "t_epoch": round(time.time(), 3),
            "cpu_pct": round(100.0 * (1 - (idle - idle_prev) / max(t - t_prev, 1)), 2),
            "mem_mb": mem_used_mb(),
            "rx_Bps": round((rx - rx_prev) / a.interval, 1), "tx_Bps": round((tx - tx_prev) / a.interval, 1),
            "rtt_ms": rtt}) + "\n")
        out.flush()
        t_prev, idle_prev, rx_prev, tx_prev = t, idle, rx, tx
    ct = os.times()
    out.write(json.dumps({"summary": True, "cpu_s": round(ct[0] + ct[1] + ct[2] + ct[3], 3),
                          "probes": probes, "samples": k - 1}) + "\n")
    out.close()


if __name__ == "__main__":
    main()
