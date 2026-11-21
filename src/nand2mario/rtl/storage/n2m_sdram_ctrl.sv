// SPDX-License-Identifier: MIT
// License text: LICENSE-sdram-controller.txt beside this file.
// Adapted from the nand2mario reference src/rtl/storage/n2m_sdram_ctrl.sv at
// 21614b96fdd7ff0fa101c13a504ba15c533adf34 (SHA-256
// 3180497b6542c323e236e23f1a2ccdf6822239ebb99ffa50cdd1ba37176c7f86), itself
// ported from bui-bui src/rtl/mafia/sdram/mafia_sdram_ctrl.sv (MIT, adapted
// from FPGA-MAFIA). Modification history: wiki/src/nand2mario/rtl/storage/mas.md.
// Contract: wiki/src/nand2mario/rtl/storage/mas.md.
//
// One requester, one outstanding 16-bit access, fixed 25 MHz, CL2, burst
// length one, explicit precharge after every access. Every register is in
// clk_i; dram_clk_o is the inverted system clock driven to the pin, so the
// device samples commands 20 ns after they change and read data launched at
// the device edge is captured at the following clk_i edge. The data bus is
// split into dram_dq_o/dram_dq_oe_o/dram_dq_i; the board top resolves the
// pin. Registers use the n2m system's synchronous reset.
`default_nettype none
`include "src/common/rtl/pkg/macros.vh"

