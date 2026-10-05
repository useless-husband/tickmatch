// Find the lowest and the highest set bit of a 2**W-bit vector.
//
// Two-level: the vector is cut into groups of 2**LO bits; one small encoder
// picks the group, a second one picks the bit inside it.  The order book
// uses it to turn "which price levels hold orders" into the best price in
// one combinational step, with no sorted structure anywhere.
module tm_prienc #(
    parameter W = 8
) (
    input  wire [(1<<W)-1:0] v,
    output wire              any,
    output reg  [W-1:0]      lo,   // index of the lowest set bit (0 if none)
    output reg  [W-1:0]      hi    // index of the highest set bit (0 if none)
);
    localparam LO = W / 2;
    localparam HI = W - LO;
    localparam GN = 1 << HI;       // number of groups
    localparam GS = 1 << LO;       // bits per group

    reg [GN-1:0] grp;
    reg [HI-1:0] glo, ghi;
    reg [GS-1:0] slo, shi;
    reg [LO-1:0] blo, bhi;
    integer g, b;

    assign any = |v;

    always @* begin
        for (g = 0; g < GN; g = g + 1) grp[g] = |v[g*GS +: GS];
        glo = {HI{1'b0}};
        ghi = {HI{1'b0}};
        for (g = GN - 1; g >= 0; g = g - 1) if (grp[g]) glo = g[HI-1:0];
        for (g = 0; g < GN; g = g + 1) if (grp[g]) ghi = g[HI-1:0];
        slo = v[{glo, {LO{1'b0}}} +: GS];
        shi = v[{ghi, {LO{1'b0}}} +: GS];
        blo = {LO{1'b0}};
        bhi = {LO{1'b0}};
        for (b = GS - 1; b >= 0; b = b - 1) if (slo[b]) blo = b[LO-1:0];
        for (b = 0; b < GS; b = b + 1) if (shi[b]) bhi = b[LO-1:0];
        lo = {glo, blo};
        hi = {ghi, bhi};
    end
endmodule
