# tickmatch -- run every target from the repository root.
#
# All paths are relative on purpose: the checkout may live in a directory whose name has
# spaces or non-ASCII characters, which GNU make and Verilator's generated makefiles cannot
# handle in absolute paths.  For the same reason the Verilated model is compiled with one
# direct compiler call instead of Verilator's own makefile.

SHELL := /bin/bash
PYTHON ?= python3
VERILATOR ?= verilator
YOSYS ?= yosys
CXX ?= c++
JOBS ?= 4

RTL := rtl/tm_engine.sv rtl/tm_daycfg.sv rtl/tm_prienc.sv
VROOT := $(shell $(VERILATOR) --getenv VERILATOR_ROOT 2>/dev/null)
VDEFS := -DVM_SC=0 -DVM_TIMING=0 -DVM_TRACE=0 -DVM_TRACE_FST=0 -DVM_TRACE_VCD=0 -DVM_TRACE_SAIF=0 -DVM_COVERAGE=0
VFLAGS := --cc -O3 --x-assign fast --x-initial fast --noassert -Irtl -Wno-fatal -Wno-lint -Wno-style
VSRC := $(VROOT)/include/verilated.cpp $(VROOT)/include/verilated_threads.cpp

# equivalence run sizes (messages per seed)
EQ_SEEDS ?= 1 2 3 4
EQ_MSGS ?= 50000
LONG_SEEDS ?= 11 12 13 14 15 16 17 18 19 20 21 22
LONG_MSGS ?= 250000

.PHONY: all lint loopcheck build test unit vectors equiv equiv-long prove mutants synth bench day report demo clean
all: test

lint:
	$(VERILATOR) --lint-only -Wall -Irtl --top-module tm_engine $(RTL)
	$(VERILATOR) --lint-only -Wall -Irtl --top-module tm_engine -GLVL_W=9 -GID_W=10 $(RTL)
	@echo "lint: verilator -Wall clean"

# ---------------------------------------------------------------- simulator
build/vm/Vtm_engine.h: $(RTL)
	rm -rf build/vm && mkdir -p build
	$(VERILATOR) $(VFLAGS) --top-module tm_engine -Mdir build/vm $(RTL)

