#!/usr/bin/env python3
"""Turn the Yosys run of `make synth` into synth/report.md and synth/summary.json.

There is no place-and-route here: the clock figure is an ESTIMATE from the number of LUT
levels on the longest path (Yosys `ltp`), using a per-level delay that is stated in the
report.  Treat it as an order of magnitude, not a timing closure result.
"""
import json
import os
import re
import sys

NS_LUT = 0.6            # one LUT6 plus local routing on a 7-series -1 part (rule of thumb)
NS_CARRY = 0.1          # one CARRY4 stage in a chain
NS_FIXED = 1.0          # clock-to-out + setup


def main():
    d = sys.argv[1]
    stat = json.load(open(os.path.join(d, "stat.json")))
    cells = (stat.get("design") or stat["modules"][next(iter(stat["modules"]))])["num_cells_by_type"]
    log = open(os.path.join(d, "depth.log")).read()
    m = re.search(r"Longest topological path in \S+ \(length=(\d+)\):\n((?:\s+\d+: .*\n)+)", log)
    depth = int(m.group(1)) if m else None
    steps = m.group(2).splitlines()[1:] if m else []
    n_carry = sum(1 for x in steps if "carry4" in x.lower() or "CARRY4" in x)
    n_lut = len(steps) - n_carry
    src = []
    for x in steps:
        for f in re.findall(r"(tm_\w+\.sv:\d+)", x):
            if not src or src[-1] != f:
                src.append(f)
    lut = sum(n for c, n in cells.items() if re.match(r"LUT\d", c))
    lutram = sum(n for c, n in cells.items() if c.startswith("RAM") and not c.startswith("RAMB"))
    ff = sum(n for c, n in cells.items() if c.startswith("FD"))
    bram36 = cells.get("RAMB36E1", 0)
    bram18 = cells.get("RAMB18E1", 0)
    dsp = cells.get("DSP48E1", 0)
    carry = cells.get("CARRY4", 0)
    muxf = sum(n for c, n in cells.items() if c.startswith("MUXF"))
    period = None if depth is None else round(NS_FIXED + NS_LUT * n_lut + NS_CARRY * n_carry, 1)
    fmax = None if period is None else round(1000 / period, 1)
    summ = dict(family="Xilinx 7-series (xc7), Yosys synth_xilinx", lut=lut, lutram_cells=lutram, ff=ff,
                bram36=bram36, bram18=bram18, dsp48=dsp, carry4=carry, muxf=muxf, logic_levels=depth,
                path_lut_levels=n_lut, path_carry4=n_carry, path_source_lines=src,
                est_period_ns=period, est_fmax_mhz=fmax, ns_lut=NS_LUT, ns_carry=NS_CARRY, ns_fixed=NS_FIXED)
    os.makedirs("synth", exist_ok=True)
    json.dump(summ, open("synth/summary.json", "w"), indent=1)
    with open("synth/report.md", "w") as f:
        f.write("# Synthesis estimate\n\n`make synth`: Yosys `synth_xilinx -family xc7 -flatten`, default parameters "
                "(4096 order ids, 256 price levels, 12-bit quantities). No place and route was run.\n\n")
        f.write("| Resource | Count |\n| --- | --- |\n")
        for k, v in (("LUTs (logic)", lut), ("LUT-RAM cells (level tables)", lutram), ("Flip-flops", ff),
                     ("RAMB36E1", bram36), ("RAMB18E1", bram18), ("DSP48E1", dsp), ("CARRY4", carry),
                     ("MUXF7/F8", muxf)):
            f.write("| %s | %d |\n" % (k, v))
        f.write("\nLongest register-to-register path (second run, `-nodsp -nolutram`, Yosys `ltp -noff`): "
                "**%s cells: %d LUT/mux levels and %d CARRY4 stages**. Source lines on it: %s.\n\n"
                % (depth, n_lut, n_carry, ", ".join("`%s`" % x for x in src) or "-"))
        f.write("Timing ESTIMATE: %.1f ns fixed + %.1f ns per LUT level + %.1f ns per CARRY4 = **%s ns, about %s MHz**. "
                "These per-cell delays are a rule of thumb for a -1 speed grade, not a timing report; a real figure "
                "needs place and route on a named part.\n" % (NS_FIXED, NS_LUT, NS_CARRY, period, fmax))
        f.write("\nAll cell types:\n\n```\n")
        for c in sorted(cells):
            f.write("%-12s %d\n" % (c, cells[c]))
        f.write("```\n")
    print(open("synth/report.md").read())


if __name__ == "__main__":
    main()
