`ifndef SYNTHESIS
// Self-contained native performance harness for the nand2doom product system.
// It preloads a sparse image while reset is held, starts the CPU through real
// UART Reset-PC/Resume packets, then records per-frame cycles, retirements,
// cache events, framebuffer CRCs and VGA rasters. N2D_PERF_SDRAM selects the
// real memory controller with a cycle-level SDRAM device; otherwise the
// two-cycle fast line model is used. N2D_PERF_HALF_WIDTH selects the
// half-width framebuffer and two-way D-cache geometry of the DE10-Lite;
// otherwise the full-width, four-way DE2-115 geometry is used. A frame ends at a framebuffer bank swap. All file
// paths come from plusargs.
module n2d_perf_tb;
    localparam int unsigned CLKS_PER_BIT = 25;
    localparam int unsigned FRAME_BYTES = 320 * 200;
    localparam int unsigned RASTER_CYCLES = 800 * 525;
    localparam logic [31:0] BOOT_MARKER = 32'hD00D3701;
    localparam logic [31:0] SCENE_MARKER = 32'hD00D3703;
    localparam logic [31:0] RUNTIME_FAILURE_MARKER = 32'hD00D37EE;

    bit clk_i;
    logic reset_i = 1'b1, uart_rx_i = 1'b1;
    logic sdram_force_error_i = 1'b0, ps2_clk_i = 1'b1, ps2_data_i = 1'b1;
    logic preload_mode_i = 1'b1, preload_valid_i = 1'b0;
    logic [25:0] preload_address_i = '0;
    logic [2047:0] preload_data_i = '0;
    logic [255:0] preload_byte_enable_i = '0;
    // The full public boundary is connected; the harness reads a subset.
    /* verilator lint_off UNUSEDSIGNAL */
    logic preload_ready_o, preload_response_valid_o, preload_response_error_o;
    logic uart_tx_o, halt_ack_o, observation_valid_o;
    logic [63:0] observation_order_o;
    logic [31:0] observation_pc_o, observation_instruction_o, observation_next_pc_o;
    logic [4:0] observation_rd_address_o;
    logic observation_rd_write_o;
    logic [31:0] observation_rd_write_data_o;
    logic observation_memory_valid_o, observation_memory_write_o;
    logic [31:0] observation_memory_address_o, observation_memory_read_data_o;
    logic [31:0] observation_memory_write_data_o;
    logic [3:0] observation_memory_read_mask_o, observation_memory_write_mask_o;
    logic observation_trap_o;
    logic [31:0] observation_cause_o, observation_trap_value_o;
    logic framebuffer_write_valid_o;
    logic [15:0] framebuffer_write_address_o;
    logic [31:0] framebuffer_write_data_o;
    logic [3:0] framebuffer_write_strobe_o;
    logic framebuffer_front_bank_o, framebuffer_swap_pending_o;
    logic instruction_hit_event_o, instruction_miss_event_o;
    logic data_hit_event_o, data_miss_event_o;
    logic debug_memory_request_valid_o, ps2_event_consumed_o;
    logic [31:0] system_completion_o, system_tick_o;
    logic [9:0] pixel_x_o, pixel_y_o;
    logic visible_o;
    logic [3:0] vga_red_o, vga_green_o, vga_blue_o;
    logic vga_hsync_o, vga_vsync_o, frame_start_o, frame_end_o;
    logic [31:0] frame_sequence_o;
    /* verilator lint_on UNUSEDSIGNAL */

`ifdef N2D_PERF_HALF_WIDTH
    localparam bit FULL_WIDTH = 1'b0;
    localparam string GEOMETRY = "half";
`else
    localparam bit FULL_WIDTH = 1'b1;
    localparam string GEOMETRY = "full";
`endif
`ifdef N2D_PERF_SDRAM
    n2d_sdram_system #(.FRAMEBUFFER_FULL_WIDTH(FULL_WIDTH)) u_dut (.*);
    localparam string BACKEND = "sdram";
`else
    n2d_integrated_system #(.FRAMEBUFFER_FULL_WIDTH(FULL_WIDTH)) u_dut (.*);
    localparam string BACKEND = "fast";
