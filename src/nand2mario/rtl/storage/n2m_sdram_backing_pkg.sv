`default_nettype none

// nand2mario's configuration of the common SDRAM controller (n2a_sdram_ctrl):
// burst one, close page, linear map and the waits that keep the backing's
// fixed 9/10-clock latency (wiki/src/common/rtl/sdram/mas.md, project
// configurations). The backing and every bench fixture take the controller
// parameters from here, so the benches test the product configuration.
package n2m_sdram_backing_pkg;
    localparam int unsigned SDRAM_BURST_BEATS           = 1;
    localparam bit          SDRAM_OPEN_ROW              = 1'b0;
    localparam bit          SDRAM_ROW_XOR_BANK          = 1'b0;
    localparam int unsigned SDRAM_POWERUP_CLOCKS        = 5001;
    localparam int unsigned SDRAM_INIT_REFRESHES        = 8;
    localparam int unsigned SDRAM_INIT_WAIT_MIN         = 2;
    localparam int unsigned SDRAM_WAIT_RCD              = 2;
    localparam int unsigned SDRAM_WAIT_RP               = 2;
    localparam int unsigned SDRAM_WAIT_RFC              = 3;
    localparam int unsigned SDRAM_WAIT_WR               = 2;
    localparam int unsigned SDRAM_WAIT_MRD              = 2;
    // Why: the refresh-deadline negative target overrides the fixture
    // parameters these default, so that elaboration reads neither.
    /* verilator lint_off UNUSEDPARAM */
    localparam int unsigned SDRAM_REFRESH_DUE           = 160;
    localparam int unsigned SDRAM_REFRESH_DEADLINE      = 195;
    /* verilator lint_on UNUSEDPARAM */
    localparam bit          SDRAM_REFRESH_AGE_FROM_MODE = 1'b1;
endpackage
