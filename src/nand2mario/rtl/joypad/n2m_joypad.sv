`default_nettype none
`include "src/common/rtl/pkg/macros.vh"

// Game Boy joypad register P1/JOYP ($FF00) in the 25 MHz domain. Eight
// active-high button inputs (bit 0 Right, 1 Left, 2 Up, 3 Down, 4 A, 5 B,
// 6 Select, 7 Start) are sampled into a register every system edge. P1 bits
// [5:4] are the active-low row selects written by the CPU (bit 4 directions,
// bit 5 actions); bits [3:0] read the selected rows' lines active-low (a
// pressed button reads 0, an unselected row reads 1); bits [7:6] read 1.
// A selected line going from 1 to 0, whether a button was pressed or a
// select write exposed an already pressed button, pulses interrupt_o and
// wake_o for one system cycle after the edge that sampled it. Register
// accesses arrive on the peripheral port: the block decodes $FF00, answers
// in the same cycle and applies a write on the edge that samples the
// response. Debounce and board mapping belong to a later board wrapper.
module n2m_joypad (
  input  logic                               clk_i,
  input  logic                               reset_i,
  // Active-high buttons; sampled every edge, no timing relation required.
  input  logic [7:0]                         buttons_i,
  // Peripheral port from the memory map; kind is not consulted.
  /* verilator lint_off UNUSEDSIGNAL */
  input  n2m_interfaces_pkg::t_bus_request   io_request_i,
  /* verilator lint_on UNUSEDSIGNAL */
  output n2m_interfaces_pkg::t_bus_response  io_response_o,
  output logic                               io_selected_o,
  // High while any button of a selected row is pressed (CPU STOP policy).
  output logic                               selected_active_o,
  // One-cycle pulse after the edge that sampled a selected line falling:
  // the set request for IF bit 4 and the qualified STOP wake request.
  output logic                               interrupt_o,
  output logic                               wake_o
);
  localparam logic [15:0] P1_ADDRESS = 16'hFF00;

  logic [7:0] buttons_q, buttons_d;
  logic [1:0] select_q, select_d;
  logic [3:0] pressed_q, pressed_d;
  logic       event_q, event_d;
  logic       write_p1;

  assign io_selected_o = io_request_i.address == P1_ADDRESS;
  assign write_p1 = io_request_i.valid && io_selected_o && io_request_i.write;

  always_comb begin
    buttons_d = buttons_i;
    select_d = write_p1 ? io_request_i.write_data[5:4] : select_q;
  end

  // Ref: Pan Docs "Joypad Input": a row whose select bit is 0 contributes its
  // buttons. Why: lines are active-high here so the two rows OR together; the
  // read inverts them.
  always_comb begin
    pressed_q = ({4{~select_q[0]}} & buttons_q[3:0]) | ({4{~select_q[1]}} & buttons_q[7:4]);
    pressed_d = ({4{~select_d[0]}} & buttons_d[3:0]) | ({4{~select_d[1]}} & buttons_d[7:4]);
    // Ref: Pan Docs: the joypad interrupt fires when a line falls (1 then 0).
    event_d = |(pressed_d & ~pressed_q);
  end

  `DFF_RST(buttons_q, buttons_d, clk_i, reset_i)
  `DFF_RST(select_q, select_d, clk_i, reset_i)
  `DFF_RST(event_q, event_d, clk_i, reset_i)

  assign selected_active_o = |pressed_q;
  assign interrupt_o = event_q;
  assign wake_o = event_q;

  // Ref: Pan Docs "Joypad Input": lines read active-low below the stored
  // select bits and two high bits. Why: read_data is not gated by io_selected_o
  // because the system fan-out answers every other address.
  always_comb begin
    io_response_o.valid = io_request_i.valid && io_selected_o;
    io_response_o.fault = 1'b0;
    io_response_o.read_data = {2'b11, select_q, ~pressed_q};
  end

  `ASSERT_KNOWN(joypad_controls_known, clk_i, reset_i, {io_request_i.valid, buttons_i})
  `ASSERT(joypad_access_known, clk_i, reset_i,
      !io_request_i.valid || !$isunknown({io_request_i.write, io_request_i.address}))
  `ASSERT(joypad_write_known, clk_i, reset_i,
      !write_p1 || !$isunknown(io_request_i.write_data))
  `ASSERT(joypad_event_follows_fall, clk_i, reset_i,
      event_q |-> $past(|(pressed_d & ~pressed_q)))
endmodule
