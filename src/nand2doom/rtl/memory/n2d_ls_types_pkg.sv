// Type-only package of nand2doom load/store blocks' module-private state
// records (RTL BKM "Types and shared constants live in packages"). It holds
// no constants or functions, so every closure that compiles one of these
// blocks lists it without unused-parameter lint. Fields whose widths follow
// a block parameter (cache ways, prefetch slots, debug burst words) stay
// registers of their own beside the packaged record. Fixed widths that a
// block also names as a localparam are written here as literals: 9-bit set
// indices (512 sets) and 22-bit line addresses (16-byte lines in 64 MiB).
package n2d_ls_types_pkg;
    // n2d_icache control phase.
    typedef enum logic [2:0] {
        ICACHE_IDLE,
        ICACHE_LOOKUP,
        ICACHE_LINE_REQUEST,
        ICACHE_LINE_RESPONSE,
        ICACHE_INVALIDATE
    } t_icache_state;

    // n2d_icache control state, captured request and registered (miss/error)
    // CPU and maintenance outputs (pulses reset low like every other field).
    typedef struct packed {
        t_icache_state state;
        logic [31:0]   request_address;
        logic          victim_way;
        logic [8:0]    invalidate_set;
        logic          cpu_response_valid;
        logic [31:0]   cpu_response_data;
        logic          cpu_response_error;
        logic          maintenance_done;
        logic          hit_event;
        logic          miss_event;
    } t_icache_regs;

    // n2d_dcache control phase. The maintenance states stay last: the RAM
    // read address selects the walk set for every state at or after
    // DCACHE_MAINTENANCE_READ.
    typedef enum logic [3:0] {
        DCACHE_IDLE,
        DCACHE_LOOKUP,
        DCACHE_REFILL_REQUEST,
        DCACHE_REFILL_RESPONSE,
        DCACHE_MAINTENANCE_DRAIN,
        DCACHE_MAINTENANCE_READ,
        DCACHE_MAINTENANCE_SCAN,
        DCACHE_MAINTENANCE_WRITEBACK_REQUEST,
        DCACHE_MAINTENANCE_WRITEBACK_RESPONSE
    } t_dcache_state;

    // n2d_dcache control state, captured request, the store-hit forward
    // record, the maintenance walk and registered (miss/error) CPU and
    // maintenance outputs (pulses reset low like every field). The victim
    // and forward ways, the metadata words and the prefetch buffer follow
    // WAYS or PREFETCH_LINES and are registers of their own in the cache.
    // line_buffer holds the dirty victim on a miss; after a store hit it holds
    // the written line for the forward record. The two uses never overlap: a
    // miss captures its victim on the edge that ends the forwarded lookup.
    typedef struct packed {
        t_dcache_state state;
        logic [31:2]   request_address;
        logic          request_write;
        logic [31:0]   request_write_data;
        logic [3:0]    request_byte_enable;
        // Post-reset metadata sweep: init_set is the next word to clear.
        logic          init_done;
        logic [8:0]    init_set;
        // The set the metadata RAM read on the last edge and whether a word
        // was written on that same edge.
        logic [8:0]    meta_read_set;
        logic          meta_forward_valid;
        logic [8:0]    meta_forward_set;
        logic [127:0]  line_buffer;
        // One displaced dirty line. An install transfers ownership only
        // after the refill succeeds; on refill error the array still owns it.
        logic          miss_dirty;
        logic          write_buffer_valid;
        logic [21:0]   write_buffer_line;
        logic [127:0]  write_buffer_data;
        // Once selected, the drain holds its payload through acceptance and
        // response, even if a new demand appears. Failed drains retain data.
        logic          write_buffer_active;
        logic          write_buffer_accepted;
        logic          write_buffer_failed;
        logic          forward_valid;
        logic [8:0]    forward_set;
        logic [8:0]    maintenance_set;
        logic          cpu_response_valid;
        logic [31:0]   cpu_response_data;
        logic          cpu_response_error;
        logic          maintenance_done;
        logic          maintenance_error;
        logic          hit_event;
        logic          miss_event;
        // The one prefetch line transaction: presented until accepted, then
        // owed one response; discard drops that response (maintenance).
        logic          prefetch_valid;
        logic          prefetch_presented;
        logic          prefetch_accepted;
        logic          prefetch_discard;
        logic [21:0]   prefetch_line;
        logic          prefetch_issue_event;
        logic          prefetch_useful_event;
        logic          prefetch_cancel_event;
        // Dropped lines not yet reported; one is reported per clock.
        logic [2:0]    prefetch_drop_backlog;
    } t_dcache_regs;

    // n2d_debug_memory_adapter transaction phase.
    typedef enum logic [3:0] {
        DEBUG_MEM_IDLE,
        DEBUG_MEM_WORD_RESPONSE,
        DEBUG_MEM_WORD_READ_REQUEST,
        DEBUG_MEM_WORD_READ_RESPONSE,
        DEBUG_MEM_PREPARE_REQUEST,
        DEBUG_MEM_PREPARE_RESPONSE,
        DEBUG_MEM_PUBLISH_REQUEST,
        DEBUG_MEM_PUBLISH_RESPONSE,
        DEBUG_MEM_ROLLBACK_REQUEST,
        DEBUG_MEM_ROLLBACK_RESPONSE
    } t_debug_mem_state;

    // n2d_debug_memory_adapter scalar transaction state and registered
    // word/transaction responses. The staged-word counts and indices follow
    // MAX_WORDS and are registers of their own in the adapter.
    typedef struct packed {
        t_debug_mem_state state;
        logic             transaction;
        logic             abort_pending;
        logic [25:0]      read_address;
        logic             word_response_valid;
        logic [31:0]      word_response_data;
        logic             word_response_error;
        logic             transaction_done;
        logic             transaction_error;
    } t_debug_mem_regs;
endpackage
