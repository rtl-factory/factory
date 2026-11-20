// Verification-only passive call/mode monitor from architectural retirements.
logic [31:0] single_tick_pc, single_display_pc, single_mode_address;
logic [31:0] single_mode_value;
logic [3:0] single_mode_known;
longint unsigned single_tick_calls, single_display_calls, single_previous_ticks;
bit single_enabled;
initial begin
    string path;
    int fd;
    if ($value$plusargs("single_tic_calls=%s", path)) begin
        fd = $fopen(path, "r");
        if (fd == 0 || $fscanf(fd, "%h %h %h", single_tick_pc, single_display_pc, single_mode_address) != 3)
            $fatal(1, "missing single-tic compiled call/mode symbols");
        $fclose(fd);
        single_enabled = 1;
    end
end
task automatic observe_single_tic_calls;
    if (single_enabled && observation_valid_o && !observation_trap_o) begin
        if (observation_memory_valid_o && observation_memory_write_o
                && {observation_memory_address_o[31:2],2'b00} == single_mode_address) begin
            for (int lane = 0; lane < 4; lane++) begin
                if (observation_memory_write_mask_o[lane]) begin
                    single_mode_value[lane*8 +: 8] = observation_memory_write_data_o[lane*8 +: 8];
                    single_mode_known[lane] = 1;
                end
            end
        end
        if (observation_pc_o == single_tick_pc) single_tick_calls++;
        if (observation_pc_o == single_display_pc) begin
            single_display_calls++;
            if (single_mode_known != 4'hf || single_mode_value != 1)
                $fatal(1, "compiled firmware did not enable singletics before display");
            if (single_display_calls > 1 && single_tick_calls - single_previous_ticks != 1)
                $fatal(1, "expected exactly one G_Ticker between D_Display entries");
            $fdisplay(report_fd, "single_tic display=%0d tick_calls=%0d since_previous=%0d mode=%0d cycle=%0d",
                single_display_calls, single_tick_calls, single_tick_calls - single_previous_ticks,
                single_mode_value, cycle - resume_cycle);
            single_previous_ticks = single_tick_calls;
        end
    end
endtask
