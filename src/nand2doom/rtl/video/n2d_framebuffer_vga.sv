`include "src/common/rtl/pkg/macros.vh"

// Double-buffered indexed framebuffer, palette and 2x VGA scanout. Two
// banks hold complete images: the CPU/host port writes and reads the back
// bank, VGA scans the front bank, and a swap request flips them at the next
// vertical blank. FULL_WIDTH selects 320x200 banks; otherwise each bank holds
// 160x200 pixels, stores only even-x bytes and scans each stored pixel as two
// source pixels (wiki/src/nand2doom/rtl/video/mas.md).
module n2d_framebuffer_vga #(
  parameter bit FULL_WIDTH = 1'b1
) (
  input  logic        clk_i,
  input  logic        reset_i,

  input  logic        framebuffer_request_valid_i,
  output logic        framebuffer_request_ready_o,
  input  logic        framebuffer_request_write_i,
  // Read-only view of the front bank instead of the back bank.
  input  logic        framebuffer_request_front_i,
  // Word-aligned byte offset; bits 1:0 are zero by contract.
  input  logic [15:0] framebuffer_request_address_i,
  // Half width stores only bytes 0 and 2; bytes 1 and 3 (odd x) are dropped.
  /* verilator lint_off UNUSEDSIGNAL */
  input  logic [31:0] framebuffer_request_write_data_i,
  input  logic [3:0]  framebuffer_request_write_strobe_i,
  /* verilator lint_on UNUSEDSIGNAL */
  output logic        framebuffer_response_valid_o,
  output logic [31:0] framebuffer_response_read_data_o,
  input  logic        swap_request_i,
  output logic        swap_pending_o,
  output logic        front_bank_o,

  input  logic        palette_request_valid_i,
  output logic        palette_request_ready_o,
  input  logic        palette_request_write_i,
  // Read-only view of the scanned (active) palette bank instead of the
  // software-visible inactive bank; never with a write.
  input  logic        palette_request_active_i,
  input  logic [7:0]  palette_request_index_i,
  // Upper write-data bits and strobes 3:2 are architecturally ignored because
  // palette entries expose a 32-bit aperture but store RGB444 in bits 11:0.
  /* verilator lint_off UNUSEDSIGNAL */
  input  logic [31:0] palette_request_write_data_i,
  input  logic [3:0]  palette_request_write_strobe_i,
  /* verilator lint_on UNUSEDSIGNAL */
  output logic        palette_response_valid_o,
  output logic [31:0] palette_response_read_data_o,
  input  logic        palette_commit_i,
  output logic        palette_commit_pending_o,
  output logic        active_palette_bank_o,

  output logic [9:0]  pixel_x_o,
  output logic [9:0]  pixel_y_o,
  output logic        visible_o,
  output logic [3:0]  vga_red_o,
  output logic [3:0]  vga_green_o,
  output logic [3:0]  vga_blue_o,
  output logic        vga_hsync_o,
  output logic        vga_vsync_o,
  output logic        frame_start_o,
  output logic        frame_end_o,
  output logic [31:0] frame_sequence_o
);
  import n2d_types_pkg::*;

  localparam int unsigned ACTIVE_WORD_COUNT = (320 * 200) / 4;
  localparam int unsigned TOP_BORDER_LINES = 40;
  localparam int unsigned BOTTOM_IMAGE_LINE = TOP_BORDER_LINES + (200 * 2);
  // Stored bytes per framebuffer word: all four at full width, bytes 0 and 2
  // (the even-x pixels) at half width.
  localparam int unsigned LANES = FULL_WIDTH ? 4 : 2;
  localparam int unsigned LANE_BITS = FULL_WIDTH ? 2 : 1;

  logic [7:0] cpu_lane_q [0:1][0:LANES-1], scan_lane_q [0:1][0:LANES-1];
  localparam fb_cpu_t CPU_RESET = '{response_valid: 1'b0, in_range: 1'b0, bank: 1'b0,
                                    front_bank: 1'b0, swap_pending: 1'b0};
  fb_cpu_t cpu_q, cpu_d;
  // Why: the scan lane of the previous scan address has a width that follows
  // FULL_WIDTH, so it is a register of its own beside the packaged
  // software-port record.
  logic [LANE_BITS-1:0] scan_lane_select_q, scan_lane_select_d;
  logic [7:0] palette_cpu_low, palette_scan_low;
  logic [3:0] palette_cpu_high, palette_scan_high;
  logic       palette_low_write, palette_high_write;
  logic       palette_port_bank;

  logic [9:0] timing_x;
  logic [9:0] timing_y;
  logic       timing_visible;
  logic       timing_hsync;
  logic       timing_vsync;
  logic       timing_frame_start;
  logic       timing_frame_end;
  logic       timing_line_start;
  logic       timing_line_end;

  logic [15:0] scan_address;
  logic [8:0]  source_x;
  logic [7:0]  source_y;
  logic [7:0]  scan_index;
  logic [13:0] cpu_word;
  logic        cpu_in_range;
  logic        cpu_bank;
  logic        vertical_blank_start;

  localparam fb_pipe_t PIPE_RESET = '{
    x_q: '0, x_qq: '0, y_q: '0, y_qq: '0, visible_q: 1'b0, visible_qq: 1'b0,
    hsync_q: 1'b1, hsync_qq: 1'b1, vsync_q: 1'b1, vsync_qq: 1'b1,
    frame_start_q: 1'b0, frame_start_qq: 1'b0, frame_end_q: 1'b0,
    frame_end_qq: 1'b0, palette_committed: 1'b0};
  fb_pipe_t pipe_q, pipe_d;
  localparam fb_pal_t PAL_RESET = '{response_valid: 1'b0, active_bank: 1'b0,
                                    commit_pending: 1'b0, committed: 1'b0};
  fb_pal_t pal_q, pal_d;
  logic [31:0] frame_sequence_q;

  assign framebuffer_request_ready_o = 1'b1;
  assign palette_request_ready_o = 1'b1;
  assign frame_sequence_o = frame_sequence_q;
  assign swap_pending_o = cpu_q.swap_pending;
  assign front_bank_o = cpu_q.front_bank;

  n2a_vga_timing u_timing (
    .clk_i(clk_i),
    .reset_i(reset_i),
    .pixel_x_o(timing_x),
    .pixel_y_o(timing_y),
    .visible_o(timing_visible),
    .hsync_o(timing_hsync),
    .vsync_o(timing_vsync),
    .line_start_o(timing_line_start),
    .line_end_o(timing_line_end),
    .frame_start_o(timing_frame_start),
    .frame_end_o(timing_frame_end)
  );

  always_comb begin
    source_x = timing_x[9:1];
    source_y = 8'((timing_y - 10'd40) >> 1);
    // Why: a row-major source pixel offset addresses the stored pixel at both
    // widths because a half-width word stores source pixels x and x+2.
    scan_address = (16'(source_y) * 16'd320) + 16'(source_x);
    cpu_word = framebuffer_request_address_i[15:2];
    cpu_in_range = 32'(cpu_word) < ACTIVE_WORD_COUNT;
    // Ref: wiki/src/nand2doom/rtl/video/mas.md "Requirements" VID-R1: writes and
    // ordinary reads use the back bank while VGA scans the front, so a displayed
    // frame never tears; the front view reads the scanned bank.
    cpu_bank = framebuffer_request_front_i ? cpu_q.front_bank : ~cpu_q.front_bank;
    // Why: on the first vertical-blank line every active pixel of the frame has
    // been fetched, so a bank or palette flip here is frame-atomic.
    vertical_blank_start = timing_line_start && (timing_y == 10'd480);
  end

  // Why: each bank has LANES byte RAMs indexed by framebuffer word; RAM index
  // k holds bank k / LANES, lane k % LANES. Two copies of each lane give the
  // software port and the scan port independent read addresses: the
  // software copy reads at its request word (single port), the scan copy
  // reads at the raster word (simple dual port). Reads are unconditional;
  // response validity masks cycles without a request.
  // Explicit generate block and genvar: Quartus rejects the inline form.
  genvar ram;
  generate
  for (ram = 0; ram < 2 * LANES; ram = ram + 1) begin : g_framebuffer
    localparam int unsigned BANK = ram / LANES;
    localparam int unsigned LANE = ram % LANES;
    localparam int unsigned BYTE = FULL_WIDTH ? LANE : 2 * LANE;
    logic write_enable;
    assign write_enable = !reset_i && framebuffer_request_valid_i &&
        framebuffer_request_write_i && !framebuffer_request_front_i &&
        (cpu_bank == 1'(BANK)) && framebuffer_request_write_strobe_i[BYTE] &&
        cpu_in_range;
    n2a_sync_ram #(.DATA_WIDTH(8), .DEPTH(16384)) u_cpu_ram (
      .clk_i(clk_i),
      .write_enable_i(write_enable),
      .address_i(cpu_word),
      .write_data_i(framebuffer_request_write_data_i[BYTE * 8 +: 8]),
      .read_data_o(cpu_lane_q[BANK][LANE])
    );
    n2a_sdp_ram #(.DATA_WIDTH(8), .DEPTH(16384)) u_scan_ram (
      .clk_i(clk_i),
      .write_enable_i(write_enable),
      .write_address_i(cpu_word),
      .write_data_i(framebuffer_request_write_data_i[BYTE * 8 +: 8]),
      .read_address_i(scan_address[15:2]),
      .read_data_o(scan_lane_q[BANK][LANE])
    );
  end
  endgenerate

  // Ref: wiki/src/nand2doom/rtl/video/mas.md "Requirements" VID-R1: the swap
  // request is retained until the first vertical-blank line, so a swap never
  // tears a frame; a request on that clock also swaps.
  always_comb begin
    cpu_d = cpu_q;
    cpu_d.response_valid = framebuffer_request_valid_i;
    if (framebuffer_request_valid_i) begin
      cpu_d.in_range = cpu_in_range;
      cpu_d.bank = cpu_bank;
    end
    scan_lane_select_d = LANE_BITS'(FULL_WIDTH ? scan_address[1:0] : {1'b0, scan_address[1]});
    if (vertical_blank_start && (cpu_q.swap_pending || swap_request_i)) begin
      cpu_d.front_bank = ~cpu_q.front_bank;
      cpu_d.swap_pending = 1'b0;
    end else if (swap_request_i) begin
      cpu_d.swap_pending = 1'b1;
    end
  end

  `N2A_DFF_RST_VAL(cpu_q, cpu_d, clk_i, reset_i, CPU_RESET)
  `N2A_DFF_RST(scan_lane_select_q, scan_lane_select_d, clk_i, reset_i)
  assign framebuffer_response_valid_o = cpu_q.response_valid;

  // Why: a half-width read returns each stored byte for both source pixels it
  // represents, so every read sees the displayed 320-wide image.
  always_comb begin
    for (int index = 0; index < 4; index++)
      framebuffer_response_read_data_o[index * 8 +: 8] = cpu_q.in_range ?
          cpu_lane_q[cpu_q.bank][FULL_WIDTH ? index : index / 2] : 8'b0;
    scan_index = scan_lane_q[cpu_q.front_bank][scan_lane_select_q];
  end

  // Why: the inactive palette is software-visible; the active one is scanned.
  // Neither bank is reset (wiki/src/nand2doom/rtl/video/mas.md): until the first commit takes
  // effect the active bank is bank 0, which software cannot have written, so
  // scanout is forced black instead, exactly the image of a black-reset bank.
  always_comb begin
    pal_d = pal_q;
    pal_d.response_valid = palette_request_valid_i;
    if (vertical_blank_start && (pal_q.commit_pending || palette_commit_i)) begin
      pal_d.active_bank = ~pal_q.active_bank;
      pal_d.commit_pending = 1'b0;
      pal_d.committed = 1'b1;
    end else if (palette_commit_i) begin
      pal_d.commit_pending = 1'b1;
    end
    palette_port_bank = palette_request_active_i ? pal_q.active_bank : ~pal_q.active_bank;
    palette_low_write = !reset_i && palette_request_valid_i && !palette_request_active_i &&
        palette_request_write_i && palette_request_write_strobe_i[0];
    palette_high_write = !reset_i && palette_request_valid_i && !palette_request_active_i &&
        palette_request_write_i && palette_request_write_strobe_i[1];
  end

  `N2A_DFF_RST_VAL(pal_q, pal_d, clk_i, reset_i, PAL_RESET)

  // Why: both banks share one block RAM per strobe lane, addressed by
  // {bank, index}: bits 7:0 under strobe 0 and bits 11:8 under strobe 1.
  // Port A reads and writes the inactive bank at the request index (or only
  // reads the active bank for the endpoint's scanned-palette view); port B
  // only reads the active bank at the scanned index, so the ports never
  // write one address while the other reads it. Reads are unconditional and
  // read-first.
  n2a_tdp_ram #(.DATA_WIDTH(8), .DEPTH(512)) u_palette_low (
    .clk_i(clk_i),
    .a_write_enable_i(palette_low_write),
    .a_address_i({palette_port_bank, palette_request_index_i}),
    .a_write_data_i(palette_request_write_data_i[7:0]),
    .a_read_data_o(palette_cpu_low),
    .b_write_enable_i(1'b0),
    .b_address_i({pal_q.active_bank, scan_index}),
    .b_write_data_i(8'b0),
    .b_read_data_o(palette_scan_low)
  );
  n2a_tdp_ram #(.DATA_WIDTH(4), .DEPTH(512)) u_palette_high (
    .clk_i(clk_i),
    .a_write_enable_i(palette_high_write),
    .a_address_i({palette_port_bank, palette_request_index_i}),
    .a_write_data_i(palette_request_write_data_i[11:8]),
    .a_read_data_o(palette_cpu_high),
    .b_write_enable_i(1'b0),
    .b_address_i({pal_q.active_bank, scan_index}),
    .b_write_data_i(4'b0),
    .b_read_data_o(palette_scan_high)
  );
  // Why: data is defined only with response_valid, as for the framebuffer
  // port, so the RAM outputs drive it without masking.
  assign palette_response_read_data_o = {20'b0, palette_cpu_high, palette_cpu_low};
  assign palette_response_valid_o = pal_q.response_valid;
  assign palette_commit_pending_o = pal_q.commit_pending;
  assign active_palette_bank_o = pal_q.active_bank;

  // Why: synchronous framebuffer read plus synchronous palette RAM read form a
  // fixed two-clock pixel pipeline, so timing and event signals take the same
  // delay to stay aligned with their pixel; the commit flag is sampled with the
  // bank that addresses the read.
  always_comb begin
    pipe_d.x_q = timing_x;
    pipe_d.x_qq = pipe_q.x_q;
    pipe_d.y_q = timing_y;
    pipe_d.y_qq = pipe_q.y_q;
    pipe_d.visible_q = timing_visible;
    pipe_d.visible_qq = pipe_q.visible_q;
    pipe_d.hsync_q = timing_hsync;
    pipe_d.hsync_qq = pipe_q.hsync_q;
    pipe_d.vsync_q = timing_vsync;
    pipe_d.vsync_qq = pipe_q.vsync_q;
    pipe_d.frame_start_q = timing_frame_start;
    pipe_d.frame_start_qq = pipe_q.frame_start_q;
    pipe_d.frame_end_q = timing_frame_end;
    pipe_d.frame_end_qq = pipe_q.frame_end_q;
    pipe_d.palette_committed = pal_q.committed;
  end

  `N2A_DFF_RST_VAL(pipe_q, pipe_d, clk_i, reset_i, PIPE_RESET)

  // Why: consuming the common line-end event as a local contract check keeps
  // scanout coupled to the shared timing definition rather than re-deriving it.
  `N2A_ASSERT_CLOCKED_FATAL_WHEN_UNGUARDED(clk_i, reset_i, timing_frame_end, timing_line_end,
      "n2d framebuffer frame end must coincide with line end")
  `N2A_ASSERT_CLOCKED_FATAL_WHEN_UNGUARDED(clk_i, reset_i, framebuffer_request_valid_i,
      framebuffer_request_address_i[1:0] == 2'b00, "n2d framebuffer requests are word aligned")

  logic [31:0] frame_sequence_next;
  logic frame_sequence_enable;
  assign frame_sequence_next = frame_sequence_q + 1'b1;
  assign frame_sequence_enable = pipe_q.frame_start_q;
  `N2A_DFF_RST_EN(frame_sequence_q, frame_sequence_next, clk_i, frame_sequence_enable, reset_i, '0)

  always_comb begin
    pixel_x_o = pipe_q.x_qq;
    pixel_y_o = pipe_q.y_qq;
    visible_o = pipe_q.visible_qq;
    vga_hsync_o = pipe_q.hsync_qq;
    vga_vsync_o = pipe_q.vsync_qq;
    frame_start_o = pipe_q.frame_start_qq;
    frame_end_o = pipe_q.frame_end_qq;
    vga_red_o = '0;
    vga_green_o = '0;
    vga_blue_o = '0;
    if (pipe_q.visible_qq && pipe_q.palette_committed &&
        (pipe_q.y_qq >= 10'(TOP_BORDER_LINES)) &&
        (pipe_q.y_qq < 10'(BOTTOM_IMAGE_LINE))) begin
      vga_red_o = palette_scan_high;
      vga_green_o = palette_scan_low[7:4];
      vga_blue_o = palette_scan_low[3:0];
    end
  end
endmodule
