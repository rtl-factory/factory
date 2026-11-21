package n2m_interfaces_pkg;
    localparam int unsigned ADDRESS_WIDTH = 16;
    localparam int unsigned DATA_WIDTH = 8;

    typedef logic [ADDRESS_WIDTH-1:0] t_address;
    typedef logic [DATA_WIDTH-1:0] t_data;

    typedef enum logic [1:0] {
        ACCESS_FETCH = 2'd0,
        ACCESS_DATA  = 2'd1,
        ACCESS_STACK = 2'd2,
        ACCESS_IDLE  = 2'd3
    } t_access_kind;

    // Requests and responses are sampled only in the 25 MHz system domain.
    // A requester holds every request field stable while valid is asserted and
    // until one response with valid asserted is sampled.
    typedef struct packed {
        logic         valid;
        logic         write;
        t_access_kind kind;
        t_address     address;
        t_data        write_data;
    } t_bus_request;

    typedef struct packed {
        logic  valid;
        logic  fault;
        t_data read_data;
    } t_bus_response;

    // Architectural observation only; functional control must not consume it.
    // The opcode is little-endian in instruction-byte order and opcode_length
    // says how many low bytes are meaningful.
    typedef struct packed {
        logic        valid;
        logic        is_interrupt;
        t_address    pc_before;
        t_address    pc_after;
        logic [23:0] opcode;
        logic [1:0]  opcode_length;
        t_data       a;
        t_data       f;
        t_data       b;
        t_data       c;
        t_data       d;
        t_data       e;
        t_data       h;
        t_data       l;
        logic [15:0] sp;
        logic        ime;
        logic        halted;
        logic        stopped;
    } t_retirement;
endpackage
