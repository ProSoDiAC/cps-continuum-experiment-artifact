"""Two-phase intersection (NS/EW) with adaptive closed-loop control.

Simplified microscopic model, in pure Python (SUMO remains the next real step):
- Bernoulli arrivals per step (dt) with time-varying demand, per approach;
- discharge at saturation flow during green, FIFO queues to measure the waiting time;
- at every control_period the controller picks the phase with the longer queue (with hysteresis);
- the decision is applied after the cycle latency; if the deadline was missed the
  decision is discarded and the previous phase stays (stale actuation).
"""
import random
from collections import deque

DT = 0.1                    # s
SAT_FLOW = 0.5              # veh/s a verde
LOST_TIME = 2.0             # s of all-red at the phase change
DEMAND = {"NS": 0.22, "EW": 0.14}   # mean veh/s per phase
SWING = 0.6                 # relative amplitude of the demand modulation
HYSTERESIS = 3              # vehicles of advantage required to change phase
PERIOD_S = 600.0            # period of the modulation (peak hour shifting NS<->EW)


def _rate(phase, t):
    import math
    sign = 1 if phase == "NS" else -1
    return DEMAND[phase] * (1 + SWING * sign * math.sin(2 * math.pi * t / PERIOD_S))


class Intersection:
    """State of the intersection, advanced in steps of DT. Used by the simulator and by the real workload."""

    def __init__(self, seed):
        self.rng = random.Random(seed)
        self.queues = {"NS": deque(), "EW": deque()}
        self.phase, self.lost_until, self.credit = "NS", 0.0, 0.0
        self.served = []

    def lengths(self):
        return {p: len(q) for p, q in self.queues.items()}

    def switch(self, target, t):
        if target != self.phase:
            self.phase, self.lost_until, self.credit = target, t + LOST_TIME, 0.0

    def step(self, t):
        for p in self.queues:
            if self.rng.random() < _rate(p, t) * DT:
                self.queues[p].append(t)
        if t >= self.lost_until:
            self.credit += SAT_FLOW * DT
            while self.credit >= 1.0 and self.queues[self.phase]:
                self.served.append(t - self.queues[self.phase].popleft())
                self.credit -= 1.0
            self.credit = min(self.credit, 1.0)

    def window(self, t):
        """(average waiting time, vehicles in queue) over the last period; resets the served ones."""
        waiting = [t - a for q in self.queues.values() for a in q]
        w = self.served + waiting
        self.served = []
        return (sum(w) / len(w) if w else 0.0, len(waiting))


def choose_phase(lengths, phase):
    """Controller decision: changes phase only with an advantage of HYSTERESIS vehicles."""
    other = "EW" if phase == "NS" else "NS"
    return other if lengths[other] >= lengths[phase] + HYSTERESIS else phase


def simulate(n_cycles, period_s, latencies_ms, missed, seed):
    """Returns a list of (avg_wait_s, queue_len) per control cycle.

    latencies_ms[k] e missed[k] descrivono il ciclo k del loop di controllo.
    """
    plant = Intersection(seed)
    pending, out, t = None, [], 0.0
    steps_per_cycle = int(round(period_s / DT))
    for k in range(n_cycles):
        target = choose_phase(plant.lengths(), plant.phase)
        if not missed[k] and target != plant.phase:
            pending = (t + latencies_ms[k] / 1000.0, target)
        for _ in range(steps_per_cycle):
            if pending and t >= pending[0]:
                plant.switch(pending[1], t)
                pending = None
            plant.step(t)
            t += DT
        out.append(plant.window(t))
    return out
