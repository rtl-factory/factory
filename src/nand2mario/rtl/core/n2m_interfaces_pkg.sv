package n2m_interfaces_pkg;
  localparam int unsigned ADDRESS_WIDTH = 16;
  localparam int unsigned DATA_WIDTH = 8;

  typedef logic [ADDRESS_WIDTH-1:0] address_t;
  typedef logic [DATA_WIDTH-1:0] data_t;

  typedef enum logic [1:0] {
    ACCESS_FETCH = 2'd0,
    ACCESS_DATA  = 2'd1,
    ACCESS_STACK = 2'd2,
    ACCESS_IDLE  = 2'd3
  } access_kind_t;

  // Requests and responses are sampled only in the 25 MHz system domain.
  // A requester holds every request field stable while valid is asserted and
  // until one response with valid asserted is sampled.
  typedef struct packed {
    logic         valid;
    logic         write;
    access_kind_t kind;
    address_t     address;
    data_t        write_data;
  } bus_request_t;

  typedef struct packed {
    logic  valid;
    logic  fault;
    data_t read_data;
  } bus_response_t;

  // Architectural observation only; functional control must not consume it.
  // The opcode is little-endian in instruction-byte order and opcode_length
  // says how many low bytes are meaningful.
  typedef struct packed {
    logic        valid;
    logic        is_interrupt;
    address_t    pc_before;
    address_t    pc_after;
    logic [23:0] opcode;
    logic [1:0]  opcode_length;
    data_t       a;
    data_t       f;
    data_t       b;
    data_t       c;
    data_t       d;
    data_t       e;
    data_t       h;
    data_t       l;
    logic [15:0] sp;
    logic        ime;
    logic        halted;
    logic        stopped;
  } retirement_t;
endpackage