`endif

    always #20 clk_i = ~clk_i;

    // ------------------------------------------------------------------
    // Run state shared by the stimulus and the monitors.
    longint unsigned cycle, resume_cycle, resume_retired;
    longint unsigned max_cycles = 64'd400_000_000;
    int unsigned frames_wanted;
    int report_fd, trace_fd;
    longint unsigned trace_start, trace_end;
    // Optional compact binary memory-stream trace (see stream_record).
    int stream_fd;
    longint unsigned stream_start;
    bit stream_at_scene, stream_on;
    logic [31:0] stream_last_iline;
    bit stream_iline_valid;
`ifdef N2D_PERF_FST
    longint unsigned fst_start, fst_end;
    bit fst_started;
`endif
    string ppm_dir;
    bit running;
    // Per-bank shadow of the displayed 320x200 index image. A half-width
    // write of an even-x byte also fills the odd-x pixel it represents.
    logic [7:0] shadow [0:1][0:FRAME_BYTES-1];
    bit written [0:1][0:FRAME_BYTES-1];
    int unsigned written_count [0:1];
    bit last_front, swap_pending, swap_requested;
    longint unsigned swap_request_cycle, spin_start_cycle;
    longint unsigned swap_count, swap_wait_total, swap_spin_total, swap_guard_violations;
    longint unsigned frame_wait, frame_spin;
    bit boot_seen, scene_marker_seen, scene_done;
    int unsigned frames_after_scene, frame_index;
    longint unsigned frame_start_cycle, frame_start_retire;
    longint unsigned ihit, imiss, dhit, dmiss;
    longint unsigned frame_ihit, frame_imiss, frame_dhit, frame_dmiss;
    bit [31:0] last_marker;
    // PMON event totals from the core's registered per-ID event vector, and
    // the exclusive per-clock cycle classification. Both read only the event
    // vector, never core state encodings.
    localparam int PMON_IDS [49] = '{1, 2, 3, 4, 5, 6, 8, 9, 10, 12, 13, 14, 15, 16, 17, 18,
                                     19, 29, 30, 32, 33, 34, 64, 65, 66, 67, 68, 69, 77, 78, 79,
                                     80, 81, 82, 83, 84, 85, 86, 87, 88, 89, 90, 91, 92, 93, 94, 95, 96, 97};
    localparam int CYCLE_CLASSES = 11;
    localparam string CYCLE_NAMES [CYCLE_CLASSES] = '{"retire", "divide", "uncached_wait",
        "dmiss_wait", "load_use", "mul_use", "data_admission", "serialize", "ifetch_wait",
        "halted", "other"};
    // Root-cause split of the ifetch_wait and dmiss_wait classes: each of
    // their clocks counts in exactly one split class (see stall_split).
    localparam int SPLIT_CLASSES = 12;
    localparam string SPLIT_NAMES [SPLIT_CLASSES] = '{"ifetch_predict", "ifetch_mispredict",
        "ifetch_redirect", "ifetch_miss", "ifetch_other", "dmiss_service", "dmiss_fetch",
        "dmiss_prefetch", "dmiss_writeback", "dmiss_debug", "dmiss_refresh", "dmiss_cache"};
    longint unsigned split_count [SPLIT_CLASSES];
    longint unsigned split_frame_base [SPLIT_CLASSES];
    longint unsigned pmon_count [128];
    longint unsigned pmon_frame_base [128];
    longint unsigned cycle_count [CYCLE_CLASSES];
    longint unsigned cycle_frame_base [CYCLE_CLASSES];
    bit frame_end_seen;
    // Event vectors of the previous three clocks. A clock without a
    // retirement is attributed to the stall that removed an instruction
    // from that retirement slot: a memory-stage hold one clock earlier, an
    // execute-stage stall two clocks earlier, or execute starved by fetch
    // three clocks earlier.
    logic [127:0] events_d1, events_d2, events_d3;
    // VGA raster capture state.
    bit vga_armed, vga_active;
    string vga_label;
    int unsigned vga_count;
    bit [31:0] vga_crc;
    int vga_fd;
    bit finish_after_vga;

    function automatic logic [31:0] crc32_byte(logic [31:0] crc, logic [7:0] value);
        logic [31:0] c;
        c = crc ^ {24'h0, value};
        for (int bit_index = 0; bit_index < 8; bit_index++)
            c = c[0] ? ((c >> 1) ^ 32'hEDB88320) : (c >> 1);
        return c;
    endfunction

    function automatic logic [31:0] shadow_crc(input bit bank);
        logic [31:0] c = 32'hFFFFFFFF;
        for (int index = 0; index < FRAME_BYTES; index++) c = crc32_byte(c, shadow[bank][index]);
        return ~c;
    endfunction

    // Observation fields are zero outside a valid retirement, so the count
    // is the order of the last valid retirement plus one.
    longint unsigned last_order_plus_one;
    function automatic longint unsigned retired();
        return last_order_plus_one;
    endfunction

    `include "src/nand2doom/dv/platform/n2d_perf_state.svh"
    `include "src/nand2doom/dv/platform/n2d_perf_singletics.svh"

    // Dump the same passive FB shadow used by this harness's CRC oracle.
    task automatic dump_single_tic_pixels(input string label);
        int fd;
        if (single_enabled) begin
            fd = $fopen({ppm_dir, "/doom-", label, "-fb.bin"}, "wb");
            if (fd == 0) $fatal(1, "cannot write framebuffer pixel artifact");
            for (int index = 0; index < FRAME_BYTES; index++) $fwrite(fd, "%c", shadow[last_front][index]);
            $fclose(fd);
        end
    endtask

    // ------------------------------------------------------------------
    // Stimulus: preload, reset release, UART start.
    task automatic preload_file(input logic [25:0] base, input string path);
        int fd, count;
        logic [7:0] chunk [0:255];
        logic [25:0] address;
        fd = $fopen(path, "rb");
        if (fd == 0) $fatal(1, "cannot open preload file %s", path);
        address = base;
        forever begin
            for (int lane = 0; lane < 256; lane++) chunk[lane] = 8'h0;
            count = $fread(chunk, fd);
            if (count <= 0) break;
            @(negedge clk_i);
            preload_valid_i = 1'b1;
            preload_address_i = address;
            for (int lane = 0; lane < 256; lane++) preload_data_i[lane*8 +: 8] = chunk[lane];
            preload_byte_enable_i = (count >= 256) ? '1 : ((256'(1) << count) - 256'(1));
            address = address + 26'(count);
            if (count < 256) begin
                @(negedge clk_i);
                preload_valid_i = 1'b0;
                break;
            end
        end
        @(negedge clk_i);
        preload_valid_i = 1'b0;
        preload_byte_enable_i = '0;
        $fclose(fd);
        $fdisplay(report_fd, "preload address=0x%07h end=0x%07h path=%s", base, address, path);
    endtask

    task automatic uart_send_byte(input logic [7:0] value);
        @(negedge clk_i) uart_rx_i = 1'b0;
        repeat (CLKS_PER_BIT) @(negedge clk_i);
        for (int bit_index = 0; bit_index < 8; bit_index++) begin
            uart_rx_i = value[bit_index];
            repeat (CLKS_PER_BIT) @(negedge clk_i);
        end
        uart_rx_i = 1'b1;
        repeat (CLKS_PER_BIT) @(negedge clk_i);
    endtask

    initial begin
        string preload_list, uart_list, report_path, trace_path, path;
`ifdef N2D_PERF_FST
        string fst_path;
