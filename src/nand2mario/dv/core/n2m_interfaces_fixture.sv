module n2m_interfaces_fixture;
    import n2m_interfaces_pkg::*;

    initial begin
        if ($bits(t_address) != 16) $fatal(1, "t_address width mismatch");
        if ($bits(t_data) != 8) $fatal(1, "t_data width mismatch");
        if ($bits(t_access_kind) != 2) $fatal(1, "t_access_kind width mismatch");
        if ($bits(t_bus_request) != 28) $fatal(1, "t_bus_request width mismatch");
        if ($bits(t_bus_response) != 10) $fatal(1, "t_bus_response width mismatch");
        if ($bits(t_retirement) != 143) $fatal(1, "t_retirement width mismatch");
        if ((ACCESS_FETCH != 2'd0) || (ACCESS_DATA != 2'd1) ||
            (ACCESS_STACK != 2'd2) || (ACCESS_IDLE != 2'd3)) begin
            $fatal(1, "t_access_kind encoding mismatch");
        end
        $finish;
    end
endmodule
