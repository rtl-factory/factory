`include "src/common/rtl/pkg/macros.vh"

module n2a_vga_timing #(
    parameter int unsigned H_VISIBLE = 640,
    parameter int unsigned H_FRONT_PORCH = 16,
    parameter int unsigned H_SYNC = 96,
    parameter int unsigned H_BACK_PORCH = 48,
    parameter int unsigned V_VISIBLE = 480,
    parameter int unsigned V_FRONT_PORCH = 10,
    parameter int unsigned V_SYNC = 2,
    parameter int unsigned V_BACK_PORCH = 33,
    parameter bit H_SYNC_ACTIVE = 1'b0,
    parameter bit V_SYNC_ACTIVE = 1'b0,
    parameter int unsigned H_COORD_WIDTH =
        $clog2(H_VISIBLE + H_FRONT_PORCH + H_SYNC + H_BACK_PORCH),
    parameter int unsigned V_COORD_WIDTH =
        $clog2(V_VISIBLE + V_FRONT_PORCH + V_SYNC + V_BACK_PORCH)
) (
    input  logic                                              clk_i,
    input  logic                                              reset_i,
    output logic [((H_COORD_WIDTH < 1) ? 1 : H_COORD_WIDTH)-1:0] pixel_x_o,
    output logic [((V_COORD_WIDTH < 1) ? 1 : V_COORD_WIDTH)-1:0] pixel_y_o,
    output logic                                              visible_o,
    output logic                                              hsync_o,
    output logic                                              vsync_o,
    output logic                                              line_start_o,
    output logic                                              line_end_o,
    output logic                                              frame_start_o,
    output logic                                              frame_end_o
);
    localparam int unsigned H_TOTAL =
        H_VISIBLE + H_FRONT_PORCH + H_SYNC + H_BACK_PORCH;
    localparam int unsigned V_TOTAL =
        V_VISIBLE + V_FRONT_PORCH + V_SYNC + V_BACK_PORCH;
    localparam int unsigned IMPLEMENTATION_H_TOTAL = (H_TOTAL < 2) ? 2 : H_TOTAL;
    localparam int unsigned IMPLEMENTATION_V_TOTAL = (V_TOTAL < 2) ? 2 : V_TOTAL;
    localparam int unsigned REQUIRED_H_COORD_WIDTH = $clog2(IMPLEMENTATION_H_TOTAL);
    localparam int unsigned REQUIRED_V_COORD_WIDTH = $clog2(IMPLEMENTATION_V_TOTAL);
    localparam logic [REQUIRED_H_COORD_WIDTH-1:0] H_VISIBLE_LIMIT =
        REQUIRED_H_COORD_WIDTH'(H_VISIBLE);
    localparam logic [REQUIRED_H_COORD_WIDTH-1:0] H_SYNC_START =
        REQUIRED_H_COORD_WIDTH'(H_VISIBLE + H_FRONT_PORCH);
    localparam logic [REQUIRED_H_COORD_WIDTH-1:0] H_SYNC_END =
        REQUIRED_H_COORD_WIDTH'(H_VISIBLE + H_FRONT_PORCH + H_SYNC);
    localparam logic [REQUIRED_V_COORD_WIDTH-1:0] V_VISIBLE_LIMIT =
        REQUIRED_V_COORD_WIDTH'(V_VISIBLE);
    localparam logic [REQUIRED_V_COORD_WIDTH-1:0] V_SYNC_START =
        REQUIRED_V_COORD_WIDTH'(V_VISIBLE + V_FRONT_PORCH);
    localparam logic [REQUIRED_V_COORD_WIDTH-1:0] V_SYNC_END =
        REQUIRED_V_COORD_WIDTH'(V_VISIBLE + V_FRONT_PORCH + V_SYNC);

    logic [REQUIRED_H_COORD_WIDTH-1:0] horizontal_count;
    logic [REQUIRED_V_COORD_WIDTH-1:0] vertical_count;
    logic [REQUIRED_H_COORD_WIDTH-1:0] horizontal_count_d;
    logic [REQUIRED_V_COORD_WIDTH-1:0] vertical_count_d;

    `ASSERT_PARAM(g_invalid_horizontal_timing,
        (H_VISIBLE >= 1) && (H_FRONT_PORCH >= 1) &&
        (H_SYNC >= 1) && (H_BACK_PORCH >= 1),
        "n2a_vga_timing horizontal segments must be at least 1")
    `ASSERT_PARAM(g_invalid_vertical_timing,
        (V_VISIBLE >= 1) && (V_FRONT_PORCH >= 1) &&
        (V_SYNC >= 1) && (V_BACK_PORCH >= 1),
        "n2a_vga_timing vertical segments must be at least 1")
    `ASSERT_PARAM(g_invalid_horizontal_width,
        H_COORD_WIDTH == REQUIRED_H_COORD_WIDTH,
        "n2a_vga_timing H_COORD_WIDTH must equal $clog2(H_TOTAL)")
    `ASSERT_PARAM(g_invalid_vertical_width,
        V_COORD_WIDTH == REQUIRED_V_COORD_WIDTH,
        "n2a_vga_timing V_COORD_WIDTH must equal $clog2(V_TOTAL)")

    always_comb begin
        horizontal_count_d = horizontal_count + 1'b1;
        vertical_count_d = vertical_count;
        if (horizontal_count == REQUIRED_H_COORD_WIDTH'(H_TOTAL - 1)) begin
            horizontal_count_d = '0;
            if (vertical_count == REQUIRED_V_COORD_WIDTH'(V_TOTAL - 1)) begin
                vertical_count_d = '0;
            end else begin
                vertical_count_d = vertical_count + 1'b1;
            end
        end
    end

    `DFF_RST(horizontal_count, horizontal_count_d, clk_i, reset_i)
    `DFF_RST(vertical_count, vertical_count_d, clk_i, reset_i)

    always_comb begin
        pixel_x_o = H_COORD_WIDTH'(horizontal_count);
        pixel_y_o = V_COORD_WIDTH'(vertical_count);
        visible_o = (horizontal_count < H_VISIBLE_LIMIT) &&
            (vertical_count < V_VISIBLE_LIMIT);

        hsync_o = ~H_SYNC_ACTIVE;
        if ((horizontal_count >= H_SYNC_START) &&
            (horizontal_count < H_SYNC_END)) begin
            hsync_o = H_SYNC_ACTIVE;
        end

        vsync_o = ~V_SYNC_ACTIVE;
        if ((vertical_count >= V_SYNC_START) &&
            (vertical_count < V_SYNC_END)) begin
            vsync_o = V_SYNC_ACTIVE;
        end

        line_start_o = !reset_i && (horizontal_count == '0);
        line_end_o = !reset_i &&
            (horizontal_count == REQUIRED_H_COORD_WIDTH'(H_TOTAL - 1));
        frame_start_o = line_start_o && (vertical_count == '0);
        frame_end_o = line_end_o &&
            (vertical_count == REQUIRED_V_COORD_WIDTH'(V_TOTAL - 1));
    end
endmodule
