"""Conversion of the rows produced by the edge component into contract samples."""


def rows_to_samples(rows, warmup_s, wanted, tier="edge"):
    samples = []
    for r in rows:
        if r["t_s"] < warmup_s:
            continue
        t = round(r["t_s"] - warmup_s, 3)
        for m, v in (("app.e2e_latency", r["act_ms"]), ("app.deadline_miss", float(r["missed"])),
                     ("cps.avg_wait_time", r["wait_s"]), ("cps.queue_length", float(r["queue"])),
                     ("app.gateway_cpu", r["cpu_pct"])):
            if m in wanted:
                samples.append((t, m, v, tier))
    return samples
