module n2a_sync_ram_invalid_data_width_fixture;
    logic       clk;
    logic       write_enable;
    logic       address;
    logic       write_data;
    logic       read_data;

    n2a_sync_ram #(
        .DATA_WIDTH(0),
        .DEPTH(2),
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
        write_data = 1'b0;
        #100ns;
        $display("unexpected invalid RAM output=%0h", read_data);
        $fatal(1, "invalid RAM data width was accepted");
    end

    always #20ns clk = ~clk;
endmodule
