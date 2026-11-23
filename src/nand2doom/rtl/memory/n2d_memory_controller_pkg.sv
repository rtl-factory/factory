`default_nettype none

// nand2doom's configuration of the common SDRAM controller (n2a_sdram_ctrl,
// wiki/src/common/rtl/sdram/mas.md "project configurations"): burst eight,
// open row and the row-XOR-bank map keep the line latency.
// n2d_memory_controller and every bench fixture take the parameters from
// here, so the benches test the product configuration.
package n2d_memory_controller_pkg;
    localparam int unsigned N2D_SDRAM_BURST_BEATS           = 8;
    localparam bit          N2D_SDRAM_OPEN_ROW              = 1'b1;
    localparam bit          N2D_SDRAM_ROW_XOR_BANK          = 1'b1;
    localparam int unsigned N2D_SDRAM_POWERUP_CLOCKS        = 5000;
    localparam int unsigned N2D_SDRAM_INIT_REFRESHES        = 2;
    localparam int unsigned N2D_SDRAM_INIT_WAIT_MIN         = 2;
    localparam int unsigned N2D_SDRAM_WAIT_RCD              = 0;
    localparam int unsigned N2D_SDRAM_WAIT_RP               = 0;
    localparam int unsigned N2D_SDRAM_WAIT_RFC              = 0;
    // Why: open row never waits after a write beat; 1 is the controller default.
    localparam int unsigned N2D_SDRAM_WAIT_WR               = 1;
    localparam int unsigned N2D_SDRAM_WAIT_MRD              = 0;
    localparam int unsigned N2D_SDRAM_REFRESH_DUE           = 163;
    localparam int unsigned N2D_SDRAM_REFRESH_DEADLINE      = 194;
    localparam bit          N2D_SDRAM_REFRESH_AGE_FROM_MODE = 1'b0;
endpackage
