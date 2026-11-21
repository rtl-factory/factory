`default_nettype none

// Game Boy system memory map owned by the nand2mario memory subsystem:
// region bounds, store sizes and the decode destination encoding. The
// package holds facts only; n2m_memory_decode turns an address into a
// destination and n2m_memory serves the CPU bus from them.
package n2m_memory_pkg;
  // Region bounds (inclusive) of the DMG CPU address space.
  localparam logic [15:0] ROM_START       = 16'h0000;
  localparam logic [15:0] ROM_END         = 16'h7FFF;
  localparam logic [15:0] VRAM_START      = 16'h8000;
  localparam logic [15:0] VRAM_END        = 16'h9FFF;
  localparam logic [15:0] CART_RAM_START  = 16'hA000;
  localparam logic [15:0] CART_RAM_END    = 16'hBFFF;
  localparam logic [15:0] WRAM_START      = 16'hC000;
  localparam logic [15:0] WRAM_END        = 16'hDFFF;
  localparam logic [15:0] ECHO_START      = 16'hE000;
  localparam logic [15:0] ECHO_END        = 16'hFDFF;
  localparam logic [15:0] OAM_START       = 16'hFE00;
  localparam logic [15:0] OAM_END         = 16'hFE9F;
  localparam logic [15:0] UNUSABLE_START  = 16'hFEA0;
  localparam logic [15:0] UNUSABLE_END    = 16'hFEFF;
  localparam logic [15:0] IO_START        = 16'hFF00;
  localparam logic [15:0] IO_END          = 16'hFF7F;
  localparam logic [15:0] HRAM_START      = 16'hFF80;
  localparam logic [15:0] HRAM_END        = 16'hFFFE;
  localparam logic [15:0] IE_ADDRESS      = 16'hFFFF;

  // Store geometry. ROM_BYTES is the size of the CPU's ROM window (both
  // banks); its storage lives behind the cartridge port. OAM and HRAM are not
  // power-of-two regions; their stores are the next power of two and the
  // decode never produces an offset beyond the region, so the padding bytes
  // are unreachable.
  localparam int unsigned ROM_BYTES        = 32768;
  localparam int unsigned VRAM_BYTES       = 8192;
  localparam int unsigned WRAM_BYTES       = 8192;
  localparam int unsigned OAM_BYTES        = 160;
  localparam int unsigned OAM_STORE_DEPTH  = 256;
  localparam int unsigned HRAM_BYTES       = 127;
  localparam int unsigned HRAM_STORE_DEPTH = 128;
  localparam int unsigned OFFSET_WIDTH     = 15;

  // Byte returned by every destination that has no backing storage.
  localparam logic [7:0] OPEN_BUS_DATA = 8'hFF;

  // Decode destinations. The destination selects the behavior owner only;
  // it never implies a read value by itself.
  typedef enum logic [3:0] {
    DEST_ROM      = 4'd0,  // $0000-$7FFF: forwarded to the cartridge port
    DEST_VRAM     = 4'd1,  // $8000-$9FFF: 8 KiB store
    DEST_CART_RAM = 4'd2,  // $A000-$BFFF: forwarded to the cartridge port
    DEST_WRAM     = 4'd3,  // $C000-$DFFF and echo $E000-$FDFF: 8 KiB store
    DEST_OAM      = 4'd4,  // $FE00-$FE9F: 160-byte store
    DEST_UNUSABLE = 4'd5,  // $FEA0-$FEFF: reads OPEN_BUS_DATA, writes ignored
    DEST_IO       = 4'd6,  // $FF00-$FF7F: peripheral port
    DEST_HRAM     = 4'd7,  // $FF80-$FFFE: 127-byte store
    DEST_IE       = 4'd8   // $FFFF: interrupt-enable register
  } t_destination;

  typedef logic [OFFSET_WIDTH-1:0] t_offset;
endpackage
