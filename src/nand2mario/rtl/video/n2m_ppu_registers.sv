`default_nettype none
`include "src/common/rtl/pkg/macros.vh"

// LCD register file on the peripheral port: LCDC, STAT enables, SCY, SCX,
// LYC, BGP, OBP0, OBP1, WY and WX are stored here; LY and the read-only
// STAT fields come from the timing block. The port follows the memory peripheral contract: the
// response is combinational from the presented request, a write lands on
// the edge that samples the response, and io_selected_o decodes the address
// alone so the system fan-out can route the request. Reset clears every
// register, so the LCD starts off (no boot ROM establishes post-boot values).
module n2m_ppu_registers (
  input  logic                                clk_i,
  input  logic                                reset_i,
  input  n2m_interfaces_pkg::t_bus_request    io_request_i,
  output n2m_interfaces_pkg::t_bus_response   io_response_o,
  output logic                                io_selected_o,
  input  n2m_ppu_pkg::t_mode                  mode_i,
  input  logic                                coincidence_i,
  input  n2m_ppu_pkg::t_line                  ly_i,
  output logic [7:0]                          lcdc_o,
  output logic [3:0]                          stat_enables_o,
  output logic [7:0]                          scy_o,
  output logic [7:0]                          scx_o,
  output logic [7:0]                          lyc_o,
  output logic [7:0]                          bgp_o,
  output logic [7:0]                          obp0_o,
  output logic [7:0]                          obp1_o,
  output logic [7:0]                          wy_o,
  output logic [7:0]                          wx_o
);
  import n2m_ppu_pkg::*;

  logic [15:0] address;
  logic [7:0]  write_data;
  logic [3:0]  stat_write_data;
  logic        write;
  logic        lcdc_write, stat_write, scy_write, scx_write, lyc_write, bgp_write;
  logic        obp0_write, obp1_write, wy_write, wx_write;
  logic [7:0]  lcdc_q, scy_q, scx_q, lyc_q, bgp_q, obp0_q, obp1_q, wy_q, wx_q;
  logic [3:0]  stat_enables_q;
  logic [7:0]  read_data;

  assign address = io_request_i.address;
  assign write_data = io_request_i.write_data;
  assign stat_write_data = io_request_i.write_data[6:3];

  always_comb begin
    io_selected_o = 1'b0;
    case (address)
      LCDC_ADDRESS, STAT_ADDRESS, SCY_ADDRESS, SCX_ADDRESS, LY_ADDRESS, LYC_ADDRESS,
      BGP_ADDRESS, OBP0_ADDRESS, OBP1_ADDRESS, WY_ADDRESS, WX_ADDRESS: io_selected_o = 1'b1;
      default: io_selected_o = 1'b0;
    endcase
  end

  // Why: no write-once guard is needed: the memory presents the held request
  // for exactly one cycle when the responder answers combinationally.
  assign write = io_request_i.valid && io_request_i.write && io_selected_o;
  assign lcdc_write = write && (address == LCDC_ADDRESS);
  assign stat_write = write && (address == STAT_ADDRESS);
  assign scy_write = write && (address == SCY_ADDRESS);
  assign scx_write = write && (address == SCX_ADDRESS);
  assign lyc_write = write && (address == LYC_ADDRESS);
  assign bgp_write = write && (address == BGP_ADDRESS);
  assign obp0_write = write && (address == OBP0_ADDRESS);
  assign obp1_write = write && (address == OBP1_ADDRESS);
  assign wy_write = write && (address == WY_ADDRESS);
  assign wx_write = write && (address == WX_ADDRESS);

  // Ref: Pan Docs "Power Up Sequence": DMG post-boot values (there is no boot
  // ROM): LCD on with the background enabled from $8000 tiles ($91) and BGP
  // $FC; the rest zero.
  `DFF_RST_EN(lcdc_q, write_data, clk_i, lcdc_write, reset_i, 8'h91)
  `DFF_RST_EN(stat_enables_q, stat_write_data, clk_i, stat_write, reset_i, 4'h0)
  `DFF_RST_EN(scy_q, write_data, clk_i, scy_write, reset_i, 8'h00)
  `DFF_RST_EN(scx_q, write_data, clk_i, scx_write, reset_i, 8'h00)
  `DFF_RST_EN(lyc_q, write_data, clk_i, lyc_write, reset_i, 8'h00)
  `DFF_RST_EN(bgp_q, write_data, clk_i, bgp_write, reset_i, 8'hFC)
  `DFF_RST_EN(obp0_q, write_data, clk_i, obp0_write, reset_i, 8'h00)
  `DFF_RST_EN(obp1_q, write_data, clk_i, obp1_write, reset_i, 8'h00)
  `DFF_RST_EN(wy_q, write_data, clk_i, wy_write, reset_i, 8'h00)
  `DFF_RST_EN(wx_q, write_data, clk_i, wx_write, reset_i, 8'h00)

  // Ref: Pan Docs "STAT": bit 7 reads 1, bits 6:3 the stored enables, bit 2 the
  // coincidence flag and bits 1:0 the mode. Why: the unselected value is never
  // forwarded (the response is valid only when selected), so the default is
  // simply zero.
  always_comb begin
    read_data = 8'h00;
    case (address)
      LCDC_ADDRESS: read_data = lcdc_q;
      STAT_ADDRESS: read_data = {1'b1, stat_enables_q, coincidence_i, mode_i};
      SCY_ADDRESS:  read_data = scy_q;
      SCX_ADDRESS:  read_data = scx_q;
      LY_ADDRESS:   read_data = ly_i;
      LYC_ADDRESS:  read_data = lyc_q;
      BGP_ADDRESS:  read_data = bgp_q;
      OBP0_ADDRESS: read_data = obp0_q;
      OBP1_ADDRESS: read_data = obp1_q;
      WY_ADDRESS:   read_data = wy_q;
      WX_ADDRESS:   read_data = wx_q;
      default:      read_data = 8'h00;
    endcase
    io_response_o.valid = io_request_i.valid && io_selected_o;
    io_response_o.fault = 1'b0;
    io_response_o.read_data = read_data;
  end

  assign lcdc_o = lcdc_q;
  assign stat_enables_o = stat_enables_q;
  assign scy_o = scy_q;
  assign scx_o = scx_q;
  assign lyc_o = lyc_q;
  assign bgp_o = bgp_q;
  assign obp0_o = obp0_q;
  assign obp1_o = obp1_q;
  assign wy_o = wy_q;
  assign wx_o = wx_q;

  `ASSERT(io_request_known, clk_i, reset_i,
      !io_request_i.valid || !$isunknown(io_request_i))
endmodule
