module n2a_sync_ram_invalid_min_depth_fixture;
  logic       clk;
  logic       write_enable;
  logic       address;
  logic [7:0] write_data;
  logic [7:0] read_data;

  n2a_sync_ram #(
    .DATA_WIDTH(8),
    .DEPTH(1),
    .ADDR_WIDTH(1)
  ) dut (
    .clk_i(clk),
    .write_enable_i(write_enable),
    .address_i(address),
    .write_data_i(write_data),
    .read_data_o(read_data)
  );

  initial begin
    clk = 1'b0;
    write_enable = 1'b0;
    address = 1'b0;
    write_data = '0;
    #100ns;
    $display("unexpected invalid RAM output=%0h", read_data);
    $fatal(1, "RAM depth smaller than two was accepted");
  end

  always #20ns clk = ~clk;
endmodule
