`ifndef SYNTHESIS
// Cycle-level x16 SDRAM device for performance simulation of the real
// n2d_sdram_controller. It mirrors the controller pin-contract test: commands
// are decoded on the controller clock edge; the mode register must select
// burst length eight, sequential and CAS latency two; a READ on edge N
// returns beat k for the controller to sample on edge N+1+k; a WRITE on edge
// N takes beat k on edge N+k with that beat's DQM byte mask. Each bank tracks
// one open row; PRECHARGE closes one bank (A10 low) or all banks (A10 high).
// Every command spacing (tRCD, tRP, tRC, tRAS, tWR, tRFC, tMRD) meets the
// IS42S16320D-7 datasheet minimum rounded up to whole 25 MHz clocks (tRC and
// tRFC two, the others one), no command may interrupt a burst, and the
// controller may drive DQ only after one idle clock following the last read
// beat. Violations are
// fatal. Storage is indexed by byte address [25:1]. The controller map puts
// the row in [25:13], the x16 column in [10:1] and bank [12:11] XOR [14:13],
// so the word index is {row, bank XOR row[1:0], column}. A preload port fills
// memory only while the controller is held in reset.
module n2d_sdram_device_model (
    input  logic          clk_i,
    input  logic [12:0]   address_i,
    input  logic [1:0]    bank_i,
    input  logic          ras_n_i,
    input  logic          cas_n_i,
    input  logic          we_n_i,
    input  logic [1:0]    dqm_i,
    input  logic [15:0]   dq_i,
    input  logic          dq_oe_i,
    output logic [15:0]   dq_o,
    input  logic          preload_valid_i,
    input  logic [25:0]   preload_address_i,
    input  logic [2047:0] preload_data_i,
    input  logic [255:0]  preload_byte_enable_i
);
    localparam logic [15:0] POISON = 16'hBAD0;
    localparam logic [12:0] MODE_VALUE = 13'h023;
    // Datasheet minimums in 25 MHz clocks: tRCD/tRP 15 ns, tRAS 37 ns,
    // tWR (tDPL)/tMRD 14 ns, tRC = tRFC 60 ns (ISSI Rev. B, AC table).
    localparam longint T_RCD = 1, T_RP = 1, T_RAS = 1, T_WR = 1, T_MRD = 1;
    localparam longint T_RC = 2, T_RFC = 2;
    localparam longint TURNAROUND = 2;
    logic [15:0] memory [0:(1 << 25) - 1];
    // Why: two-state device state starts at zero without a reset, which is
    // safe because DQ is gated by read_out_q.
    bit [12:0] open_row_q [0:3];
    bit [3:0]  open_q;
    bit        mode_set_q;
    bit [24:0] read_word_q, write_word_q;
    bit [2:0]  read_left_q, write_left_q;
    bit        read_busy_q, write_busy_q;
    bit        read_out_q;
    bit [15:0] dq_q;
    // Why: the edge counter and last-command edges for the spacing checks can
    // all start at zero, because the controller's power-up NOP period keeps
    // the first command far from edge zero.
    longint    now_q;
    longint    last_active_q [0:3];
    longint    last_precharge_q [0:3];
    longint    last_refresh_q, last_mode_q;
    longint    last_write_beat_q, last_read_out_q;
    logic [24:0] word;
    logic        active, read, write, precharge, refresh, mode, nop;

    assign active    = !ras_n_i &&  cas_n_i &&  we_n_i;
    assign read      =  ras_n_i && !cas_n_i &&  we_n_i;
    assign write     =  ras_n_i && !cas_n_i && !we_n_i;
    assign precharge = !ras_n_i &&  cas_n_i && !we_n_i;
    assign refresh   = !ras_n_i && !cas_n_i &&  we_n_i;
    assign mode      = !ras_n_i && !cas_n_i && !we_n_i;
    assign nop       =  ras_n_i &&  cas_n_i &&  we_n_i;
    assign word = {open_row_q[bank_i], bank_i ^ open_row_q[bank_i][1:0], address_i[9:0]};
    assign dq_o = read_out_q ? dq_q : POISON;

    // Sequential burst order wraps inside the aligned eight-beat group.
    function automatic bit [24:0] next_beat(input bit [24:0] current);
        return {current[24:3], current[2:0] + 3'd1};
    endfunction

    always_ff @(posedge clk_i) begin
        now_q <= now_q + 1;
        // Why: this controller-edge abstraction is equivalent to the inverted
        // board clock: a READ observed at N drives beat zero after N, ready
        // for N+1, as the physical device launches after its n+CL-1 edge plus
        // tAC.
        read_out_q <= read || read_busy_q;
        dq_q <= read ? memory[word] : read_busy_q ? memory[read_word_q] : POISON;
        if (read_out_q) last_read_out_q <= now_q;
        if (read_busy_q) begin
            assert (nop) else $fatal(1, "SDRAM command interrupts a read burst");
            read_word_q <= next_beat(read_word_q);
            read_left_q <= read_left_q - 3'd1;
            if (read_left_q == 3'd0) read_busy_q <= 1'b0;
        end
        if (write_busy_q) begin
            assert (nop) else $fatal(1, "SDRAM command interrupts a write burst");
            assert (dq_oe_i) else $fatal(1, "SDRAM write beat without DQ drive");
            if (!dqm_i[0]) memory[write_word_q][7:0] <= dq_i[7:0];
            if (!dqm_i[1]) memory[write_word_q][15:8] <= dq_i[15:8];
            last_write_beat_q <= now_q;
            write_word_q <= next_beat(write_word_q);
            write_left_q <= write_left_q - 3'd1;
            if (write_left_q == 3'd0) write_busy_q <= 1'b0;
        end else if (!write) begin
            assert (!dq_oe_i) else $fatal(1, "SDRAM DQ driven outside a write burst");
        end
        if (dq_oe_i)
            assert (!read_out_q && now_q - last_read_out_q >= TURNAROUND)
                else $fatal(1, "SDRAM DQ bus turnaround violation");
        if (preload_valid_i) begin
            for (int lane = 0; lane < 256; lane++) begin
                if (preload_byte_enable_i[lane]) begin
                    if (lane[0] ^ preload_address_i[0])
                        memory[25'((preload_address_i + 26'(lane)) >> 1)][15:8] <= preload_data_i[lane*8 +: 8];
                    else
                        memory[25'((preload_address_i + 26'(lane)) >> 1)][7:0] <= preload_data_i[lane*8 +: 8];
                end
            end
        end
        if (active) begin
            assert (!open_q[bank_i]) else $fatal(1, "SDRAM ACTIVE to an open bank");
            assert (now_q - last_precharge_q[bank_i] >= T_RP) else $fatal(1, "SDRAM tRP violation");
            assert (now_q - last_active_q[bank_i] >= T_RC) else $fatal(1, "SDRAM tRC violation");
            assert (now_q - last_refresh_q >= T_RFC) else $fatal(1, "SDRAM tRFC violation");
            assert (now_q - last_mode_q >= T_MRD) else $fatal(1, "SDRAM tMRD violation");
            open_row_q[bank_i] <= address_i;
            open_q[bank_i] <= 1'b1;
            last_active_q[bank_i] <= now_q;
        end
        if (read || write) begin
            assert (mode_set_q) else $fatal(1, "SDRAM column command before mode register set");
            assert (open_q[bank_i]) else $fatal(1, "SDRAM column command without an open row");
            assert (now_q - last_active_q[bank_i] >= T_RCD) else $fatal(1, "SDRAM tRCD violation");
            assert (!address_i[10]) else $fatal(1, "SDRAM auto-precharge is not used");
            assert (address_i[2:0] == 3'b0) else $fatal(1, "SDRAM burst does not start a line");
        end
        if (read) begin
            read_busy_q <= 1'b1;
            read_word_q <= next_beat(word);
            read_left_q <= 3'd6;
        end
        if (write) begin
            assert (dq_oe_i) else $fatal(1, "SDRAM WRITE without DQ drive");
            if (!dqm_i[0]) memory[word][7:0] <= dq_i[7:0];
            if (!dqm_i[1]) memory[word][15:8] <= dq_i[15:8];
            last_write_beat_q <= now_q;
            write_busy_q <= 1'b1;
            write_word_q <= next_beat(word);
            write_left_q <= 3'd6;
        end
        if (precharge) begin
            assert (now_q - last_write_beat_q >= T_WR) else $fatal(1, "SDRAM tWR violation");
            for (int b = 0; b < 4; b++) begin
                if (address_i[10] || bank_i == 2'(b)) begin
                    if (open_q[b])
                        assert (now_q - last_active_q[b] >= T_RAS) else $fatal(1, "SDRAM tRAS violation");
                    open_q[b] <= 1'b0;
                    last_precharge_q[b] <= now_q;
                end
            end
        end
        if (refresh) begin
            assert (open_q == '0) else $fatal(1, "SDRAM REFRESH with an open row");
            for (int b = 0; b < 4; b++)
                assert (now_q - last_precharge_q[b] >= T_RP) else $fatal(1, "SDRAM refresh tRP violation");
            assert (now_q - last_refresh_q >= T_RFC) else $fatal(1, "SDRAM tRFC violation");
            last_refresh_q <= now_q;
        end
        if (mode) begin
            assert (open_q == '0) else $fatal(1, "SDRAM mode register set with an open row");
            assert (now_q - last_refresh_q >= T_RFC) else $fatal(1, "SDRAM MRS tRFC violation");
            assert (address_i == MODE_VALUE && bank_i == 2'b00)
                else $fatal(1, "SDRAM mode register is not burst-eight sequential CAS-two");
            mode_set_q <= 1'b1;
            last_mode_q <= now_q;
        end
    end
endmodule
`endif
