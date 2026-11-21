`default_nettype none
`include "src/common/rtl/pkg/macros.vh"

// Native implementation of the pinned menu's catalogue/loader ABI. The
// external backing holds the complete library; a private 64 KiB cartridge
// store holds the current image and its snapshot bank. The copy engine is
// the external backing's only mapper-side reader. Existing host writes go
// through that backing's independent load port, while host_hold_i prevents
// this engine from starting new reads and holds the console in reset.
// System reset loses the active image but never clears stored library bytes.
module n2m_library (
  input  logic clk_i,
  input  logic reset_i,
  input  logic core_reset_i,
  input  logic host_hold_i,
  input  logic backing_initialized_i,
  input  n2m_interfaces_pkg::t_bus_request request_i,
  output logic ready_o,
  output n2m_interfaces_pkg::t_bus_response response_o,
  output n2m_cartridge_pkg::t_backing_request backing_request_o,
  input  logic backing_ready_i,
  input  n2m_cartridge_pkg::t_backing_response backing_response_i,
  output logic hold_o
);
  import n2m_library_pkg::*;
  t_state state_q, state_d;
  logic library_mode, busy, local_access, accept, local_valid_q;
  logic [7:0] local_data, local_data_q, status;
  logic bank_write, select_write, game_exit, source_response;
  logic entry_valid, last_byte;
  logic [31:0] next_crc;
  logic [16:0] copy_bytes;
  logic [20:0] copy_destination;
  logic active_load_write;
  logic [7:0] mapper_header_data;
  n2m_interfaces_pkg::t_bus_request mapper_request;
  n2m_interfaces_pkg::t_bus_response mapper_response;
  logic mapper_ready, active_ready;
  n2m_cartridge_pkg::t_backing_request active_request, active_load;
  n2m_cartridge_pkg::t_backing_response active_response;

  assign library_mode = state_q.profile == PROFILE_LOADER;
  assign busy = (state_q.phase != LIB_IDLE) && (state_q.phase != LIB_FAILED);
  assign hold_o = !state_q.image_valid || (state_q.phase == LIB_RESET);
  assign status = {busy, state_q.window_ready, backing_initialized_i && !host_hold_i,
                   5'b00000};
  assign local_access = library_mode &&
      ((request_i.address >= 16'ha000) || request_i.write ||
       (request_i.address[14] && state_q.window_copy && busy));
  assign ready_o = local_access ? 1'b1 : mapper_ready;
  assign accept = request_i.valid && ready_o && !core_reset_i;
  assign bank_write = accept && request_i.write && library_mode &&
                      (request_i.address[15:13] == 3'b001);
  assign select_write = accept && request_i.write && library_mode &&
                        (request_i.address[15:13] == 3'b011) &&
                        (request_i.write_data <= MENU_INDEX);
  assign game_exit = accept && request_i.write && !library_mode &&
                     (request_i.address[15:13] == 3'b011) &&
                     (request_i.write_data == MENU_INDEX);

  always_comb begin
    local_data = 8'hff;
    case (request_i.address)
      16'ha000: local_data = status;
      16'ha001: local_data = {2'b00, state_q.bank};
      16'ha002: local_data = state_q.result;
      16'ha003: local_data = state_q.index;
      default: local_data = 8'hff;
    endcase
    mapper_request = request_i;
    mapper_request.valid = request_i.valid && !local_access && !core_reset_i;
    response_o = mapper_response;
    if (local_valid_q) begin
      response_o.valid = 1'b1;
      response_o.fault = 1'b0;
      response_o.read_data = local_data_q;
    end
  end
  logic local_accept;
  assign local_accept = accept && local_access;
  `DFF_RST(local_valid_q, local_accept, clk_i, core_reset_i)
  `DFF_RST_EN(local_data_q, local_data, clk_i, local_accept, core_reset_i, 8'hff)

  // Why: ordinary games keep the existing mapper. Its header observation comes
  // from the catalogue's profile because the loader image's header bytes are
  // deliberately ROM-only and would select the wrong mapper.
  always_comb begin
    mapper_header_data = active_load.write_data;
    case (active_load.address)
      21'h000147: mapper_header_data = state_q.entry_profile == PROFILE_MBC1 ? 8'h01 : 8'h00;
      21'h000148: mapper_header_data = state_q.entry_profile == PROFILE_MBC1 ? 8'h01 : 8'h00;
      21'h000149: mapper_header_data = 8'h00;
      default: mapper_header_data = active_load.write_data;
    endcase
  end
  n2m_cartridge u_mapper (
    .clk_i (clk_i), .reset_i (core_reset_i),
    .request_i (mapper_request), .ready_o (mapper_ready), .response_o (mapper_response),
    .backing_request_o (active_request), .backing_ready_i (active_ready),
    .backing_response_i (active_response),
    .load_write_i (active_load_write), .load_address_i (active_load.address),
    .load_data_i (mapper_header_data)
  );
  /* verilator lint_off PINCONNECTEMPTY */
  n2m_cartridge_backing #(.ROM_BYTES (ACTIVE_ROM_BYTES), .RAM_BYTES (8192)) u_active (
    .clk_i (clk_i), .reset_i (core_reset_i),
    .request_i (active_request), .ready_o (active_ready), .response_o (active_response),
    .load_request_i (active_load), .load_ready_o (), .load_response_o (), .initialized_o ()
  );
  /* verilator lint_on PINCONNECTEMPTY */

  // Why: no CPU read shares the library source port (the active cartridge's
  // independent RAM port serves the CPU), so one outstanding byte suffices and
  // no arbiter is needed.
  assign source_response = ((state_q.phase == LIB_ENTRY_RESPONSE) ||
                            (state_q.phase == LIB_COPY_RESPONSE) ||
                            (state_q.phase == LIB_DRAIN)) && backing_response_i.valid;
  assign copy_bytes = state_q.window_copy ? 17'd16384 : state_q.entry_length[16:0];
  assign last_byte = state_q.offset == copy_bytes - 17'd1;
  assign next_crc = crc32_byte(state_q.crc, backing_response_i.read_data);
  assign copy_destination = {4'd0, state_q.offset} +
                            (state_q.window_copy ? 21'd16384 : 21'd0);
  assign active_load_write = (state_q.phase == LIB_COPY_RESPONSE) &&
                             backing_response_i.valid && !host_hold_i;
  assign entry_valid = (state_q.entry_valid == 8'h01) &&
      ((((state_q.entry_profile == PROFILE_DIRECT) || (state_q.entry_profile == PROFILE_LOADER)) &&
         (state_q.entry_length == 24'd32768)) ||
       ((state_q.entry_profile == PROFILE_MBC1) && (state_q.entry_length == 24'd65536) &&
        (state_q.target < 8'd15)));
  always_comb begin
    backing_request_o = '0;
    backing_request_o.valid = !host_hold_i && backing_initialized_i &&
        ((state_q.phase == LIB_ENTRY_REQUEST) || (state_q.phase == LIB_COPY_REQUEST));
    backing_request_o.address = state_q.source + {4'd0, state_q.offset};
    active_load = '0;
    active_load.valid = active_load_write;
    active_load.write = 1'b1;
    active_load.address = copy_destination;
    active_load.write_data = backing_response_i.read_data;
  end

  always_comb begin
    state_d = state_q;
    case (state_q.phase)
      LIB_BOOT: begin
        if (backing_initialized_i && !host_hold_i) begin
          state_d.target = MENU_INDEX;
          state_d.source = CATALOGUE_ADDRESS + 21'd512;
          state_d.offset = '0;
          state_d.phase = LIB_ENTRY_REQUEST;
        end
      end
      LIB_IDLE: begin
        if (select_write) state_d.index = request_i.write_data;
        if ((bank_write || select_write || game_exit) && !backing_initialized_i)
          state_d.result = RESULT_NOT_READY;
        else if (backing_initialized_i) begin
          if (bank_write) begin
            state_d.bank = request_i.write_data[5:0];
            state_d.source = {1'b0, request_i.write_data[5:0], 14'd0};
            state_d.offset = '0;
            state_d.window_ready = 1'b0;
            state_d.window_copy = 1'b1;
            state_d.phase = LIB_COPY_REQUEST;
          end else if (select_write || game_exit) begin
            state_d.target = game_exit ? MENU_INDEX : request_i.write_data;
            state_d.source = CATALOGUE_ADDRESS +
                (game_exit ? 21'd512 : {8'd0, request_i.write_data, 5'd0});
            state_d.offset = '0;
            state_d.window_copy = 1'b0;
            state_d.phase = LIB_ENTRY_REQUEST;
          end
        end
      end
      LIB_ENTRY_REQUEST: begin
        if (backing_request_o.valid && backing_ready_i) state_d.phase = LIB_ENTRY_RESPONSE;
      end
      LIB_ENTRY_RESPONSE: begin
        if (backing_response_i.valid) begin
          case (state_q.offset)
            17'd0: state_d.entry_valid = backing_response_i.read_data;
            17'd1: state_d.entry_profile = backing_response_i.read_data;
            17'd2: state_d.entry_length[7:0] = backing_response_i.read_data;
            17'd3: state_d.entry_length[15:8] = backing_response_i.read_data;
            17'd4: state_d.entry_crc[7:0] = backing_response_i.read_data;
            17'd5: state_d.entry_crc[15:8] = backing_response_i.read_data;
            17'd6: state_d.entry_crc[23:16] = backing_response_i.read_data;
            17'd7: state_d.entry_crc[31:24] = backing_response_i.read_data;
            17'd24: state_d.entry_length[23:16] = backing_response_i.read_data;
            default: begin end
          endcase
          state_d.offset = state_q.offset + 17'd1;
          state_d.phase = state_q.offset == 17'd31 ? LIB_CHECK : LIB_ENTRY_REQUEST;
        end
      end
      LIB_CHECK: begin
        if (!entry_valid) begin
          state_d.result = RESULT_INVALID_SLOT;
          state_d.phase = state_q.image_valid ? LIB_IDLE : LIB_FAILED;
        end else begin
          state_d.image_valid = 1'b0;
          state_d.window_ready = 1'b0;
          state_d.window_copy = 1'b0;
          state_d.source = {1'b0, state_q.target[4:0], 15'd0};
          state_d.offset = '0;
          state_d.crc = 32'hffffffff;
          state_d.phase = LIB_COPY_REQUEST;
        end
      end
      LIB_COPY_REQUEST: begin
        if (backing_request_o.valid && backing_ready_i) state_d.phase = LIB_COPY_RESPONSE;
      end
      LIB_COPY_RESPONSE: begin
        if (backing_response_i.valid) begin
          state_d.crc = next_crc;
          state_d.offset = state_q.offset + 17'd1;
          state_d.phase = LIB_COPY_REQUEST;
          if (last_byte) begin
            if (state_q.window_copy) begin
              state_d.window_ready = 1'b1;
              state_d.window_copy = 1'b0;
              state_d.phase = LIB_IDLE;
            end else if (~next_crc != state_q.entry_crc) begin
              state_d.result = RESULT_CRC_MISMATCH;
              state_d.profile = 8'd0;
              state_d.phase = LIB_FAILED;
            end else begin
              state_d.profile = state_q.entry_profile;
              state_d.result = RESULT_OK;
              state_d.reset_count = '0;
              state_d.phase = LIB_RESET;
            end
          end
        end
      end
      LIB_RESET: begin
        state_d.reset_count = state_q.reset_count + 14'd1;
        if (state_q.reset_count == RESET_CLOCKS - 14'd1) begin
          state_d.image_valid = 1'b1;
          state_d.phase = LIB_IDLE;
        end
      end
      LIB_DRAIN: begin
        if (backing_response_i.valid) state_d.phase = LIB_BOOT;
      end
      LIB_FAILED: begin end  // Why: no self-recovery; it stays failed until a host HOLD, reload and RELEASE.
      default: state_d = RESET_STATE;
    endcase
    // Why: HOLD invalidates the active image. An accepted read is drained before
    // it is abandoned, so a new host load cannot make a late response part of a
    // later image. Requests not yet accepted can be withdrawn immediately.
    if (host_hold_i) begin
      state_d.image_valid = 1'b0;
      state_d.window_ready = 1'b0;
      state_d.window_copy = 1'b0;
      if (((state_q.phase != LIB_ENTRY_RESPONSE) && (state_q.phase != LIB_COPY_RESPONSE) &&
           (state_q.phase != LIB_DRAIN)) || source_response) state_d.phase = LIB_BOOT;
      else state_d.phase = LIB_DRAIN;
    end
  end
  `DFF_RST_VAL(state_q, state_d, clk_i, reset_i, RESET_STATE)

  `ASSERT(request_known, clk_i, reset_i,
      !request_i.valid || !$isunknown(request_i))
  `ASSERT(library_response_requested, clk_i, reset_i,
      !backing_response_i.valid || (state_q.phase == LIB_ENTRY_RESPONSE) ||
      (state_q.phase == LIB_COPY_RESPONSE) || (state_q.phase == LIB_DRAIN))
  `ASSERT(no_cpu_during_image_copy, clk_i, reset_i,
      !(active_load_write && !state_q.window_copy) || core_reset_i)
  `ASSERT(window_writes_stay_high, clk_i, reset_i,
      !(active_load_write && state_q.window_copy) ||
      ((active_load.address >= 21'h004000) && (active_load.address < 21'h008000)))
endmodule
