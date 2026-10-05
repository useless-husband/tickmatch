#!/usr/bin/env python3
"""Write the official worked examples (tests/official_vectors.py) as one stimulus file.

Each example is its own trading day; a snapshot request follows the example's order so the
resulting book is part of what the RTL must reproduce.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from tests.official_vectors import VECTORS, cents, messages  # noqa: E402


def fmt(m):
    return " ".join(str(x) for x in m)


def main():
    for v in VECTORS:
        print("R %d %d" % (cents(v["ref"]), 1 if v.get("vi_enable", True) else 0))
        setup, action = messages(v)
        for m in setup + action:
            print(fmt(m))
        print("S")


if __name__ == "__main__":
    main()
