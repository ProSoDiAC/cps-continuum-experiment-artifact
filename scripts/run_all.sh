#!/usr/bin/env bash
# Sequential execution of the final experiments (RQ1 site A, RQ1 site B, RQ3, RQ2).
# WARNING: creates real VMs on SLICES-Gent. Run it only once, in tmux:
#   tmux new -d -s all 'bash scripts/run_all.sh'
# Each step uses a dedicated SLICES experiment (fom2-<step>), with its own lease; at the end of the step the experiment
# is deleted (including any leftover VMs). A failed step does not block the following ones.
# A step can be re-run by hand: runs already completed are skipped.
set -u
cd "$(dirname "$0")/.."
export PYTHONPATH=.:orchestrator
SEED=1
LOG=experiments/run_all.log
STATUS=experiments/run_all.status
: > "$STATUS"

say() { echo "[$(date '+%F %T')] $*" | tee -a "$LOG"; }

say "=== avvio run_all ==="

# --- preliminary checks: if one fails, no resource is created -------------------------------
fail=0
if [ -n "$(git status --porcelain -- orchestrator workload descriptor analysis)" ]; then
  say "ERROR: uncommitted changes in the code (code_version would be 'dirty'): commit before starting"; fail=1
fi
ssh -o BatchMode=yes -o ConnectTimeout=10 edge-host 'sudo -n tc -V >/dev/null && sudo -n systemd-run --version >/dev/null' \
  || { say "ERROR: edge-host not reachable, or sudo (tc, systemd-run) not enabled"; fail=1; }
slices experiment list >/dev/null 2>&1 || { say "ERROR: slices-cli not authenticated"; fail=1; }
[ "$fail" = 0 ] || { say "preliminary checks failed: stop"; exit 1; }
say "preliminary checks ok (code_version $(git rev-parse --short HEAD))"

# check of a step: number of completed, non-simulated runs, all with the same clean code_version
check_step() {  # $1=folder $2=expected runs
  python3 - "$1" "$2" <<'PY'
import json, glob, sys
d, expected = sys.argv[1], int(sys.argv[2])
runs = [json.load(open(p)) for p in glob.glob(d + "/traffic*/provenance.json")]
bad = [r for r in runs if r["status"] != "completed" or r["simulated"]]
dirty = [r for r in runs if "dirty" in r["code_version"]]
versions = {r["code_version"] for r in runs}
nobg = [r for r in runs if not all(v is not None for v in r["conditions"].get("background_load", {}).values())]
ok = len(runs) == expected and not bad and not dirty and len(versions) == 1
print(f"run={len(runs)}/{expected} anomale={len(bad)} dirty={len(dirty)} versioni={sorted(versions)} bg_load_mancante={len(nobg)}")
sys.exit(0 if ok else 1)
PY
}

run_step() {  # $1=name $2=descriptor $3=folder $4=lease $5=expected runs
  local name=$1 desc=$2 out=$3 lease=$4 expected=$5 exp="fom2-$1"
  say "--- step $name: $desc -> $out (lease $lease, expected $expected runs)"
  if [ -e "$out" ]; then say "SKIPPED $name: $out already exists"; echo "$name SKIPPED (folder exists)" >> "$STATUS"; return; fi
  local attempt rc res
  mkdir -p experiments/logs
  for attempt in 1 2; do          # the second attempt resumes: completed runs are skipped
    python3 -m orchestrator run "$desc" --backend slices --real --lease "$lease" --seed "$SEED" \
        --experiment "$exp" --out "$out" >> "experiments/logs/${name}.log" 2>&1
    rc=$?
    res=$(check_step "$out" "$expected" 2>&1); ok=$?
    say "step $name attempt $attempt: exit $rc; $res"
    [ "$ok" = 0 ] && break
    sleep 60
  done
  if [ "$ok" = 0 ]; then echo "$name OK ($res)" >> "$STATUS"; else echo "$name FALLITO ($res)" >> "$STATUS"; fi
  # cleanup: the experiment of the step is deleted (with any leftover VMs)
  say "leftover resources of $exp: $(SLICES_EXPERIMENT=$exp slices bi list 2>&1 | grep -cE 'edge|fog|cloud')"
  slices experiment delete "$exp" --force >> "$LOG" 2>&1 || say "warning: delete $exp failed (it expires on its own)"
}

run_step rq1-gent   experiments/descriptors/rq1_gent.yaml   experiments/rq1_gent   4h  10
run_step rq1-hybrid experiments/descriptors/rq1_hybrid.yaml experiments/rq1_hybrid 4h  10
run_step rq3        experiments/descriptors/rq3.yaml        experiments/rq3        3h  6
run_step rq2        experiments/descriptors/rq2.yaml        experiments/rq2        10h 60

say "=== fine run_all ==="
say "summary:"; cat "$STATUS" | tee -a "$LOG"