`endif
        int list_fd;
        logic [31:0] tokens [$];
        logic [25:0] base;
        logic [31:0] value;
        if (!$value$plusargs("preload=%s", preload_list)) $fatal(1, "+preload=<list> is required");
        if (!$value$plusargs("uart=%s", uart_list)) $fatal(1, "+uart=<hex byte list> is required");
        if (!$value$plusargs("report=%s", report_path)) $fatal(1, "+report=<path> is required");
        void'($value$plusargs("frames=%d", frames_wanted));
        void'($value$plusargs("max_cycles=%d", max_cycles));
        void'($value$plusargs("ppm_dir=%s", ppm_dir));
        report_fd = $fopen(report_path, "w");
        if (report_fd == 0) $fatal(1, "cannot open report %s", report_path);
        if ($value$plusargs("trace=%s", trace_path)) begin
            trace_fd = $fopen(trace_path, "w");
            void'($value$plusargs("trace_start=%d", trace_start));
            void'($value$plusargs("trace_end=%d", trace_end));
        end
        if ($value$plusargs("stream=%s", path)) begin
            stream_fd = $fopen(path, "wb");
            if (stream_fd == 0) $fatal(1, "cannot open stream %s", path);
            // Without +stream_start the stream starts at the scene marker.
            stream_at_scene = !$value$plusargs("stream_start=%d", stream_start);
            $fwrite(stream_fd, "N2DMST01");
        end
`ifdef N2D_PERF_FST
        if ($value$plusargs("fst=%s", fst_path)) begin
            if (!$value$plusargs("fst_start=%d", fst_start) || !$value$plusargs("fst_end=%d", fst_end)
                    || fst_end <= fst_start)
                $fatal(1, "+fst requires +fst_start < +fst_end absolute cycles");
            $dumpfile(fst_path);
        end
