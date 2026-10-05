"""The invariant checker must accept the golden model's logs and reject tampered ones."""
import io
import os
import random
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
import gen  # noqa: E402
import invariants  # noqa: E402


def make_log(seed, messages=6000):
    stim, exp = io.StringIO(), io.StringIO()
    g = gen.Gen(seed, stim, exp)
    d = 0
    while g.n < messages:
        g.day(gen.pick_ref(g.rng), gen.PROFILES[gen.MIX_ORDER[d % 7]], 700)
        d += 1
    groups, cur = [], None
    for line in exp.getvalue().splitlines():
        if line[0] == "#":
            cur = []
            groups.append(cur)
        else:
            cur.append(line)
    return stim.getvalue().splitlines(), groups


class Checker(unittest.TestCase):
    def test_golden_logs_pass(self):
        for seed in (5, 6, 7):
            stim, groups = make_log(seed)
            days, n = invariants.check(stim, groups)
            self.assertGreater(n, 3000)

    def test_tampered_logs_fail(self):
        """Change one field of one trade, or drop one output: the checker must notice."""
        rng = random.Random(1)
        stim, groups = make_log(8)
        trades = [(i, j) for i, g in enumerate(groups) for j, m in enumerate(g) if m.startswith("3 ")]
        caught = 0
        for trial in range(60):
            i, j = rng.choice(trades)
            f = groups[i][j].split()
            saved = groups[i][j]
            kind = trial % 4
            if kind == 0:
                f[3] = str(int(f[3]) + 100000)                  # price outside the limits
            elif kind == 1:
                f[4] = str(int(f[4]) + 1)                       # one lot too many
            elif kind == 2:
                f[1], f[2] = f[2], f[1]                         # buyer and seller swapped
            else:
                f[2 if stim[i].split()[2] == "0" else 1] = "4095"   # another resting order named
            groups[i][j] = " ".join(f)
            try:
                invariants.check(stim, groups)
            except (invariants.Violation, KeyError):
                caught += 1
            groups[i][j] = saved
        self.assertEqual(caught, 60)


if __name__ == "__main__":
    unittest.main()
