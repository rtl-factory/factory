module n2m_interfaces_fixture;
  import n2m_interfaces_pkg::*;

  initial begin
    if ($bits(address_t) != 16) $fatal(1, "address_t width mismatch");
    if ($bits(data_t) != 8) $fatal(1, "data_t width mismatch");
    if ($bits(access_kind_t) != 2) $fatal(1, "access_kind_t width mismatch");
    if ($bits(bus_request_t) != 28) $fatal(1, "bus_request_t width mismatch");
    if ($bits(bus_response_t) != 10) $fatal(1, "bus_response_t width mismatch");
    if ($bits(retirement_t) != 143) $fatal(1, "retirement_t width mismatch");
    if ((ACCESS_FETCH != 2'd0) || (ACCESS_DATA != 2'd1) ||
        (ACCESS_STACK != 2'd2) || (ACCESS_IDLE != 2'd3)) begin
      $fatal(1, "access_kind_t encoding mismatch");
    end
    $finish;
  end
endmodule