`endif
        $fdisplay(report_fd, "start backend=%s frames=%0d max_cycles=%0d", BACKEND, frames_wanted, max_cycles);
        repeat (4) @(negedge clk_i);
        list_fd = $fopen(preload_list, "r");
        if (list_fd == 0) $fatal(1, "cannot open preload list %s", preload_list);
        while ($fscanf(list_fd, "%h %s\n", base, path) == 2) preload_file(base, path);
        $fclose(list_fd);
        @(negedge clk_i);
        preload_mode_i = 1'b0;
        @(negedge clk_i);
        reset_i = 1'b0;
        // SDRAM power-up initialization takes 5,000+ clocks on the real path.
        repeat (8000) @(negedge clk_i);
        list_fd = $fopen(uart_list, "r");
        if (list_fd == 0) $fatal(1, "cannot open UART list %s", uart_list);
        while ($fscanf(list_fd, "%h\n", value) == 1) tokens.push_back(value);
        $fclose(list_fd);
        // Tokens are packet bytes; 0x100 is a response gap. The run starts
        // when the final packet (Resume) has been fully transmitted.
        foreach (tokens[index]) begin
            if (tokens[index] == 32'h100) begin
                if (index == tokens.size() - 1) break;
                repeat (3000) @(negedge clk_i);
            end else uart_send_byte(tokens[index][7:0]);
        end
        resume_cycle = cycle;
        resume_retired = retired();
        frame_start_cycle = cycle;
        frame_start_retire = resume_retired;
        running = 1;
        $fdisplay(report_fd, "resumed cycle=%0d retired=%0d", cycle, resume_retired);
    end

    // ------------------------------------------------------------------
    // Monitors sample on the falling edge, after every rising-edge update.
    // They are sequential testbench code, so blocking updates are intended.
    /* verilator lint_off BLKSEQ */
    always @(posedge clk_i) cycle <= cycle + 1;

    // Exclusive per-clock class: the first matching rule wins. Only the
    // classified event bits are read from the full vectors.
    /* verilator lint_off UNUSEDSIGNAL */
    function automatic int cycle_class(input logic [127:0] e, input logic [127:0] d1,
                                       input logic [127:0] d2, input logic [127:0] d3);
        if (e[2]) return 0;              // INSTRET
        if (d2[6]) return 1;             // MULDIV_HOLD in execute
        if (d1[67]) return 2;            // UNCACHED_WAIT (subset of DATA_HOLD)
        if (d1[4]) return 3;             // DATA_HOLD on a D-cache line
        if (d2[5]) return 4;             // LOAD_USE_HOLD
        if (d2[69]) return 5;            // MUL_USE_HOLD
        if (d2[8]) return 6;             // DATA_ADMISSION_HOLD
        if (d2[68]) return 7;            // SYSTEM_SERIALIZE_HOLD
        if (d3[3]) return 8;             // IFETCH_WAIT (includes redirect refill)
        if (e[76]) return 9;             // DEBUG_HALTED
        return 10;
    endfunction

    // Split of an ifetch_wait clock (IFETCH_WAIT cause events 83..86 three
    // clocks earlier) or a dmiss_wait clock (the line-memory owner events
    // 87..91, else MEM_BACKPRESSURE, one clock earlier, like DATA_HOLD).
    function automatic int stall_split(input int cycle_class_index, input logic [127:0] d1,
                                       input logic [127:0] d3);
        if (cycle_class_index == 8) begin
            if (d3[83]) return 0;        // correctly predicted transfer bubble
            if (d3[84]) return 1;        // mispredict refill
            if (d3[85]) return 2;        // trap, MRET or resume refill
            if (d3[86]) return 3;        // fetch supply: I-miss or fill
            return 4;
        end
        if (d1[88]) return 5;            // own demand line read in service
        if (d1[87]) return 6;            // behind an I-cache line read
        if (d1[89]) return 7;            // behind a D-cache prefetch
        if (d1[90]) return 8;            // behind a D-cache line write
        if (d1[91]) return 9;            // behind the debug master
        if (d1[33]) return 10;           // MEM_BACKPRESSURE: SDRAM refresh
        return 11;                       // D-cache lookup, request and install
    endfunction
    /* verilator lint_on UNUSEDSIGNAL */

    task automatic report_pmon(input string scope, input int index, input bit delta);
        string line;
        line = $sformatf("pmon scope=%s index=%0d", scope, index);
        foreach (PMON_IDS[k])
            line = {line, $sformatf(" e%0d=%0d", PMON_IDS[k],
                pmon_count[PMON_IDS[k]] - (delta ? pmon_frame_base[PMON_IDS[k]] : 0))};
        $fdisplay(report_fd, "%s", line);
        line = $sformatf("cycles scope=%s index=%0d", scope, index);
        for (int k = 0; k < CYCLE_CLASSES; k++)
            line = {line, $sformatf(" %s=%0d", CYCLE_NAMES[k],
                cycle_count[k] - (delta ? cycle_frame_base[k] : 0))};
        $fdisplay(report_fd, "%s", line);
        line = $sformatf("split scope=%s index=%0d", scope, index);
        for (int k = 0; k < SPLIT_CLASSES; k++)
            line = {line, $sformatf(" %s=%0d", SPLIT_NAMES[k],
                split_count[k] - (delta ? split_frame_base[k] : 0))};
        $fdisplay(report_fd, "%s", line);
    endtask

    task automatic finish_run(input string status);
        report_pmon("total", 0, 0);
        $fdisplay(report_fd, "swap geometry=%s count=%0d wait=%0d spin=%0d guard_violations=%0d",
                  GEOMETRY, swap_count, swap_wait_total, swap_spin_total, swap_guard_violations);
`ifdef N2D_PERF_FST
        if (fst_end != 0 && !fst_started) status = "fst-window-not-reached";
