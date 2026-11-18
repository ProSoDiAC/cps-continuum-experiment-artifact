"""Writes the metrics and logs of a run in the contract format."""
import csv


def write_metrics(path, samples):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["t_s", "metric", "value", "tier"])
        w.writerows(samples)


def write_log(path, lines):
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")
