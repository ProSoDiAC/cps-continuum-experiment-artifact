"""Components of the traffic-control workload (standard library only).

Synchronous HTTP chain, one cycle every control_period:
  edge: detects the queues -> POST estimator /estimate
  estimator: estimates (EMA) -> POST controller /decide
  controller: chooses the phase -> POST actuator /actuate (on the edge node)
  actuator: applies the phase if the deadline has not expired, otherwise discards it.

Actuation time and deadline are measured on the edge node alone (same monotonic
clock), so no clock synchronisation between nodes is needed for the end-to-end latency.
Roles:  python3 -m workload.components {estimator,controller,edge} ...
"""
import argparse
import http.client
import json
import os
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .traffic import DT, Intersection, choose_phase

# Emulated computation cost (ms, CPU busy-wait) per component and tier.
COMPUTE_MS = {
    "state-estimator": {"fog": 80, "cloud": 40, "edge": 160},
    "signal-controller": {"fog": 30, "cloud": 15, "edge": 120},
}


def busy(ms):
    end = time.perf_counter() + ms / 1000.0
    while time.perf_counter() < end:
        pass


class Peer:
    """Persistent-connection HTTP client (TCP_NODELAY), used by one thread at a time."""

    def __init__(self, addr):
        self.host, self.port = addr.rsplit(":", 1)
        self.conn, self.lock = None, threading.Lock()

    def _connect(self, timeout):
        self.conn = http.client.HTTPConnection(self.host, int(self.port), timeout=timeout)
        self.conn.connect()
        self.conn.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

    def post(self, path, obj, timeout=2.0):
        body = json.dumps(obj).encode()
        with self.lock:
            for attempt in (0, 1):
                try:
                    if self.conn is None:
                        self._connect(timeout)
                    self.conn.sock.settimeout(timeout)
                    self.conn.request("POST", path, body, {"Content-Type": "application/json"})
                    return json.loads(self.conn.getresponse().read())
                except (OSError, http.client.HTTPException, ValueError):
                    self.conn = None
                    if attempt:
                        raise

    def get(self, path, timeout=2.0):
        c = http.client.HTTPConnection(self.host, int(self.port), timeout=timeout)
        try:
            c.request("GET", path)
            return c.getresponse().status
        finally:
            c.close()


def make_server(port, routes, host="0.0.0.0"):
    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        disable_nagle_algorithm = True

        def log_message(self, *a):
            pass

        def _send(self, code, obj):
            data = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            self._send(200, {"ok": True})

        def do_POST(self):
            req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            self._send(200, routes[self.path](req))

    class Quiet(ThreadingHTTPServer):
        def handle_error(self, request, client_address):
            pass                                           # reset a fine run: normale

    srv = Quiet((host, port), H)
    srv.daemon_threads = True
    return srv


def run_estimator(a):
    ctrl, state = Peer(a.controller), {"ema": None}

    def estimate(req):
        busy(a.compute_ms)
        q = req["queues"]
        ema = state["ema"] or q
        state["ema"] = {p: 0.5 * ema[p] + 0.5 * q[p] for p in q}
        return ctrl.post("/decide", {**req, "est": state["ema"]}, timeout=a.timeout)

    make_server(a.port, {"/estimate": estimate}, a.bind).serve_forever()


def run_controller(a):
    act = Peer(a.actuator)

    def decide(req):
        busy(a.compute_ms)
        target = choose_phase(req["est"], req["phase"])
        return act.post("/actuate", {"t0": req["t0"], "deadline_ms": req["deadline_ms"],
                                     "seq": req["seq"], "target": target}, timeout=a.timeout)

    make_server(a.port, {"/decide": decide}, a.bind).serve_forever()


