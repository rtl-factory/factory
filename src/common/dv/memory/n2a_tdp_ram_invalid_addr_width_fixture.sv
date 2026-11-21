module n2a_tdp_ram_invalid_addr_width_fixture;
    logic       clk;
    logic       a_write_enable;
    logic       b_write_enable;
    logic [1:0] a_address;
    logic [1:0] b_address;
    logic [7:0] a_write_data;
    logic [7:0] b_write_data;
    logic [7:0] a_read_data;
    logic [7:0] b_read_data;

    n2a_tdp_ram #(
        .DATA_WIDTH(8),
        .DEPTH(8),
        .ADDR_WIDTH(2)
    ) dut (
        .clk_i(clk),
        .a_write_enable_i(a_write_enable),
        .a_address_i(a_address),
        .a_write_data_i(a_write_data),
        .a_read_data_o(a_read_data),
        .b_write_enable_i(b_write_enable),
        .b_address_i(b_address),
        .b_write_data_i(b_write_data),
        .b_read_data_o(b_read_data)
    );

    initial begin
        clk = 1'b0;
        a_write_enable = 1'b0;
        b_write_enable = 1'b0;
        a_address = '0;
        b_address = '0;
        a_write_data = '0;
        b_write_data = '0;
        #100ns;
        $display("unexpected invalid RAM outputs a=%0h b=%0h", a_read_data, b_read_data);
        $fatal(1, "invalid true-dual-port RAM address width was accepted");
    end

    always #20ns clk = ~clk;
endmodule
