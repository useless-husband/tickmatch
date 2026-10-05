#!/usr/bin/env python3
"""Latency in clock cycles per message type, and throughput (make bench).

Latency is counted by the harness from the cycle a message is taken to the cycle the engine
is ready for the next one, with the output side always ready.  It therefore includes every
output message of that input.  Results go to build/bench.json; `make synth` supplies the
clock estimate that turns cycles into time.
"""
import json
import os
import subprocess
import sys
import time

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
os.chdir(ROOT)
PY = sys.executable


def read_stats(path):
    st = {"classes": {}}
    for line in open(path):
        f = line.split()
        if f[0] == "lat":
            st["classes"].setdefault(f[1], {})[int(f[2])] = int(f[3])
        else:
            st[f[0]] = int(f[1])
    return st


def summary(h):
    n = sum(h.values())
    keys = sorted(h)

    def pct(p):
        acc = 0
        for k in keys:
            acc += h[k]
            if acc >= p * n:
                return k
        return keys[-1]
    return dict(n=n, min=keys[0], p50=pct(0.5), p99=pct(0.99), max=keys[-1],
                mean=round(sum(k * v for k, v in h.items()) / n, 2))


def run(name, gen_args):
    os.makedirs("build/bench", exist_ok=True)
    stim = "build/bench/%s.stim" % name
    subprocess.run([PY, "tools/gen.py", "--stim", stim] + gen_args, check=True, stdout=subprocess.DEVNULL)
    t0 = time.time()
    subprocess.run(["build/sim_tm", "--quiet", "--stats", "build/bench/%s.stats" % name], stdin=open(stim), check=True)
    wall = time.time() - t0
    st = read_stats("build/bench/%s.stats" % name)
    os.remove(stim)
    return dict(name=name, messages=st["messages"], cycles=st["cycles"], outputs=st["outputs"],
                cycles_per_message=round(st["cycles"] / st["messages"], 2), sim_seconds=round(wall, 2),
                sim_messages_per_second=int(st["messages"] / wall),
                classes={k: summary(v) for k, v in sorted(st["classes"].items())})


def main():
    res = [run("day", ["--seed", "2330", "--profile", "day", "--ref", "58300", "--messages", "400000"]),
           run("sweep", ["--seed", "7", "--profile", "sweep", "--ref", "58300", "--messages", "100000"]),
           run("mix", ["--seed", "9", "--profile", "mix", "--messages", "400000"])]
    json.dump(res, open("build/bench.json", "w"), indent=1)
    for r in res:
        print("%-6s %7d messages, %9d cycles, %.2f cycles/message, simulated at %d messages/s"
              % (r["name"], r["messages"], r["cycles"], r["cycles_per_message"], r["sim_messages_per_second"]))
    print("\nlatency in cycles, 'day' flow:")
    print("  %-28s %8s %5s %5s %5s %6s" % ("message", "count", "min", "p50", "p99", "max"))
    for k, s in res[0]["classes"].items():
        print("  %-28s %8d %5d %5d %5d %6d" % (k, s["n"], s["min"], s["p50"], s["p99"], s["max"]))


if __name__ == "__main__":
    main()
