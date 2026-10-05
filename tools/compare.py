#!/usr/bin/env python3
"""Compare the RTL's output log with the golden model's, message by message.

    python3 tools/compare.py expected actual [stimulus]

Exit status 0 if identical.  On the first difference, prints the input line and both
sides' outputs for that message.  A day whose reference price the engine refuses
("CFG 1 ...") is compared on the error flag only.
"""
import sys


def groups(path):
    cur, idx = None, None
    with open(path) as f:
        for line in f:
            if line[0] == "#":
                if cur is not None:
                    yield idx, cur
                idx, cur = int(line[1:]), []
            else:
                cur.append(line.rstrip("\n"))
    if cur is not None:
        yield idx, cur


def same(a, b):
    if a == b:
        return True
    return len(a) == 1 and len(b) == 1 and a[0].startswith("CFG 1") and b[0].startswith("CFG 1")


def main():
    exp, act = groups(sys.argv[1]), groups(sys.argv[2])
    n = msgs = 0
    for (i, a), (j, b) in zip(exp, act):
        if i != j or not same(a, b):
            print("MISMATCH at input line %d" % i)
            if len(sys.argv) > 3:
                with open(sys.argv[3]) as f:
                    lines = [x for x in f if x.strip() and x[0] != "#"]
                lo = max(0, i - 6)
                for k in range(lo, i + 1):
                    print("  stim[%d]: %s" % (k, lines[k].rstrip()))
            print("  expected:", a)
            print("  actual:  ", b)
            return 1
        n += 1
        msgs += len(a)
    if next(exp, None) is not None or next(act, None) is not None:
        print("MISMATCH: one log is longer than the other (after %d inputs)" % n)
        return 1
    print("identical: %d input messages, %d output messages" % (n, msgs))
    return 0


if __name__ == "__main__":
    sys.exit(main())
