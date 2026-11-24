// Type-only package of nand2doom platform, I/O and debug-link blocks'
// module-private state records (RTL BKM "Types and shared constants live in
// packages"). It holds no constants or functions, so every closure that
// compiles one of these blocks lists it without unused-parameter lint,
// including the standalone video, keyboard and PMON targets that do not list
// n2d_pkg. Fields whose widths follow a block parameter (timeouts, burst
// words, framebuffer width) stay registers of their own beside the packaged
// record. Enum literals carry their block's prefix because they share one
// package scope.
package n2d_types_pkg;
    // n2d_debug_device_line_bridge phase.
    typedef enum logic [1:0] {
        LINE_BRIDGE_IDLE,
        LINE_BRIDGE_ISSUE,
        LINE_BRIDGE_RESPONSE
    } t_line_bridge_state;

    // n2d_debug_device_line_bridge state: one line transaction unpacked into
    // four word requests. address[25:17] and [3:0] are captured but unused.
    typedef struct packed {
        t_line_bridge_state state;
        logic               write;
        logic               palette;
        logic [25:0]        address;
        logic [127:0]       write_data;
        logic [15:0]        byte_enable;
        logic [1:0]         word;
        logic [127:0]       response_data;
        logic               line_response_valid;
    } t_line_bridge_regs;

    // n2d_framebuffer_vga software-port bookkeeping and bank control. The
    // scan lane select follows FULL_WIDTH and is a register of its own.
    typedef struct packed {
        logic response_valid;
        logic in_range;
        logic bank;
        logic front_bank;
        logic swap_pending;
    } t_fb_cpu;

    // n2d_framebuffer_vga two-stage pixel pipeline: timing/event delays plus
    // the palette lookup, whose RGB is the palette RAM's scan port output.
    typedef struct packed {
        logic [9:0] x_q, x_qq;
        logic [9:0] y_q, y_qq;
        logic       visible_q, visible_qq;
        logic       hsync_q, hsync_qq;
        logic       vsync_q, vsync_qq;
        logic       frame_start_q, frame_start_qq;
        logic       frame_end_q, frame_end_qq;
        logic       palette_committed;
    } t_fb_pipe;

    // n2d_framebuffer_vga palette aperture response, active bank, deferred
    // commit flag, and whether any commit has taken effect since reset.
    typedef struct packed {
        logic response_valid;
        logic active_bank;
        logic commit_pending;
        logic committed;
    } t_fb_pal;

    // Owner of the one line-memory transaction, one bit per MEM_OWNER_* PMON
    // event: fetch is bit 0 (event 87) through debug at bit 4 (event 91).
    typedef struct packed {
        logic debug;           // 91: the debug master
        logic data_write;      // 90: D-cache writeback or maintenance write
        logic data_prefetch;   // 89: D-cache next-line prefetch read
        logic data_read;       // 88: D-cache demand read
        logic fetch;           // 87: I-cache line fill
    } t_pmon_owner;

    // n2d_pmon_platform_events per-transaction tracking.
    typedef struct packed {
        logic        dcache_write;
        logic        icache_line_read_pending;
        logic        dcache_line_read_pending;
        logic        memory_busy;
        t_pmon_owner owner;
    } t_pmon_track;

    // n2d_ps2_keyboard physical receiver: synchronized-clock edge history,
    // frame assembly, prefix tracking and sticky diagnostics (asynchronous
    // reset per wiki/src/nand2doom/rtl/io/mas.md). The frame timeout counter follows
    // FRAME_TIMEOUT_CYCLES and is a register of its own.
    typedef struct packed {
        logic       ps2_clk_previous;
        logic       receiver_enable;
        logic       frame_active;
        logic [3:0] frame_bit_index;
        logic [7:0] frame_data;
        logic       frame_parity;
        logic       extended_prefix;
        logic       break_prefix;
        logic       parity_error;
        logic       frame_error;
        logic       timeout_error;
    } t_ps2_rx;

    // n2d_ps2_keyboard event FIFO bookkeeping (asynchronous reset); the
    // entries themselves are an unreset flop array written through the
    // canonical indexed write.
    typedef struct packed {
        logic [2:0] fifo_read_pointer;
        logic [2:0] fifo_write_pointer;
        logic [3:0] fifo_count;
        logic       overflow_error;
    } t_ps2_fifo;

    // n2d_ps2_keyboard byte-stream prefix qualifiers, independent of the
    // physical receiver's: only reset or the byte that completes the event
    // clears them.
    typedef struct packed {
        logic extended_prefix;
        logic break_prefix;
    } t_ps2_scan_inject;

    // n2d_system data-port owner of the outstanding CPU data request.
    typedef enum logic [2:0] {
        DATA_NONE,
        DATA_SDRAM,
        DATA_FRAMEBUFFER,
        DATA_PALETTE,
        DATA_SYSTEM,
        DATA_PS2,
        DATA_ERROR
    } t_system_data_owner;

    // n2d_system halt-time cache maintenance tracking.
    typedef struct packed {
        logic halt_request_prev;
        logic required;
        logic icache_complete;
        logic dcache_complete;
    } t_system_maint;

    // n2d_system debug memory port owner.
    typedef enum logic [1:0] {
        DEBUG_NONE,
        DEBUG_SDRAM,
        DEBUG_DEVICE
    } t_system_debug_owner;

    // n2d_system uncached system/video-control register file and response.
    typedef struct packed {
        logic        response_valid;
        logic [31:0] response_data;
        logic        response_error;
        logic [31:0] completion;
        logic [31:0] tick;
    } t_system_regs;

    // n2d_uart_bulk framing phase.
    typedef enum logic [3:0] {
        BULK_IDLE, BULK_RX_HEADER, BULK_RX_BODY, BULK_RX_CRC, BULK_CHECK, BULK_PREPARE,
        BULK_SEND_ENGINE, BULK_WAIT_ENGINE, BULK_CHECK_ENGINE,
        BULK_TX_HEADER, BULK_TX_DATA, BULK_TX_CRC, BULK_DRAIN, BULK_QUARANTINE, BULK_WAIT_OLD
    } t_bulk_state;

    // n2d_uart_bulk framing state: request header, CRC accumulators,
    // progress counters and the engine hand-off bookkeeping. The gap and
    // frame timers follow the timeout parameters and are registers of their
    // own.
    typedef struct packed {
        t_bulk_state state;
        logic [7:0]  version;
        logic [7:0]  command;
        logic [7:0]  seq;
        logic [7:0]  status;
        logic [31:0] address;
        logic [31:0] crc;
        logic [31:0] received_crc;
        logic [15:0] length;
        logic [15:0] received;
        logic [12:0] completed;
        logic [12:0] data_index;
        logic [3:0]  header_index;
        logic [1:0]  crc_index;
        logic [1:0]  phase;
        logic        quarantine;
        logic        busy_entry;
        logic [5:0]  chunk_bytes;
        logic [6:0]  engine_index;
        logic [7:0]  engine_crc;
        logic [7:0]  engine_status;
        logic [7:0]  engine_count;
        logic        engine_bad;
    } t_bulk_regs;

    // n2d_uart_debug_engine request parser phase.
    typedef enum logic [2:0] {
        DEBUG_RX_WAIT_SOF,
        DEBUG_RX_HEADER,
        DEBUG_RX_PAYLOAD,
        DEBUG_RX_DISCARD,
        DEBUG_RX_CHECKSUM
    } t_debug_rx_state;

    // n2d_uart_debug_engine executor phase.
    typedef enum logic [3:0] {
        DEBUG_EXEC_IDLE,
        DEBUG_EXEC_HALT_WAIT,
        DEBUG_EXEC_RESUME_WAIT,
        DEBUG_EXEC_ARB_WAIT,
        DEBUG_EXEC_MEM_REQ,
        DEBUG_EXEC_MEM_RSP,
        DEBUG_EXEC_COMMIT_WAIT,
        DEBUG_EXEC_ABORT_WAIT,
        DEBUG_EXEC_INPUT_WAIT
    } t_debug_exec_state;

    // n2d_uart_debug_engine request parser: frame state, header fields and
    // per-byte checksum. The payload index and length, the inter-byte
    // counter and the payload image follow MAX_WORDS or
    // INTERBYTE_TIMEOUT_CYCLES and are registers of their own.
    typedef struct packed {
        t_debug_rx_state rx_state;
        logic [3:0]      rx_header_index;
        logic [9:0]      rx_discard_remaining;
        logic [7:0]      rx_crc;
        logic [7:0]      req_version;
        logic [7:0]      req_command;
        logic [7:0]      req_sequence;
        logic [31:0]     req_address;
        logic [7:0]      req_count;
        logic [3:0]      req_byte_enable;
        logic            packet_valid;
        logic            packet_checksum_ok;
        logic            parser_error_valid;
        logic [7:0]      parser_error_status;
        logic [7:0]      parser_error_command;
        logic [7:0]      parser_error_sequence;
    } t_debug_rx;

    // n2d_uart_debug_engine discard-only parser used while service is
    // occupied. Its inter-byte counter is a register of its own.
    typedef struct packed {
        t_debug_rx_state busy_rx_state;
        logic [3:0]      busy_header_index;
        logic [9:0]      busy_payload_remaining;
        logic [7:0]      busy_crc;
        logic [7:0]      busy_command;
        logic [7:0]      busy_sequence;
        logic [7:0]      busy_count;
        logic            busy_packet_valid;
    } t_debug_busy;

    // n2d_uart_debug_engine executor and response serializer: command
    // sequencing and transaction pulses. The timeout counters, word and
    // response indices and the response image follow the block parameters
    // and are registers of their own.
    typedef struct packed {
        t_debug_exec_state exec_state;
        logic              transaction_active;
        logic [7:0]        abort_status;
        logic              busy_pending;
        logic [7:0]        busy_pending_command;
        logic [7:0]        busy_pending_sequence;
        logic              response_active;
        logic [7:0]        response_command;
        logic [7:0]        response_sequence;
        logic [7:0]        response_status;
        logic [7:0]        response_count;
        logic [7:0]        response_crc;
        logic              halt_req;
        logic              cache_invalidate;
        logic              reset_pc_valid;
        logic [31:0]       reset_pc;
        logic              txn_begin;
        logic              txn_commit;
        logic              txn_abort;
    } t_debug_exec;

    // n2d_uart_stream receive parser phase.
    typedef enum logic [1:0] {
        STREAM_RX_HUNT,
        STREAM_RX_HEADER,
        STREAM_RX_PAYLOAD,
        STREAM_RX_CRC
    } t_stream_rx_state;

    // n2d_uart_stream executor phase.
    typedef enum logic [2:0] {
        STREAM_X_IDLE,
        STREAM_X_ARB,
        STREAM_X_FETCH,
        STREAM_X_REQ,
        STREAM_X_RSP,
        STREAM_X_COMMIT,
        STREAM_X_ABORT
    } t_stream_x_state;

    // n2d_uart_stream received frame: header fields, the digest seed and the
    // receive verdict that decides whether the executor touches memory.
    typedef struct packed {
        logic [7:0]  kind;
        logic [15:0] seq;
        logic [31:0] address;
        logic [31:0] length;
        logic [31:0] seed;
        logic [7:0]  status;
    } t_stream_entry;

    // n2d_uart_stream block state: session/receive parser, two-entry
    // in-order queue, executor and reply serializer. The gap, session-idle
    // and operation timers follow the timeout parameters and are registers
    // of their own.
    typedef struct packed {
        logic                session;
        logic                closing;
        t_stream_rx_state    rx_state;
        logic [3:0]          header_index;
        logic [7:0]          header_crc;
        logic [31:0]         crc;
        t_stream_entry       frame;
        logic [10:0]         payload_bytes;
        logic [10:0]         payload_index;
        logic [31:0]         word_acc;
        logic [1:0]          crc_index;
        logic [23:0]         received_crc;
        logic                store;
        t_stream_entry [1:0] entries;
        logic                wr_ptr;
        logic                rd_ptr;
        logic [1:0]          count;
        t_stream_x_state     x_state;
        logic [31:0]         x_address;
        logic [31:0]         x_remaining;
        logic [7:0]          x_word;
        logic [3:0]          x_chunk_left;
        logic [31:0]         x_crc;
        logic [7:0]          x_abort_status;
        logic                txn_begin;
        logic                txn_commit;
        logic                txn_abort;
        logic [31:0]         committed;
        logic                reply_active;
        logic [3:0]          reply_index;
        logic [7:0]          reply_crc;
        logic [7:0]          reply_kind;
        logic [15:0]         reply_seq;
        logic [7:0]          reply_status;
        logic [31:0]         reply_value;
    } t_stream_regs;

    // n2d_uart_mem_port phase: a screen read, or one SDRAM word as one
    // debug-adapter transaction.
    typedef enum logic [2:0] {
        MEM_PORT_IDLE,
        MEM_PORT_SCREEN,
        MEM_PORT_SCREEN_WAIT,
        MEM_PORT_ARB,
        MEM_PORT_REQ,
        MEM_PORT_RSP,
        MEM_PORT_FINISH
    } t_mem_port_state;

    // n2d_uart_mem_port request, result and transaction pulses.
    typedef struct packed {
        t_mem_port_state state;
        logic            write;
        logic [31:0]     addr;
        logic [31:0]     wdata;
        logic [31:0]     rdata;
        logic            error;
        logic            rsp_valid;
        logic            txn_begin;
        logic            txn_commit;
        logic            txn_abort;
    } t_mem_port_regs;
endpackage