module n2m_sdram_ctrl #(
  // Defaults from the contract; a fixture changes them only to prove the
  // device model's refresh deadline check.
  parameter int REFRESH_INTERVAL = n2m_sdram_pkg::SDRAM_REFRESH_INTERVAL,
  parameter int REFRESH_DEADLINE_CLOCKS = n2m_sdram_pkg::SDRAM_REFRESH_DEADLINE_CLOCKS
) (
  input  logic                                           clk_i,
  input  logic                                           reset_i,
  // Request: held until the edge that samples request_ready_o high. The
  // address is a device byte address whose bit 0 is zero; byte_enable masks
  // the written lanes (ignored on reads, which return the whole word).
  input  logic                                           request_valid_i,
  input  logic                                           request_write_i,
  input  logic [n2m_sdram_pkg::SDRAM_ADDRESS_BITS-1:0]   request_address_i,
  input  logic [n2m_sdram_pkg::SDRAM_DATA_BITS-1:0]      request_write_data_i,
  input  logic [n2m_sdram_pkg::SDRAM_BYTE_LANES-1:0]     request_byte_enable_i,
  output logic                                           request_ready_o,
  // Exactly one response pulse per accepted request, for writes too.
  output logic                                           response_valid_o,
  output logic [n2m_sdram_pkg::SDRAM_DATA_BITS-1:0]      response_data_o,
  output logic                                           idle_o,
  output logic                                           initialized_o,
  // DE10-Lite SDRAM pins.
  output logic [12:0]                                    dram_addr_o,
  output logic [1:0]                                     dram_ba_o,
  output logic                                           dram_cas_n_o,
  output logic                                           dram_cke_o,
  output logic                                           dram_clk_o,
  output logic                                           dram_cs_n_o,
  output logic                                           dram_dqml_o,
  output logic                                           dram_dqmh_o,
  output logic                                           dram_ras_n_o,
  output logic                                           dram_we_n_o,
  output logic [n2m_sdram_pkg::SDRAM_DATA_BITS-1:0]      dram_dq_o,
  output logic                                           dram_dq_oe_o,
  input  logic [n2m_sdram_pkg::SDRAM_DATA_BITS-1:0]      dram_dq_i
);
  import n2m_sdram_pkg::*;

  t_sdram_state   state, state_next;
  t_sdram_command command;
  logic           accept;
  logic           drive_data;
  logic [1:0]     write_mask;

  `ASSERT_PARAM(g_invalid_refresh, (REFRESH_INTERVAL > SDRAM_REFRESH_CLOCKS) &&
      (REFRESH_DEADLINE_CLOCKS > REFRESH_INTERVAL) && (REFRESH_DEADLINE_CLOCKS < 256),
      "n2m_sdram_ctrl refresh interval must precede a deadline below 256 clocks")

  `DFF_RST_VAL(state, state_next, clk_i, reset_i, SDRAM_RESET_STATE)

  assign idle_o = !reset_i && state.phase == SDRAM_IDLE;
  assign initialized_o = !reset_i && state.initialized;
  assign request_ready_o = idle_o && int'(state.refresh_age) < REFRESH_INTERVAL;
  assign accept = request_valid_i && request_ready_o;
  assign response_valid_o = !reset_i && state.phase == SDRAM_COMPLETE;
  assign response_data_o = state.read_data;

  assign dram_clk_o = ~clk_i;
  assign dram_cke_o = !reset_i;
  assign dram_cs_n_o = reset_i;
  assign {dram_ras_n_o, dram_cas_n_o, dram_we_n_o} = command;
  // Ref: ISSI IS42S16320D datasheet (the part n2m_sdram_pkg targets): write
  // mask latency is zero, so a write masks the lanes its byte enables leave
  // out in the WRITE clock. Why: masks stay high until initialization completes
  // so no stray write lands during power-up.
  assign write_mask = drive_data ? ~state.byte_enable : 2'b00;
  assign dram_dqml_o = !state.initialized || write_mask[0];
  assign dram_dqmh_o = !state.initialized || write_mask[1];
  assign dram_dq_o = state.write_data;
  assign dram_dq_oe_o = drive_data && !reset_i;

  always_comb begin
    state_next = state;
    command = SDRAM_NOP_CMD;
    dram_addr_o = '0;
    dram_ba_o = sdram_bank(state.address);
    drive_data = 1'b0;
    // Why: the age counts clocks since the last AUTO REFRESH once initialized and
    // saturates rather than wrapping, so an overdue refresh never looks fresh.
    if (state.initialized && state.refresh_age != 8'hff)
      state_next.refresh_age = state.refresh_age + 8'd1;

    case (state.phase)
      SDRAM_POWERUP: begin
        // Ref: ISSI IS42S16320D datasheet power-up: NOP clocks (5000 here) after
        // reset release before the first command.
        if (int'(state.count) == SDRAM_POWERUP_CLOCKS) begin
          state_next.count = '0;
          state_next.phase = SDRAM_INIT_PRECHARGE;
        end else state_next.count = state.count + 13'd1;
      end
      SDRAM_INIT_PRECHARGE, SDRAM_PRECHARGE: begin
        command = SDRAM_PRECHARGE_CMD;
        dram_addr_o[10] = 1'b1;
        state_next.count = 13'(SDRAM_TRP - 1);
        state_next.phase = SDRAM_WAIT;
        state_next.after_wait = state.initialized ? SDRAM_COMPLETE : SDRAM_INIT_REFRESH;
      end
      SDRAM_INIT_REFRESH, SDRAM_REFRESH: begin
        command = SDRAM_REFRESH_CMD;
        state_next.refresh_age = '0;
        state_next.count = 13'(SDRAM_TRC - 1);
        state_next.phase = SDRAM_WAIT;
        state_next.after_wait = SDRAM_IDLE;
        if (!state.initialized) begin
          state_next.init_refreshes = state.init_refreshes + 4'd1;
          state_next.after_wait = state.init_refreshes == 4'd7 ? SDRAM_MODE : SDRAM_INIT_REFRESH;
        end
      end
      SDRAM_MODE: begin
        command = SDRAM_MODE_CMD;
        dram_addr_o = SDRAM_MODE_VALUE;
        dram_ba_o = '0;
        state_next.count = 13'(SDRAM_TMRD - 1);
        state_next.phase = SDRAM_WAIT;
        state_next.after_wait = SDRAM_IDLE;
        state_next.initialized = 1'b1;
        state_next.refresh_age = '0;
      end
      SDRAM_WAIT: begin
        if (state.count == '0) state_next.phase = state.after_wait;
        else state_next.count = state.count - 13'd1;
      end
      SDRAM_IDLE: begin
        state_next.count = '0;
        // Why: a due refresh wins over a request on the same edge so the refresh
        // deadline is never missed.
        if (!request_ready_o) state_next.phase = SDRAM_REFRESH;
        else if (request_valid_i) begin
          state_next.address = request_address_i;
          state_next.write = request_write_i;
          state_next.write_data = request_write_data_i;
          state_next.byte_enable = request_byte_enable_i;
          state_next.phase = SDRAM_ACTIVATE;
        end
      end
      SDRAM_ACTIVATE: begin
        command = SDRAM_ACTIVATE_CMD;
        dram_addr_o = sdram_row(state.address);
        state_next.phase = SDRAM_WAIT;
        state_next.after_wait = state.write ? SDRAM_WRITE : SDRAM_READ;
        state_next.count = 13'(SDRAM_TRCD - 1);
      end
      SDRAM_READ: begin
        dram_addr_o[9:0] = sdram_column(state.address);
        if (state.count == '0) command = SDRAM_READ_CMD;
        // Ref: SDRAM datasheet CAS latency: the device drives the word after device
        // edge READ+CL-1 and holds it through edge READ+CL, so it is captured
        // at the clk_i edge ending the clock after the READ command clock.
        if (int'(state.count) == SDRAM_CAS_LATENCY - 1) begin
          state_next.read_data = dram_dq_i;
          state_next.phase = SDRAM_PRECHARGE;
          state_next.count = '0;
        end else state_next.count = state.count + 13'd1;
      end
      SDRAM_WRITE: begin
        // Ref: SDRAM datasheet: one write beat carries the WRITE command, data and
        // byte mask in the same clock.
        dram_addr_o[9:0] = sdram_column(state.address);
        command = SDRAM_WRITE_CMD;
        drive_data = 1'b1;
        state_next.phase = SDRAM_RECOVERY;
        state_next.count = 13'(SDRAM_TWR - 1);
      end
      SDRAM_RECOVERY: begin
        if (state.count == '0) state_next.phase = SDRAM_PRECHARGE;
        else state_next.count = state.count - 13'd1;
      end
      SDRAM_COMPLETE: state_next.phase = SDRAM_IDLE;
      default: state_next = SDRAM_RESET_STATE;
    endcase
  end

  // Why: the history lets the assertions check the contract's fixed latency:
  // at the edge ending contract clock k after acceptance, bit k-1 is high. The
  // contract fixes the read response at clock 9 (bit 8) and idle at clock 10
  // (bit 9), the write response at clock 10 (bit 9) and idle at clock 11 (bit 10).
  logic [10:0] read_history;
  logic [10:0] write_history;
  `DFF_RST(read_history, {read_history[9:0], accept && !request_write_i}, clk_i, reset_i)
  `DFF_RST(write_history, {write_history[9:0], accept && request_write_i}, clk_i, reset_i)

  `ASSERT(SDRAM_WORD_ALIGNED, clk_i, reset_i, accept |-> request_address_i[0] == 1'b0)
  `ASSERT(SDRAM_REQUEST_BEFORE_INIT, clk_i, reset_i, request_valid_i |-> state.initialized)
  `ASSERT(SDRAM_REQUEST_STABLE, clk_i, reset_i,
      $past(request_valid_i && !request_ready_o) && request_valid_i |->
          $stable({request_write_i, request_address_i, request_write_data_i, request_byte_enable_i}))
  `ASSERT(SDRAM_REFRESH_DEADLINE, clk_i, reset_i,
      state.initialized |-> int'(state.refresh_age) <= REFRESH_DEADLINE_CLOCKS)
  `ASSERT(SDRAM_REFRESH_IDLE_ONLY, clk_i, reset_i,
      command == SDRAM_REFRESH_CMD |-> $past(state.phase) == SDRAM_IDLE || !state.initialized)
  `ASSERT(SDRAM_RESPONSE_LATENCY, clk_i, reset_i,
      response_valid_o == (read_history[SDRAM_READ_RESPONSE_CLOCKS-1] ||
                           write_history[SDRAM_WRITE_RESPONSE_CLOCKS-1]))
  `ASSERT(SDRAM_READY_RETURNS, clk_i, reset_i,
      (!read_history[SDRAM_READ_IDLE_CLOCKS-1] || idle_o) &&
      (!write_history[SDRAM_WRITE_IDLE_CLOCKS-1] || idle_o) &&
      (!(|read_history[SDRAM_READ_IDLE_CLOCKS-2:0]) || !idle_o) &&
      (!(|write_history[SDRAM_WRITE_IDLE_CLOCKS-2:0]) || !idle_o))
  `ASSERT(SDRAM_DQ_EXCLUSIVE, clk_i, reset_i, drive_data |-> state.phase == SDRAM_WRITE)
  `ASSERT(SDRAM_COLUMN_CONSISTENT, clk_i, reset_i,
      (command != SDRAM_ACTIVATE_CMD || dram_addr_o == sdram_row(state.address)) &&
      (!(command == SDRAM_READ_CMD || command == SDRAM_WRITE_CMD) ||
          (dram_addr_o[9:0] == sdram_column(state.address) && !dram_addr_o[10])))
  `ASSERT_KNOWN(SDRAM_KNOWN_PAYLOAD, clk_i, reset_i,
      accept ? {request_write_i, request_address_i, request_write_data_i, request_byte_enable_i} : '0)
endmodule
