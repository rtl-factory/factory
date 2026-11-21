`default_nettype none
`include "src/common/rtl/pkg/macros.vh"

// Frame bridge between the PPU pixel stream and the VGA scanout, all in the
// 25 MHz domain. Every pixel pulse is written into the writing bank at its
// row-major index. The three banks rotate through the roles writing, ready
// and displayed: the pixel that completes a frame (159, 143) makes the
// writing bank the ready bank (a ready frame nobody displayed yet is dropped
// in favour of the newer one); the raster origin of the VGA frame then
// swaps the ready bank into display when it holds a fresh complete frame,
// otherwise the displayed frame repeats. Swaps happen only at the origin,
// so a frame is never torn, and the PPU never writes the bank being scanned.
// With the LCD off no ready frame can be fresh and the first raster origin
// blanks the display, which stays blank until the next complete frame after
// the LCD is on again. Before the first complete frame the display is blank.
// Every completion is numbered (frame ID, 1 for the first since reset) and
// the displayed bank carries its frame's ID. A snapshot pin holds the
// displayed bank: while pinned, origins repeat instead of swapping, so the
// pinned bank is never recycled into the writing role, while completions,
// drops and the LCD-off blank continue unchanged.
module n2m_frame_bridge (
  input  logic                           clk_i,
  input  logic                           reset_i,
  input  logic                           lcd_enable_i,
  // PPU pixel stream: one-cycle pulses with the shade and coordinates.
  input  logic                           pixel_valid_i,
  input  n2m_display_pkg::t_shade        pixel_shade_i,
  input  logic [7:0]                     pixel_x_i,
  input  logic [7:0]                     pixel_y_i,
  // Raster origin (0, 0) of the VGA timing, one cycle per VGA frame.
  input  logic                           vga_frame_start_i,
  // Snapshot pin and release requests, one cycle each, never together.
  input  logic                           snapshot_pin_i,
  input  logic                           snapshot_release_i,
  // Frame store write port.
  output logic                           write_enable_o,
  output n2m_display_pkg::t_bank         write_bank_o,
  output n2m_display_pkg::t_frame_index  write_index_o,
  output n2m_display_pkg::t_shade        write_shade_o,
  // Bank the scanout reads and whether it holds a complete frame to show.
  output n2m_display_pkg::t_bank         display_bank_o,
  output logic                           display_valid_o,
  // Frame ID of the displayed bank and the snapshot pin level.
  output n2m_snapshot_pkg::t_frame_id     display_id_o,
  output logic                           snapshot_pinned_o,
  // One-cycle observation pulses of the bank policy, registered: each is
  // high in the cycle after the edge that applied the event.
  output logic                           frame_complete_o,
  output logic                           frame_swapped_o,
  output logic                           frame_dropped_o,
  output logic                           frame_repeated_o
);
  import n2m_display_pkg::*;

  localparam t_frame_bridge_state STATE_RESET = '{write_bank: 2'd0, ready_bank: 2'd1, display_bank: 2'd2,
                                                  ready_fresh: 1'b0, display_valid: 1'b0, pinned: 1'b0,
                                                  frame_count: '0, display_id: '0};
  t_frame_bridge_state state_q, state_d;
  logic   complete, swapped, dropped, repeated;

  assign complete = pixel_valid_i && (pixel_x_i == 8'(FRAME_WIDTH - 1)) &&
                    (pixel_y_i == 8'(FRAME_HEIGHT - 1));

  assign write_enable_o = pixel_valid_i;
  assign write_bank_o = state_q.write_bank;
  assign write_index_o = frame_index(pixel_x_i, pixel_y_i);
  assign write_shade_o = pixel_shade_i;
  assign display_bank_o = state_q.display_bank;
  assign display_valid_o = state_q.display_valid;
  assign display_id_o = state_q.display_id;
  assign snapshot_pinned_o = state_q.pinned;

  // Why: a completion is applied before a same-cycle raster origin, so the frame
  // that completes exactly at the origin is displayed by that VGA frame.
  // LCD-off invalidation is applied after the completion: a frame that
  // completes while the LCD is off is never fresh, even if the LCD is
  // enabled on the very next cycle. A pin or release is applied before a
  // same-cycle origin: the pin holds the frame displayed before that origin,
  // the release lets that origin swap.
  always_comb begin
    state_d = state_q;
    swapped = 1'b0;
    dropped = 1'b0;
    repeated = 1'b0;
    if (complete) begin
      dropped = state_q.ready_fresh;
      state_d.ready_bank = state_q.write_bank;
      state_d.write_bank = state_q.ready_bank;
      state_d.ready_fresh = 1'b1;
      state_d.frame_count = state_q.frame_count + 1'b1;
    end
    if (!lcd_enable_i) state_d.ready_fresh = 1'b0;
    if (snapshot_release_i) state_d.pinned = 1'b0;
    if (snapshot_pin_i && state_q.display_valid) state_d.pinned = 1'b1;
    if (vga_frame_start_i) begin
      if (!lcd_enable_i) begin
        state_d.display_valid = 1'b0;
      end else if (state_d.ready_fresh && !state_d.pinned) begin
        state_d.display_bank = state_d.ready_bank;
        state_d.display_id = state_d.frame_count;
        state_d.ready_bank = state_q.display_bank;
        state_d.ready_fresh = 1'b0;
        state_d.display_valid = 1'b1;
        swapped = 1'b1;
      end else begin
        repeated = state_q.display_valid;
      end
    end
  end

  `DFF_RST_VAL(state_q, state_d, clk_i, reset_i, STATE_RESET)
  `DFF_RST(frame_complete_o, complete, clk_i, reset_i)
  `DFF_RST(frame_swapped_o, swapped, clk_i, reset_i)
  `DFF_RST(frame_dropped_o, dropped, clk_i, reset_i)
  `DFF_RST(frame_repeated_o, repeated, clk_i, reset_i)

  `ASSERT(banks_are_distinct, clk_i, reset_i,
      (state_q.write_bank != state_q.ready_bank) && (state_q.ready_bank != state_q.display_bank) &&
      (state_q.display_bank != state_q.write_bank))
  `ASSERT(display_bank_changes_at_origin, clk_i, reset_i,
      (state_d.display_bank == state_q.display_bank) || vga_frame_start_i)
  `ASSERT(pinned_bank_is_held, clk_i, reset_i,
      !state_q.pinned || !state_d.pinned ||
      ((state_d.display_bank == state_q.display_bank) && (state_d.display_id == state_q.display_id)))
  `ASSERT(pin_and_release_exclusive, clk_i, reset_i, !(snapshot_pin_i && snapshot_release_i))
  `ASSERT(write_never_hits_display, clk_i, reset_i,
      !write_enable_o || (write_bank_o != display_bank_o))
  `ASSERT(pixel_in_frame, clk_i, reset_i,
      !pixel_valid_i || ((pixel_x_i < 8'(FRAME_WIDTH)) && (pixel_y_i < 8'(FRAME_HEIGHT))))
  `ASSERT_KNOWN(pixel_control_known, clk_i, reset_i, {pixel_valid_i, lcd_enable_i, vga_frame_start_i, snapshot_pin_i, snapshot_release_i})
endmodule
