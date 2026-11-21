`default_nettype none
`include "src/common/rtl/pkg/macros.vh"

// nand2mario display tier: the frame bridge writes the PPU pixel stream into
// the three-bank frame store and owns the bank roles; the VGA scanout reads
// the displayed bank through the common raster timing and drives 4:4:4 RGB
// with active-low syncs. Everything is in the 25 MHz system domain; there
// is no clock-domain crossing. The observation pulses expose the bank
// policy to the tests and the later board top. The snapshot ports pin the
// displayed frame and read it back byte by byte; the snapshot reader takes
// the frame store's read port only in cycles the scanout leaves free.
module n2m_display (
    input  logic                        clk_i,
    input  logic                        reset_i,
    // PPU LCD enable (LCDC.7) and pixel stream.
    input  logic                        lcd_enable_i,
    input  logic                        pixel_valid_i,
    input  n2m_display_pkg::t_shade     pixel_shade_i,
    input  logic [7:0]                  pixel_x_i,
    input  logic [7:0]                  pixel_y_i,
    // VGA output, aligned with pixel_start_o.
    output logic [3:0]                  vga_red_o,
    output logic [3:0]                  vga_green_o,
    output logic [3:0]                  vga_blue_o,
    output logic                        vga_hsync_o,
    output logic                        vga_vsync_o,
    output logic                        pixel_start_o,
    // Bank policy observation: one-cycle pulses and the display state.
    output logic                        frame_complete_o,
    output logic                        frame_swapped_o,
    output logic                        frame_dropped_o,
    output logic                        frame_repeated_o,
    output logic                        display_valid_o,
    // Frame snapshot: pin/release requests, the pin level and the byte port.
    input  logic                                 snapshot_pin_i,
    input  logic                                 snapshot_release_i,
    output logic                                 snapshot_pinned_o,
    input  n2m_snapshot_pkg::t_snapshot_request   snapshot_request_i,
    output logic                                 snapshot_ready_o,
    output n2m_snapshot_pkg::t_snapshot_response  snapshot_response_o
);
    logic                          vga_frame_start;
    logic                          write_enable;
    n2m_display_pkg::t_bank        write_bank, display_bank, read_bank;
    n2m_display_pkg::t_frame_index write_index, read_index, scan_read_index, snapshot_read_index;
    n2m_display_pkg::t_shade       write_shade, read_shade;
    n2m_snapshot_pkg::t_frame_id    display_id;
    logic                          scan_read_busy, snapshot_read_enable;

    // Why: the scanout must never miss a read, so the snapshot reader takes the
    // store's read index only in cycles the scanout does not consume; the bank
    // is always the displayed one, which the bridge never writes.
    assign read_index = snapshot_read_enable ? snapshot_read_index : scan_read_index;

    n2m_frame_bridge u_bridge (
        .clk_i             (clk_i),
        .reset_i           (reset_i),
        .lcd_enable_i      (lcd_enable_i),
        .pixel_valid_i     (pixel_valid_i),
        .pixel_shade_i     (pixel_shade_i),
        .pixel_x_i         (pixel_x_i),
        .pixel_y_i         (pixel_y_i),
        .vga_frame_start_i (vga_frame_start),
        .snapshot_pin_i    (snapshot_pin_i),
        .snapshot_release_i (snapshot_release_i),
        .write_enable_o    (write_enable),
        .write_bank_o      (write_bank),
        .write_index_o     (write_index),
        .write_shade_o     (write_shade),
        .display_bank_o    (display_bank),
        .display_valid_o   (display_valid_o),
        .display_id_o      (display_id),
        .snapshot_pinned_o (snapshot_pinned_o),
        .frame_complete_o  (frame_complete_o),
        .frame_swapped_o   (frame_swapped_o),
        .frame_dropped_o   (frame_dropped_o),
        .frame_repeated_o  (frame_repeated_o)
    );

    n2m_frame_ram u_frame_ram (
        .clk_i          (clk_i),
        .reset_i        (reset_i),
        .write_enable_i (write_enable),
        .write_bank_i   (write_bank),
        .write_index_i  (write_index),
        .write_shade_i  (write_shade),
        .read_bank_i    (read_bank),
        .read_index_i   (read_index),
        .read_shade_o   (read_shade)
    );

    n2m_vga_scanout u_scanout (
        .clk_i             (clk_i),
        .reset_i           (reset_i),
        .display_bank_i    (display_bank),
        .display_valid_i   (display_valid_o),
        .read_bank_o       (read_bank),
        .read_index_o      (scan_read_index),
        .read_shade_i      (read_shade),
        .read_busy_o       (scan_read_busy),
        .vga_frame_start_o (vga_frame_start),
        .vga_red_o         (vga_red_o),
        .vga_green_o       (vga_green_o),
        .vga_blue_o        (vga_blue_o),
        .vga_hsync_o       (vga_hsync_o),
        .vga_vsync_o       (vga_vsync_o),
        .pixel_start_o     (pixel_start_o)
    );

    n2m_frame_snapshot u_snapshot (
        .clk_i           (clk_i),
        .reset_i         (reset_i),
        .request_i       (snapshot_request_i),
        .ready_o         (snapshot_ready_o),
        .response_o      (snapshot_response_o),
        .pinned_i        (snapshot_pinned_o),
        .display_valid_i (display_valid_o),
        .bank_i          (display_bank),
        .frame_id_i      (display_id),
        .scan_busy_i     (scan_read_busy),
        .read_enable_o   (snapshot_read_enable),
        .read_index_o    (snapshot_read_index),
        .read_shade_i    (read_shade)
    );

    `ASSERT(scan_and_write_banks_differ, clk_i, reset_i,
        !write_enable || (write_bank != read_bank))
    `ASSERT(snapshot_read_outside_scan, clk_i, reset_i, !snapshot_read_enable || !scan_read_busy)
endmodule