`endif
        $fdisplay(report_fd, "done status=%s cycle=%0d run_cycles=%0d retired=%0d ihit=%0d imiss=%0d dhit=%0d dmiss=%0d",
                  status, cycle, cycle - resume_cycle, retired(), ihit, imiss, dhit, dmiss);
        $fclose(report_fd);
        if (trace_fd != 0) $fclose(trace_fd);
        if (stream_fd != 0) $fclose(stream_fd);
        if (status != "pass" && status != "fst-window") $fatal(1, "nand2doom perf harness: %s", status);
        $finish;
    endtask

    always @(negedge clk_i) begin
        observe_game_state();
        observe_single_tic_calls();
        if (observation_valid_o) last_order_plus_one = observation_order_o + 64'd1;
        if (running) begin
            if (instruction_hit_event_o) ihit++;
            if (instruction_miss_event_o) imiss++;
            if (data_hit_event_o) dhit++;
            if (data_miss_event_o) dmiss++;
            begin
                logic [127:0] events;
                int cycle_class_index;
                events = u_dut.u_system.pmon_events;
                foreach (PMON_IDS[k]) if (events[PMON_IDS[k]]) pmon_count[PMON_IDS[k]]++;
                cycle_class_index = cycle_class(events, events_d1, events_d2, events_d3);
                cycle_count[cycle_class_index]++;
                if (cycle_class_index == 3 || cycle_class_index == 8)
                    split_count[stall_split(cycle_class_index, events_d1, events_d3)]++;
                events_d3 = events_d2;
                events_d2 = events_d1;
                events_d1 = events;
            end
            if (observation_valid_o && observation_trap_o) begin
                $fdisplay(report_fd, "trap cycle=%0d pc=0x%08h cause=0x%08h value=0x%08h",
                          cycle, observation_pc_o, observation_cause_o, observation_trap_value_o);
                finish_run("trap");
            end
            if (system_completion_o != last_marker) begin
                last_marker = system_completion_o;
                $fdisplay(report_fd, "marker cycle=%0d value=0x%08h retired=%0d",
                          cycle - resume_cycle, system_completion_o, retired());
                if (system_completion_o == BOOT_MARKER) boot_seen = 1;
                if (system_completion_o == SCENE_MARKER) scene_marker_seen = 1;
                if (system_completion_o == RUNTIME_FAILURE_MARKER
                        || system_completion_o[31:12] == 20'hD00DE)
                    finish_run("runtime-failure-marker");
            end
            // Bank swap: the frame that was just drawn becomes the front
            // bank. Writes sampled on this edge commit with the new front
            // bank, so the swap is handled first.
            swap_pending = framebuffer_swap_pending_o;
            if (framebuffer_front_bank_o != last_front) begin
                last_front = framebuffer_front_bank_o;
                swap_count++;
                frame_wait = swap_requested ? cycle - swap_request_cycle : 0;
                frame_spin = (spin_start_cycle != 0) ? cycle - spin_start_cycle : 0;
                swap_wait_total += frame_wait;
                swap_spin_total += frame_spin;
                swap_requested = 0;
                spin_start_cycle = 0;
                $fdisplay(report_fd,
                    "frame index=%0d start=%0d end=%0d retired_start=%0d retired_end=%0d crc=0x%08h ihit=%0d imiss=%0d dhit=%0d dmiss=%0d after_scene=%0d wait=%0d spin=%0d",
                    frame_index, frame_start_cycle - resume_cycle, cycle - resume_cycle,
                    frame_start_retire, retired(), shadow_crc(last_front), ihit - frame_ihit,
                    imiss - frame_imiss, dhit - frame_dhit, dmiss - frame_dmiss, scene_done,
                    frame_wait, frame_spin);
                if (stream_on)
                    stream_record(8'd8, 8'(scene_done), 32'(frame_index), 32'(retired()), 32'h0);
                // Per-frame PMON deltas run end to end between swaps,
                // matching the cycles-per-frame interval.
                if (frame_end_seen)
                    report_pmon(scene_done ? "gameplay" : "frame", frame_index, 1);
                frame_end_seen = 1;
                foreach (pmon_count[k]) pmon_frame_base[k] = pmon_count[k];
                foreach (cycle_count[k]) cycle_frame_base[k] = cycle_count[k];
                foreach (split_count[k]) split_frame_base[k] = split_count[k];
                frame_start_cycle = cycle;
                frame_start_retire = retired();
                frame_ihit = ihit; frame_imiss = imiss; frame_dhit = dhit; frame_dmiss = dmiss;
                report_game_state("frame", frame_index);
                if (scene_done) dump_single_tic_pixels($sformatf("frame-%03d", frames_after_scene + 1));
                frame_index++;
                if (scene_done) begin
                    // Capture every gameplay frame's next raster; a frame
                    // is millions of cycles, far longer than one raster.
                    frames_after_scene++;
                    if (!vga_armed && !vga_active) begin
                        vga_armed = 1;
                        vga_label = $sformatf("frame-%03d", frames_after_scene);
                        finish_after_vga = (frames_after_scene >= frames_wanted);
                    end
                end
            end
            if (u_dut.u_system.framebuffer_swap_request && !swap_requested) begin
                swap_requested = 1;
                swap_request_cycle = cycle;
            end
            // Software spins on the swap status (video control 0x008) only
            // when it needs the back bank before the swap completed.
            if (swap_pending && spin_start_cycle == 0 && u_dut.u_system.data_request_valid
                    && !u_dut.u_system.data_request_write
                    && u_dut.u_system.data_request_address == 32'h2000_1008)
                spin_start_cycle = cycle;
            if (framebuffer_write_valid_o) begin
                // Guard: drawing must wait for a requested swap to complete.
                if (swap_pending || u_dut.u_system.framebuffer_swap_request) begin
                    swap_guard_violations++;
                    $fdisplay(report_fd, "guard cycle=%0d address=0x%04h write while swap pending",
                              cycle - resume_cycle, framebuffer_write_address_o);
                    finish_run("write-while-swap-pending");
                end
                for (int lane = 0; lane < 4; lane++) begin
                    int unsigned target;
                    target = 32'(framebuffer_write_address_o) + 32'(lane);
                    if (framebuffer_write_strobe_o[lane] && target < FRAME_BYTES
                            && (FULL_WIDTH || lane[0] == 1'b0)) begin
                        for (int pixel = 0; pixel < (FULL_WIDTH ? 1 : 2); pixel++) begin
                            shadow[!last_front][target + 32'(pixel)] = framebuffer_write_data_o[lane*8 +: 8];
                            if (!written[!last_front][target + 32'(pixel)]) begin
                                written[!last_front][target + 32'(pixel)] = 1;
                                written_count[!last_front]++;
                            end
                        end
                    end
                end
            end
            if (!scene_done && scene_marker_seen && !swap_pending && !swap_requested
                    && written_count[last_front] == FRAME_BYTES) begin
                scene_done = 1;
                report_game_state("scene", frame_index);
                dump_single_tic_pixels("scene");
                $fdisplay(report_fd, "scene cycle=%0d retired=%0d crc=0x%08h boot_seen=%0d ihit=%0d imiss=%0d dhit=%0d dmiss=%0d",
                          cycle - resume_cycle, retired(), shadow_crc(last_front), boot_seen, ihit, imiss, dhit, dmiss);
                vga_armed = 1; vga_label = "scene";
                finish_after_vga = (frames_wanted == 0);
            end
            if (cycle - resume_cycle >= max_cycles) finish_run("cycle-limit");
        end

        // VGA raster capture: same CRC definition as the cocotb gate (RGB444 as
        // two big-endian bytes per clock over one 800x525 raster from frame start).
            if (vga_armed && !vga_active && frame_start_o) begin
                vga_armed = 0;
                vga_active = 1;
                vga_count = 0;
                vga_crc = 32'hFFFFFFFF;
                if (ppm_dir != "") begin
                    vga_fd = $fopen({ppm_dir, "/doom-", vga_label, "-vga.ppm"}, "wb");
                    $fwrite(vga_fd, "P6\n640 480\n255\n");
                end
            end
            if (vga_active) begin
                vga_crc = crc32_byte(vga_crc, {4'h0, vga_red_o});
                vga_crc = crc32_byte(vga_crc, {vga_green_o, vga_blue_o});
                if (vga_fd != 0 && visible_o)
                    $fwrite(vga_fd, "%c%c%c", {vga_red_o, vga_red_o}, {vga_green_o, vga_green_o},
                            {vga_blue_o, vga_blue_o});
                vga_count++;
                if (vga_count == RASTER_CYCLES) begin
                    vga_active = 0;
                    if (vga_fd != 0) begin $fclose(vga_fd); vga_fd = 0; end
                    $fdisplay(report_fd, "vga label=%s crc=0x%08h cycle=%0d", vga_label, ~vga_crc,
                              cycle - resume_cycle);
                    if (finish_after_vga) finish_run("pass");
                end
            end
    end

`ifdef N2D_PERF_FST
    // Bounded waveform window in absolute cycles. Verilator does not act on
    // $dumpon/$dumpoff, so tracing starts with $dumpvars at the window start
    // and an FST run ends the simulation at the window end.
    always @(negedge clk_i) begin
        if (fst_end != 0) begin
            if (!fst_started && cycle >= fst_start) begin
                fst_started = 1;
                $dumpvars(0, u_dut);
                $fdisplay(report_fd, "fst on cycle=%0d", cycle);
            end
            if (fst_started && cycle >= fst_end) begin
                $fdisplay(report_fd, "fst off cycle=%0d", cycle);
                finish_run("fst-window");
            end
        end
    end
