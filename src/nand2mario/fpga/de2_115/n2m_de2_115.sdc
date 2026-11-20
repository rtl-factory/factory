# nand2mario DE2-115 timing constraints (wiki/src/nand2mario/fpga/de2_115/board-spec.md).
create_clock -name reference -period 20.000 [get_ports CLOCK_50]
derive_pll_clocks
# JTAG keyboard endpoint (n2a_jtag_keyboard in the board top): the Virtual
# JTAG hub's TCK is the endpoint's scan clock. The command/response mailbox
# buses between the TCK and system domains stay timed (the budget of the
# other projects' JTAG keyboards); only the first stages of the explicitly
# named scalar/reset synchronizers are cut. Every collection must exist in the
# fitter and timing analyzer; only synthesis (quartus_map) evaluates this file
# before the debug fabric exists.
proc n2m_jtag_required {collection description} {
    set synthesis [expr {[info exists ::TimeQuestInfo(nameofexecutable)] &&
                         $::TimeQuestInfo(nameofexecutable) eq "quartus_map"}]
    if {[get_collection_size $collection] == 0 && !$synthesis} {
        error "Missing JTAG timing collection: $description"
    }
    return $collection
}
# The vendor hub SDC makes TCK a separate asynchronous group; drop that so the
# mailbox bus constraints below are honoured (no other groups exist here).
remove_clock_groups -all
remove_clock [n2m_jtag_required [get_clocks {altera_reserved_tck}] vendor_tck]
create_clock -name jtag_tck -period 100.000 [n2m_jtag_required [get_ports {altera_reserved_tck}] tck]
set_max_delay 80.000 -from [n2m_jtag_required [get_registers {*u_jtag_keyboard*|scan_q.command[*]}] command_hold] -to [n2m_jtag_required [get_registers {*u_jtag_keyboard*|q.command[*]}] command_capture]
set_min_delay 0.000 -from [n2m_jtag_required [get_registers {*u_jtag_keyboard*|scan_q.command[*]}] command_hold] -to [n2m_jtag_required [get_registers {*u_jtag_keyboard*|q.command[*]}] command_capture]
set_max_delay 100.000 -from [n2m_jtag_required [get_registers {*u_jtag_keyboard*|q.response[*]}] response_hold] -to [n2m_jtag_required [get_registers {*u_jtag_keyboard*|scan_q.response[*]}] response_capture]
set_min_delay 0.000 -from [n2m_jtag_required [get_registers {*u_jtag_keyboard*|q.response[*]}] response_hold] -to [n2m_jtag_required [get_registers {*u_jtag_keyboard*|scan_q.response[*]}] response_capture]
foreach instance {u_req u_ack u_sys_reset u_scan_reset} {
    set_false_path -to [n2m_jtag_required [get_registers "*u_jtag_keyboard*|${instance}|stages_q\[0\]"] "$instance first stage"]
    n2m_jtag_required [get_registers "*u_jtag_keyboard*|${instance}|stages_q\[1\]"] "$instance second stage"
}
set jtag_inputs [n2m_jtag_required [get_ports {altera_reserved_tdi altera_reserved_tms}] jtag_inputs]
set_input_delay -clock jtag_tck -clock_fall -max 10.000 $jtag_inputs
set_input_delay -clock jtag_tck -clock_fall -min 0.000 $jtag_inputs
set jtag_output [n2m_jtag_required [get_ports {altera_reserved_tdo}] jtag_output]
set_output_delay -clock jtag_tck -max 10.000 $jtag_output
set_output_delay -clock jtag_tck -min 0.000 $jtag_output
derive_clock_uncertainty
set system_clock [get_clocks {*u_pll*|clk[0]}]
if {[get_collection_size $system_clock] != 1} {error "expected one 25 MHz PLL clock"}
set pll_target [get_clock_info -targets $system_clock]
if {[get_collection_size $pll_target] != 1} {error "expected one PLL clock target"}
set sdram_clock_port [get_ports DRAM_CLK]
if {[get_collection_size $sdram_clock_port] != 1} {error "expected one SDRAM clock port"}
# DRAM_CLK is the controller's inverted system clock (storage HAS): the
# device samples 20 ns after the launching edge and the read word is
# captured at the next controller edge.
create_generated_clock -name sdram_clock -source $pll_target -invert $sdram_clock_port
# ISSI IS42S16320D CL2 with 1 ns board allowance: tAC=6, tOH=2.5,
# tIS=1.8, tIH=0.8 ns (the nand2doom DE2-115 budget on the same part).
set_input_delay -clock sdram_clock -max 7.0 [get_ports {DRAM_DQ[*]}]
set_input_delay -clock sdram_clock -min 1.5 [get_ports {DRAM_DQ[*]}]
set sdram_outputs [get_ports {DRAM_ADDR[*] DRAM_BA[*] DRAM_CAS_N DRAM_CKE DRAM_CS_N DRAM_DQ[*] DRAM_DQM[*] DRAM_RAS_N DRAM_WE_N}]
set_output_delay -clock sdram_clock -max 2.8 $sdram_outputs
set_output_delay -clock sdram_clock -min -1.8 $sdram_outputs
# Asynchronous board inputs terminate in the reset synchronizer (KEY0), the
# board-input synchronizers (KEY1, SW) and the loader UART receiver's
# synchronizer (UART_RXD).
set_input_delay -clock reference -max 1.0 [get_ports {KEY[*] SW[*] UART_RXD}]
set_input_delay -clock reference -min 0.0 [get_ports {KEY[*] SW[*] UART_RXD}]
set_false_path -from [get_ports {KEY[*] SW[*] UART_RXD}]
# ADV7123: 0.2 ns setup, 1.5 ns hold, plus 0.5 ns board skew.
# Source the generated clock at its actual forwarded output pin.
create_generated_clock -name vga_dac_clock -source $pll_target -invert [get_ports VGA_CLK]
set_output_delay -clock vga_dac_clock -max 0.7 [get_ports {VGA_R[*] VGA_G[*] VGA_B[*] VGA_HS VGA_VS VGA_BLANK_N VGA_SYNC_N}]
set_output_delay -clock vga_dac_clock -min -2.0 [get_ports {VGA_R[*] VGA_G[*] VGA_B[*] VGA_HS VGA_VS VGA_BLANK_N VGA_SYNC_N}]
set_output_delay -clock $system_clock -source_latency_included -max 10.0 [get_ports {LEDR[*] HEX* UART_TXD}]
set_output_delay -clock $system_clock -source_latency_included -min 0.0 [get_ports {LEDR[*] HEX* UART_TXD}]
