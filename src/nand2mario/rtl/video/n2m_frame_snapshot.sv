`default_nettype none
`include "src/common/rtl/pkg/macros.vh"

// Frame snapshot byte port of the display tier (display HAS, frame
// snapshot): answers one byte of the 5,768-byte snapshot record per accepted
// request. Header bytes (magic, version, on-screen flag and bank, frame ID)
// come from the frame bridge's registered state and are answered one cycle
// after acceptance. A pixel byte is four shades of the pinned bank, which is
// the displayed bank the scanout already selects on the frame store's read
// port: the reader presents its read index only in cycles the scanout does
// not consume (outside the image window), captures each shade one cycle
// later and answers after the fourth. A request while not pinned is answered
// with zero. The scanout's picture is therefore unchanged by a snapshot.
module n2m_frame_snapshot (
  input  logic                                 clk_i,
  input  logic                                 reset_i,
  // Byte port: at most one request outstanding, one response per request.
  input  n2m_snapshot_pkg::snapshot_request_t   request_i,
  output logic                                 ready_o,
  output n2m_snapshot_pkg::snapshot_response_t  response_o,
  // Pinned frame identity from the frame bridge.
  input  logic                                 pinned_i,
  input  logic                                 display_valid_i,
  input  n2m_display_pkg::bank_t               bank_i,
  input  n2m_snapshot_pkg::frame_id_t           frame_id_i,
  // Frame store read port, shared with the scanout: read_enable_o takes the
  // index for this cycle; the shade returns one cycle later.
  input  logic                                 scan_busy_i,
  output logic                                 read_enable_o,
  output n2m_display_pkg::frame_index_t        read_index_o,
  input  n2m_display_pkg::shade_t              read_shade_i
);
  import n2m_display_pkg::*;
  import n2m_snapshot_pkg::*;

  localparam snapshot_offset_t HEADER_END = snapshot_offset_t'(SNAPSHOT_HEADER_BYTES);
  localparam frame_snapshot_state_t STATE_RESET = '{busy: 1'b0, issued: '0, captured: '0, capture: 1'b0,
                                                    index: '0, data: '0, response: '{valid: 1'b0, data: '0}};
  frame_snapshot_state_t state_q, state_d;
  logic             accept;
  snapshot_offset_t pixel_byte;
  logic [7:0]       shifted_data;

  assign ready_o = !state_q.busy;
  assign accept = request_i.valid && ready_o;
  assign response_o = state_q.response;
  assign read_enable_o = state_q.busy && (state_q.issued != 3'd4) && !scan_busy_i;
  assign read_index_o = state_q.index + frame_index_t'(state_q.issued);
  assign pixel_byte = request_i.offset - HEADER_END;
  // Why: the captured shade enters at the top, so after four captures pixel
  // 4m+j sits in bits [2j+1:2j] as the snapshot record requires.
  assign shifted_data = {read_shade_i, state_q.data[7:2]};

  always_comb begin
    state_d = state_q;
    state_d.response.valid = 1'b0;
    state_d.capture = read_enable_o;
    if (read_enable_o) state_d.issued = state_q.issued + 3'd1;
    if (state_q.capture) begin
      state_d.data = shifted_data;
      state_d.captured = state_q.captured + 3'd1;
      if (state_q.captured == 3'd3) begin
        state_d.busy = 1'b0;
        state_d.response = '{valid: 1'b1, data: shifted_data};
      end
    end
    if (accept) begin
      if (!pinned_i) begin
        state_d.response = '{valid: 1'b1, data: 8'h00};
      end else if (request_i.offset < HEADER_END) begin
        state_d.response = '{valid: 1'b1,
                             data: snapshot_header_byte(request_i.offset[2:0], bank_i, display_valid_i, frame_id_i)};
      end else begin
        state_d.busy = 1'b1;
        state_d.issued = '0;
        state_d.captured = '0;
        state_d.index = frame_index_t'({pixel_byte, 2'b00});
      end
    end
  end

  `N2A_DFF_RST_VAL(state_q, state_d, clk_i, reset_i, STATE_RESET)

  `N2A_ASSERT(snapshot_read_only_while_pinned, clk_i, reset_i, !read_enable_o || pinned_i)
  `N2A_ASSERT(snapshot_read_index_in_frame, clk_i, reset_i,
      !read_enable_o || (read_index_o < frame_index_t'(FRAME_PIXELS)))
  `N2A_ASSERT(snapshot_offset_in_record, clk_i, reset_i,
      !request_i.valid || (request_i.offset < snapshot_offset_t'(SNAPSHOT_RECORD_BYTES)))
  `N2A_ASSERT_KNOWN(snapshot_request_known, clk_i, reset_i, request_i.valid)
endmodule
