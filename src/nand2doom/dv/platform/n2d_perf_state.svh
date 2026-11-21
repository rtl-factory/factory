// Simulation-only passive game-state scoreboard. The symbol list is resolved
// from this run's ELF; no backing SDRAM or core-private state is inspected.
localparam int GAME_STATE_WORDS = 7;
string game_state_names [GAME_STATE_WORDS];
logic [31:0] game_state_addresses [GAME_STATE_WORDS];
logic [31:0] game_state_values [GAME_STATE_WORDS];
logic [3:0] game_state_known [GAME_STATE_WORDS];
bit game_state_enabled;
longint unsigned game_state_last_order;
bit game_state_order_seen;

initial begin
    string state_path;
    int state_fd;
    if ($value$plusargs("state_symbols=%s", state_path)) begin
        state_fd = $fopen(state_path, "r");
        if (state_fd == 0) $fatal(1, "cannot open game-state symbols %s", state_path);
        for (int word_index = 0; word_index < GAME_STATE_WORDS; word_index++) begin
            if ($fscanf(state_fd, "%s %h\n", game_state_names[word_index],
                        game_state_addresses[word_index]) != 2)
                $fatal(1, "incomplete game-state symbol list");
            if (game_state_addresses[word_index][1:0] != 0)
                $fatal(1, "unaligned game-state symbol");
        end
        $fclose(state_fd);
        game_state_enabled = 1;
    end
end

task automatic observe_game_state;
    if (game_state_enabled && observation_valid_o && observation_memory_valid_o
            && observation_memory_write_o && !observation_trap_o) begin
        if (game_state_order_seen && observation_order_o <= game_state_last_order)
            $fatal(1, "unordered game-state store observation");
        game_state_last_order = observation_order_o;
        game_state_order_seen = 1;
        for (int word_index = 0; word_index < GAME_STATE_WORDS; word_index++) begin
            if ({observation_memory_address_o[31:2], 2'b00} == game_state_addresses[word_index]) begin
                // Why: the architectural data and mask already occupy
                // addressed lanes, including the byte stores that initialize
                // BSS, so lanes copy straight through without shifting.
                for (int lane = 0; lane < 4; lane++) begin
                    if (observation_memory_write_mask_o[lane]) begin
                        game_state_values[word_index][lane*8 +: 8] = observation_memory_write_data_o[lane*8 +: 8];
                        game_state_known[word_index][lane] = 1;
                    end
                end
            end
        end
    end
endtask

task automatic report_game_state(input string label, input int unsigned index);
    if (game_state_enabled) begin
        $fwrite(report_fd, "state label=%s index=%0d cycle=%0d retired=%0d", label, index,
                cycle - resume_cycle, retired());
        for (int word_index = 0; word_index < GAME_STATE_WORDS; word_index++) begin
            if (game_state_known[word_index] != 4'hf)
                $fatal(1, "unobserved initialization of %s", game_state_names[word_index]);
            $fwrite(report_fd, " %s=%0d", game_state_names[word_index], game_state_values[word_index]);
        end
        $fwrite(report_fd, "\n");
    end
endtask
