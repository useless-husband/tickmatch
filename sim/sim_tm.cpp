// Drives the Verilated tm_engine from a text stimulus and logs every output message.
//
//   sim_tm [--stall N] [--seed S] [--stats FILE] [--quiet] < stimulus > outputs
//
// Stimulus, one message per line (the same lines the golden model reads):
//   R ref_cents vi_en          start a new trading day (reset)
//   N id side mkt tif price qty | C id | D id qty | T seconds | S
// Output: "#n" before the outputs of the n-th line, "CFG err lim_dn lim_up levels" after a
// reset, and "type id id2 price qty code" for each output message.
//
// --stall N  holds out_ready low and delays in_valid at random, about N percent of cycles
//            each, so the handshakes are exercised; without it both are always ready and
//            the cycle counts in --stats are the engine's own latency.
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <map>
#include <string>
#include <vector>

#include "Vtm_engine.h"
#include "verilated.h"

static Vtm_engine* top;
static uint64_t cycles = 0;
static uint64_t rng_state = 0x9E3779B97F4A7C15ull;
static int stall = 0;
static FILE* out = stdout;
static bool quiet = false;
static uint64_t n_out = 0;

double sc_time_stamp() { return 0; }   // Verilator's runtime wants this

static uint32_t rnd() {
    rng_state ^= rng_state << 13;
    rng_state ^= rng_state >> 7;
    rng_state ^= rng_state << 17;
    return (uint32_t)(rng_state >> 32);
}

// one clock cycle; output messages are sampled just before the rising edge
static void tick() {
    top->out_ready = (stall == 0) || ((int)(rnd() % 100) >= stall);
    top->eval();
    if (top->out_valid && top->out_ready) {
        n_out++;
        if (!quiet)
            fprintf(out, "%u %u %u %u %u %u\n", (unsigned)top->out_type, (unsigned)top->out_id,
                    (unsigned)top->out_id2, (unsigned)top->out_price, (unsigned)top->out_qty,
                    (unsigned)top->out_code);
    }
    top->clk = 1;
    top->eval();
    top->clk = 0;
    top->eval();
    cycles++;
}

int main(int argc, char** argv) {
    const char* stats_path = nullptr;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--stall") && i + 1 < argc) stall = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--seed") && i + 1 < argc) rng_state ^= strtoull(argv[++i], nullptr, 10) * 0x2545F4914F6CDD1Dull;
        else if (!strcmp(argv[i], "--stats") && i + 1 < argc) stats_path = argv[++i];
        else if (!strcmp(argv[i], "--quiet")) quiet = true;
        else { fprintf(stderr, "unknown argument %s\n", argv[i]); return 2; }
    }
    Verilated::commandArgs(argc, argv);
    top = new Vtm_engine;
    top->clk = 0; top->rst = 0; top->in_valid = 0; top->out_ready = 1;
    static char obuf[1 << 20];
    setvbuf(out, obuf, _IOFBF, sizeof obuf);

    // latency histograms: class name -> cycles -> count
    std::map<std::string, std::map<uint32_t, uint64_t>> hist;
    uint64_t n_msg = 0, busy_cycles = 0, line_no = 0;
    char line[256];
    while (fgets(line, sizeof line, stdin)) {
        char op = line[0];
        if (op == '\n' || op == '#') continue;
        if (!quiet) fprintf(out, "#%llu\n", (unsigned long long)line_no);
        line_no++;
        unsigned a = 0, b = 0, c = 0, d = 0, e = 0, f = 0;
        sscanf(line + 1, "%u %u %u %u %u %u", &a, &b, &c, &d, &e, &f);
        if (op == 'R') {
            top->rst = 1; top->cfg_ref_price = a; top->cfg_vi_en = b; top->in_valid = 0;
            for (int i = 0; i < 3; i++) tick();
            top->rst = 0;
            uint64_t guard = 0;
            do { tick(); if (++guard > 2000000) { fprintf(stderr, "reset timeout\n"); return 1; } } while (!top->in_ready);
            if (!quiet) fprintf(out, "CFG %u %u %u %u\n", (unsigned)top->cfg_error, (unsigned)top->cfg_lim_dn,
                                (unsigned)top->cfg_lim_up, (unsigned)top->cfg_n_levels);
            continue;
        }
        const char* cls = "?";
        top->in_id = 0; top->in_side = 0; top->in_mkt = 0; top->in_tif = 0; top->in_price = 0; top->in_qty = 0;
        switch (op) {
            case 'N':
                top->in_op = 1; top->in_id = a; top->in_side = b; top->in_mkt = c; top->in_tif = d;
                top->in_price = e; top->in_qty = f;
                cls = c ? "new_market" : d == 0 ? "new_limit_rod" : d == 1 ? "new_limit_ioc" : "new_limit_fok";
                break;
            case 'C': top->in_op = 2; top->in_id = a; cls = "cancel"; break;
            case 'D': top->in_op = 3; top->in_id = a; top->in_qty = b; cls = "reduce"; break;
            case 'T': top->in_op = 4; top->in_price = a; cls = "time"; break;
            case 'S': top->in_op = 5; cls = "snapshot"; break;
            case 'X': top->in_op = a; top->in_id = b; cls = "bad_op"; break;
            default: fprintf(stderr, "bad stimulus line: %s", line); return 2;
        }
        while (stall && (int)(rnd() % 100) < stall) tick();     // idle gap before the message
        top->in_valid = 1;
        uint64_t guard = 0;
        top->eval();
        while (!top->in_ready) { tick(); top->eval(); if (++guard > 1000000) { fprintf(stderr, "in_ready timeout\n"); return 1; } }
        uint64_t t0 = cycles;
        uint64_t out0 = n_out;
        tick();                                                 // the message is taken on this edge
        top->in_valid = 0;
        guard = 0;
        top->eval();
        while (!top->in_ready) { tick(); top->eval(); if (++guard > 50000000) { fprintf(stderr, "engine hung on line %llu\n", (unsigned long long)line_no); return 1; } }
        uint32_t lat = (uint32_t)(cycles - t0);
        std::string key = cls;
        if (op == 'N') {                                        // split by how much matching the order caused
            uint64_t k = n_out - out0;                          // ACK + fills (+ cancel)
            key += k <= 1 ? "/0_fills" : k <= 2 ? "/1_fill" : k <= 5 ? "/2-4_fills" : "/5+_fills";
        }
        hist[key][lat]++;
        n_msg++;
        busy_cycles += lat;
    }
    if (top->err_vwap_ovf) fprintf(stderr, "err_vwap_ovf set\n");
    fflush(out);
    if (stats_path) {
        FILE* fs = fopen(stats_path, "w");
        fprintf(fs, "messages %llu\ncycles %llu\noutputs %llu\n", (unsigned long long)n_msg,
                (unsigned long long)busy_cycles, (unsigned long long)n_out);
        for (auto& h : hist)
            for (auto& kv : h.second)
                fprintf(fs, "lat %s %u %llu\n", h.first.c_str(), kv.first, (unsigned long long)kv.second);
        fclose(fs);
    }
    delete top;
    return 0;
}
