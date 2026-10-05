#!/usr/bin/env python3
"""Run the golden model on a stimulus file; prints what sim/sim_tm must print.

    python3 tools/golden.py < stimulus > expected
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from model import engine as E  # noqa: E402


def parse(line):
    f = line.split()
    op = f[0]
    a = [int(x) for x in f[1:]]
    if op == "N":
        return ("N", a[0], a[1], a[2], a[3], a[4], a[5])
    if op == "C":
        return ("C", a[0])
    if op == "D":
        return ("D", a[0], a[1])
    if op == "T":
        return ("T", a[0])
    if op == "S":
        return ("S",)
    if op == "X":
        return ("X", a[1])
    raise ValueError(line)


def run(lines, write):
    eng = None
    n = 0
    for line in lines:
        if not line.strip() or line[0] == "#":
            continue
        write("#%d\n" % n)
        n += 1
        if line[0] == "R":
            _, ref, vi = line.split()
            eng = E.Engine(int(ref), vi_enable=bool(int(vi)))
            if eng.cfg_error:
                write("CFG 1 %s\n" % " ".join(str(x) for x in cfg_error_view(eng)))
            else:
                write("CFG 0 %d %d %d\n" % (eng.lim_dn, eng.lim_up, len(eng.levels)))
            continue
        msg = parse(line)
        if msg[0] == "X":
            eng.out = []
            if eng.cfg_error:
                eng._emit(E.REJ, code=E.REJ_CFG)
            else:
                eng._emit(E.REJ, code=E.REJ_OP)
            out = eng.out
        else:
            out = eng.submit(msg)
        for m in out:
            write(E.format_out(m) + "\n")
    return n


def cfg_error_view(eng):
    """What the RTL's configuration outputs hold when it gives up: not compared."""
    return ("-", "-", "-")


if __name__ == "__main__":
    run(sys.stdin, sys.stdout.write)