`endif

    // Optional binary memory-stream trace for offline cache studies. Each
    // record is 20 little-endian bytes after the 8-byte "N2DMST01" header:
    // kind, aux, the low 16 bits of the retired-instruction count (valid on
    // retired load/store records), then 32-bit cycle (relative to resume),
    // address, pc and data. Kinds: 1 retired load (aux = read mask, data =
    // loaded word), 2 retired store (aux = write mask, data = store data), 3 accepted
    // I-cache request whose 16-byte line differs from the previous one
    // (address = line), 4 D-cache miss event, 5 I-cache miss event, 6 line
    // request accepted (aux = owner | write << 4, address = line), 7 line
    // response (aux = owner), 8 completed frame (bank swap) (aux = after the first
    // scene, address = frame index, pc = retired count), 9 stream start (pc
    // = retired count). Retired memory operations are in program order,
    // which is the blocking D-cache's access order.
    task automatic stream_record(input logic [7:0] kind, input logic [7:0] aux,
                                 input logic [31:0] address, input logic [31:0] pc,
                                 input logic [31:0] data);
        logic [31:0] when;
        logic [15:0] count;
        when = 32'(cycle - resume_cycle);
        count = 16'(retired());
        $fwrite(stream_fd, "%c%c%c%c%c%c%c%c%c%c%c%c%c%c%c%c%c%c%c%c", kind, aux, count[7:0], count[15:8],
                when[7:0], when[15:8], when[23:16], when[31:24],
                address[7:0], address[15:8], address[23:16], address[31:24],
                pc[7:0], pc[15:8], pc[23:16], pc[31:24],
                data[7:0], data[15:8], data[23:16], data[31:24]);
    endtask

    always @(negedge clk_i) begin
        if (stream_fd != 0 && running) begin
            if (!stream_on && (stream_at_scene ? scene_marker_seen
                                               : (cycle - resume_cycle >= stream_start))) begin
                stream_on = 1;
                stream_record(8'd9, 8'h0, 32'h0, 32'(retired()), 32'h0);
            end
            if (stream_on) begin
                if (observation_valid_o && observation_memory_valid_o && !observation_trap_o)
                    stream_record(observation_memory_write_o ? 8'd2 : 8'd1,
                                  {4'h0, observation_memory_write_o ? observation_memory_write_mask_o
                                                                    : observation_memory_read_mask_o},
                                  observation_memory_address_o, observation_pc_o,
                                  observation_memory_write_o ? observation_memory_write_data_o
                                                             : observation_memory_read_data_o);
                if (u_dut.u_system.instruction_request_valid && u_dut.u_system.instruction_request_ready
                        && (!stream_iline_valid
                            || u_dut.u_system.instruction_request_address[31:4] != stream_last_iline[31:4])) begin
                    stream_last_iline = {u_dut.u_system.instruction_request_address[31:4], 4'h0};
                    stream_iline_valid = 1;
                    stream_record(8'd3, 8'h0, stream_last_iline, 32'h0, 32'h0);
                end
                if (data_miss_event_o) stream_record(8'd4, 8'h0, 32'h0, 32'h0, 32'h0);
                if (instruction_miss_event_o) stream_record(8'd5, 8'h0, 32'h0, 32'h0, 32'h0);
                for (int owner = 0; owner < 2; owner++) begin
                    if (u_dut.memory_request_valid[owner] && u_dut.memory_request_ready[owner])
                        stream_record(8'd6, {3'h0, u_dut.memory_request_write[owner], 4'(owner)},
                                      {6'h0, u_dut.memory_request_address[owner*26 +: 26]}, 32'h0, 32'h0);
                    if (u_dut.memory_response_valid[owner])
                        stream_record(8'd7, 8'(owner), 32'h0, 32'h0, 32'h0);
                end
            end
        end
    end

    // Optional text trace of retirements, cache events and line traffic.
    always @(negedge clk_i) begin
        if (trace_fd != 0 && running && cycle >= trace_start && cycle < trace_end) begin
            if (observation_valid_o)
                $fdisplay(trace_fd, "%0d R pc=%08h insn=%08h", cycle, observation_pc_o,
                          observation_instruction_o);
            if (instruction_hit_event_o) $fdisplay(trace_fd, "%0d IH", cycle);
            if (instruction_miss_event_o) $fdisplay(trace_fd, "%0d IM", cycle);
            if (data_hit_event_o) $fdisplay(trace_fd, "%0d DH", cycle);
            if (data_miss_event_o) $fdisplay(trace_fd, "%0d DM", cycle);
            for (int owner = 0; owner < 3; owner++)
                if (u_dut.memory_request_valid[owner] && u_dut.memory_request_ready[owner])
                    $fdisplay(trace_fd, "%0d L%0d %s addr=%07h", cycle, owner,
                              u_dut.memory_request_write[owner] ? "W" : "R",
                              u_dut.memory_request_address[owner*26 +: 26]);
            for (int owner = 0; owner < 3; owner++)
                if (u_dut.memory_response_valid[owner])
                    $fdisplay(trace_fd, "%0d L%0d done", cycle, owner);
        end
    end
    /* verilator lint_on BLKSEQ */
endmodule
`endif