def run_edge(a):
    plant, lock = Intersection(a.seed), threading.Lock()
    t0_wall = time.monotonic()
    est = Peer(a.estimator)

    def actuate(req):
        act_ms = (time.monotonic() - req["t0"]) * 1000.0
        on_time = act_ms <= req["deadline_ms"]
        if on_time:
            with lock:
                plant.switch(req["target"], time.monotonic() - t0_wall)
        return {"applied": on_time, "act_ms": act_ms, "seq": req["seq"]}

    srv = make_server(a.port, {"/actuate": actuate}, a.bind)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    for _ in range(100):                                  # attende estimator pronto
        try:
            est.get("/health", timeout=1.0)
            break
        except OSError:
            time.sleep(0.2)

    stop = threading.Event()

    def plant_loop():
        k, start = 0, time.monotonic()
        while not stop.is_set():
            with lock:
                plant.step(k * DT)
            k += 1
            delay = start + k * DT - time.monotonic()
            if delay > 0:
                time.sleep(delay)

    t_plant = time.monotonic()
    threading.Thread(target=plant_loop, daemon=True).start()
    t0_wall = t_plant                                      # simulation time = time since the start
    cap = 2 * a.deadline_ms
    out, cycles = open(a.out, "w"), int(a.duration / a.period)
    cpu_prev, wall_prev = sum(os.times()[:2]), time.monotonic()
    for seq in range(cycles):
        target_t = t_plant + (seq + 1) * a.period
        time.sleep(max(0.0, target_t - time.monotonic() - 0.0))
        with lock:
            lengths, phase = plant.lengths(), plant.phase
        t0 = time.monotonic()
        try:
            r = est.post("/estimate", {"queues": lengths, "phase": phase, "t0": t0,
                                       "deadline_ms": a.deadline_ms, "seq": seq},
                         timeout=cap / 1000.0 + 1.0)
            rtt = (time.monotonic() - t0) * 1000.0
            act_ms, applied = r["act_ms"], r["applied"]
        except (OSError, http.client.HTTPException, ValueError, KeyError):
            rtt, act_ms, applied = cap, cap, False
        with lock:
            wait, queue = plant.window(time.monotonic() - t0_wall)
        cpu, wall = sum(os.times()[:2]), time.monotonic()
        out.write(json.dumps({
            "seq": seq, "t_s": round(wall - t_plant, 3), "t_epoch": round(time.time(), 3), "act_ms": round(act_ms, 3),
            "rtt_ms": round(rtt, 3), "missed": not applied, "wait_s": round(wait, 3),
            "queue": queue, "cpu_pct": round(100 * (cpu - cpu_prev) / max(wall - wall_prev, 1e-9), 2),
        }) + "\n")
        out.flush()
        cpu_prev, wall_prev = cpu, wall
    stop.set()
    out.close()


def main():
    p = argparse.ArgumentParser(prog="workload.components")
    sub = p.add_subparsers(dest="role", required=True)
    e = sub.add_parser("estimator")
    e.add_argument("--port", type=int, required=True)
    e.add_argument("--controller", required=True)
    e.add_argument("--compute-ms", type=float, default=0)
    e.add_argument("--timeout", type=float, default=2.0)
    e.add_argument("--bind", default="0.0.0.0")
    c = sub.add_parser("controller")
    c.add_argument("--port", type=int, required=True)
    c.add_argument("--actuator", required=True)
    c.add_argument("--compute-ms", type=float, default=0)
    c.add_argument("--timeout", type=float, default=2.0)
    c.add_argument("--bind", default="0.0.0.0")
    g = sub.add_parser("edge")
    g.add_argument("--port", type=int, required=True)
    g.add_argument("--estimator", required=True)
    g.add_argument("--period", type=float, default=1.0)
    g.add_argument("--deadline-ms", type=float, default=250)
    g.add_argument("--duration", type=float, required=True, help="secondi totali (warm-up incluso)")
    g.add_argument("--seed", type=int, default=0)
    g.add_argument("--out", required=True)
    g.add_argument("--bind", default="0.0.0.0")
    a = p.parse_args()
    {"estimator": run_estimator, "controller": run_controller, "edge": run_edge}[a.role](a)


if __name__ == "__main__":
    main()