build/sim_tm: build/vm/Vtm_engine.h sim/sim_tm.cpp
	$(CXX) -std=c++17 -O2 -w $(VDEFS) -Ibuild/vm -I$(VROOT)/include -I$(VROOT)/include/vltstd \
	  build/vm/*.cpp $(VSRC) sim/sim_tm.cpp -o $@ -lpthread

build: build/sim_tm

# ---------------------------------------------------------------- tests
# golden model: official worked examples, rules, invariant checker self-test
unit:
	$(PYTHON) -m unittest discover -s tests -t . 2>&1 | tail -4

# RTL against the golden model on TWSE's worked examples, then on directed boundary scenarios
vectors: build/sim_tm
	@mkdir -p build/eq
	$(PYTHON) tools/vectors_stim.py > build/eq/vec.stim
	$(PYTHON) tools/golden.py < build/eq/vec.stim > build/eq/vec.exp
	./build/sim_tm < build/eq/vec.stim > build/eq/vec.out
	$(PYTHON) tools/compare.py build/eq/vec.exp build/eq/vec.out build/eq/vec.stim
	$(PYTHON) tools/invariants.py build/eq/vec.stim build/eq/vec.out
	$(PYTHON) tools/directed_stim.py > build/eq/dir.stim
	$(PYTHON) tools/golden.py < build/eq/dir.stim > build/eq/dir.exp
	./build/sim_tm < build/eq/dir.stim > build/eq/dir.out
	$(PYTHON) tools/compare.py build/eq/dir.exp build/eq/dir.out build/eq/dir.stim
	$(PYTHON) tools/invariants.py build/eq/dir.stim build/eq/dir.out

# RTL against the golden model on random and adversarial flows; every output message compared
# in order, then the invariants checked on the RTL's log.  Odd seeds run with random stalls on
# both handshakes.
define EQ_RUN
	@mkdir -p build/eq
	@for s in $(1); do \
	  $(PYTHON) tools/gen.py --seed $$s --messages $(2) --stim build/eq/s$$s.stim --exp build/eq/s$$s.exp || exit 1; \
	  if [ $$((s % 2)) = 1 ]; then st="--stall 30 --seed $$s"; else st=""; fi; \
	  ./build/sim_tm $$st < build/eq/s$$s.stim > build/eq/s$$s.out || exit 1; \
	  $(PYTHON) tools/compare.py build/eq/s$$s.exp build/eq/s$$s.out build/eq/s$$s.stim || exit 1; \
	  $(PYTHON) tools/invariants.py build/eq/s$$s.stim build/eq/s$$s.out || exit 1; \
	  rm -f build/eq/s$$s.stim build/eq/s$$s.exp build/eq/s$$s.out; \
	done
endef

equiv: build/sim_tm
	$(call EQ_RUN,$(EQ_SEEDS),$(EQ_MSGS))

equiv-long: build/sim_tm
	$(call EQ_RUN,$(LONG_SEEDS),$(LONG_MSGS))

test: lint unit vectors equiv prove

# exhaustive checks on small configurations (tools/prove.py)
prove: build/sim_tm
	$(PYTHON) tools/prove.py

# planted RTL bugs must all be caught
mutants:
	$(PYTHON) tools/mutate.py

# ---------------------------------------------------------------- one simulated day, benchmark, report
day: build/sim_tm
	@mkdir -p build/day
	$(PYTHON) tools/gen.py --seed 2330 --profile day --ref 58300 --messages 120000 --stim build/day/day.stim --exp build/day/day.exp
	./build/sim_tm --stats build/day/day.stats < build/day/day.stim > build/day/day.out
	$(PYTHON) tools/compare.py build/day/day.exp build/day/day.out build/day/day.stim
	$(PYTHON) tools/invariants.py build/day/day.stim build/day/day.out

bench: build/sim_tm
	$(PYTHON) tools/bench.py

report: day
	$(PYTHON) tools/make_report.py
	@echo "report: build/report.html"

demo: report

# ---------------------------------------------------------------- synthesis estimate
# Two Yosys runs for Xilinx 7-series.  Area: the default flow (block RAM, LUT RAM, DSP).
# Depth: multipliers and level tables mapped to plain logic (-nodsp -nolutram), registers and
# block RAMs removed, then the longest combinational path (ltp).  The DSP-free run is used for
# depth because a DSP48 that absorbed pipeline registers would otherwise look combinational.
# No place and route: the clock figure in synth/report.md is an estimate.
YS_READ := read_verilog -sv -Irtl $(RTL)

# Structural check of the RTL before technology mapping: fails on any combinational loop
# (and on other netlist problems Yosys `check` knows: multiple drivers, undriven inputs used).
loopcheck:
	@mkdir -p build/synth
	$(YOSYS) -q -l build/synth/check.log -p "$(YS_READ); hierarchy -top tm_engine; proc; flatten; opt_clean; \
	  check -assert" > /dev/null 2>&1 || (grep -iE "loop|warning|error" build/synth/check.log | head -20; exit 1)
	@echo "loopcheck: no combinational loops, no multiple drivers (yosys check -assert)"

synth: loopcheck
	@mkdir -p build/synth
	$(YOSYS) -q -l build/synth/area.log -p "$(YS_READ); synth_xilinx -family xc7 -top tm_engine -flatten; \
	  tee -q -o build/synth/stat.json stat -json" > /dev/null 2>&1 || (tail -20 build/synth/area.log; exit 1)
	$(YOSYS) -q -l build/synth/depth.log -p "$(YS_READ); synth_xilinx -family xc7 -top tm_engine -flatten -nodsp -nolutram; \
	  delete t:FD* t:RAMB*; ltp -noff" > /dev/null 2>&1 || (tail -20 build/synth/depth.log; exit 1)
	$(PYTHON) tools/synth_report.py build/synth

clean:
	rm -rf build
