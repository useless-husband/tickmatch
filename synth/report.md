# Synthesis estimate

`make synth`: Yosys `synth_xilinx -family xc7 -flatten`, default parameters (4096 order ids, 256 price levels, 12-bit quantities). No place and route was run.

| Resource | Count |
| --- | --- |
| LUTs (logic) | 11050 |
| LUT-RAM cells (level tables) | 208 |
| Flip-flops | 2495 |
| RAMB36E1 | 3 |
| RAMB18E1 | 9 |
| DSP48E1 | 34 |
| CARRY4 | 467 |
| MUXF7/F8 | 51 |

Longest register-to-register path: **None cell levels** (Yosys `ltp -noff`, counting LUTs, carry cells, muxes, RAM and DSP cells alike).

Timing ESTIMATE: 1.0 ns fixed + 0.6 ns per level = **None ns, about None MHz**. This is a rule of thumb, not a timing report; a real figure needs place and route on a named part.

All cell types:

```
$scopeinfo   5
BUFG         1
CARRY4       467
DSP48E1      34
FDRE         2457
FDSE         38
IBUF         84
INV          242
LUT2         3010
LUT3         1016
LUT4         2110
LUT5         3416
LUT6         1498
MUXF7        39
MUXF8        12
OBUF         150
RAM256X1S    48
RAM64M       160
RAMB18E1     9
RAMB36E1     3
```
