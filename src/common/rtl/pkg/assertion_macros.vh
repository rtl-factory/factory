`ifndef N2A_ASSERTION_MACROS_VH
`define N2A_ASSERTION_MACROS_VH

`include "src/common/rtl/pkg/macros.vh"

// Canonical assertion vocabulary. Product RTL never writes `ifdef SYNTHESIS,
// a clocked assertion process, or a raw assert: the conditional inclusion,
// sampling class, and failure severity of every form are fixed here.
//
// Guards:  SYNTHESIS omits the simulation-only forms; N2A_DISABLE_ASSERTIONS
//          omits only the error-severity concurrent family (it is unaffected
//          by SYNTHESIS); the *_UNGUARDED forms are always present.
// Sampling: concurrent forms sample in the preponed region with
//          `disable iff (RESET)`; clocked forms evaluate active-region values
//          inside a macro-owned always_ff that holds no state and runs only
//          when !(RESET).

// ---------------------------------------------------------------------------
// Concurrent, fatal severity (Verilator returns nonzero), simulation only.
`ifndef SYNTHESIS
  `define N2A_ASSERT(NAME, CLK, RESET, PROPERTY) \
    NAME: assert property (@(posedge (CLK)) disable iff (RESET) (PROPERTY)) \
      else $fatal(1, "N2A assertion failed");

  `define N2A_ASSERT_NO_RESET(NAME, CLK, PROPERTY) \
    NAME: assert property (@(posedge (CLK)) (PROPERTY)) \
      else $fatal(1, "N2A assertion failed");

  // Default failure action (message and continue), no severity escalation.
  `define N2A_ASSERT_PROPERTY(NAME, CLK, RESET, PROPERTY) \
    NAME: assert property (@(posedge (CLK)) disable iff (RESET) (PROPERTY));

  `define N2A_ASSERT_PROPERTY_NO_RESET(NAME, CLK, PROPERTY) \
    NAME: assert property (@(posedge (CLK)) (PROPERTY));

  // Armed one edge after RESET releases: NAME_armed_q is assertion state only.
  `define N2A_ASSERT_AFTER_RESET(NAME, CLK, RESET, PROPERTY) \
    logic NAME``_armed_q; \
    `N2A_DFF_ARST_VAL(NAME``_armed_q, 1'b1, CLK, RESET, 1'b0) \
    `N2A_ASSERT(NAME, CLK, !NAME``_armed_q, PROPERTY)

  // Simulation-only support register for assertions (counters, history).
  // NEXT must be an inline expression; nothing outside assertions may read NAME.
  `define N2A_ASSERT_HISTORY_RST(TYPE, NAME, NEXT, CLK, RESET) \
    TYPE NAME; \
    `N2A_DFF_RST(NAME, NEXT, CLK, RESET)
`else
  `define N2A_ASSERT(NAME, CLK, RESET, PROPERTY)
  `define N2A_ASSERT_NO_RESET(NAME, CLK, PROPERTY)
  `define N2A_ASSERT_PROPERTY(NAME, CLK, RESET, PROPERTY)
  `define N2A_ASSERT_PROPERTY_NO_RESET(NAME, CLK, PROPERTY)
  `define N2A_ASSERT_AFTER_RESET(NAME, CLK, RESET, PROPERTY)
  `define N2A_ASSERT_HISTORY_RST(TYPE, NAME, NEXT, CLK, RESET)
`endif

`define N2A_ASSERT_NEVER(NAME, CLK, RESET, CONDITION) \
  `N2A_ASSERT(NAME, CLK, RESET, !(CONDITION))

`define N2A_ASSERT_KNOWN(NAME, CLK, RESET, SIGNAL) \
  `N2A_ASSERT(NAME, CLK, RESET, !$isunknown(SIGNAL))

// ---------------------------------------------------------------------------
// Immediate assertions.
// Transition-only: procedural forms inside a caller-owned process. The caller
// supplies the semicolon and keeps its own synthesis guard. Product RTL uses
// the clocked forms below instead.
`ifndef SYNTHESIS
  `define N2A_ASSERT_IMMEDIATE(CONDITION) assert (CONDITION)
  `define N2A_ASSERT_IMMEDIATE_FATAL(CONDITION, MESSAGE) \
    assert (CONDITION) else $fatal(1, MESSAGE)
`else
  `define N2A_ASSERT_IMMEDIATE(CONDITION)
  `define N2A_ASSERT_IMMEDIATE_FATAL(CONDITION, MESSAGE)
`endif

// Clocked immediate forms: active-region sampling at posedge CLK while
// !(RESET), exactly like an assert in the non-reset branch of a clocked
// process. RESET may be 1'b0 for an unconditional check. The *_WHEN forms
// keep an original nested `if (GUARD) assert (x)` as a real nested branch:
// rewriting it as `!(GUARD) || x` is not four-state exact when GUARD is X.
`define N2A_ASSERT_CLOCKED_FATAL_UNGUARDED(CLK, RESET, CONDITION, MESSAGE) \
  always_ff @(posedge (CLK)) begin \
    if (!(RESET)) begin \
      assert (CONDITION) else $fatal(1, MESSAGE); \
    end \
  end

`define N2A_ASSERT_CLOCKED_ERROR_UNGUARDED(CLK, RESET, CONDITION, MESSAGE) \
  always_ff @(posedge (CLK)) begin \
    if (!(RESET)) begin \
      assert (CONDITION) else $error(MESSAGE); \
    end \
  end

`define N2A_ASSERT_CLOCKED_FATAL_WHEN_UNGUARDED(CLK, RESET, GUARD, CONDITION, MESSAGE) \
  always_ff @(posedge (CLK)) begin \
    if (!(RESET)) begin \
      if (GUARD) begin \
        assert (CONDITION) else $fatal(1, MESSAGE); \
      end \
    end \
  end

`define N2A_ASSERT_CLOCKED_ERROR_WHEN_UNGUARDED(CLK, RESET, GUARD, CONDITION, MESSAGE) \
  always_ff @(posedge (CLK)) begin \
    if (!(RESET)) begin \
      if (GUARD) begin \
        assert (CONDITION) else $error(MESSAGE); \
      end \
    end \
  end

// Asynchronous-reset processes: the original assert lived in the else branch
// of `always_ff @(posedge CLK or posedge RESET) if (RESET) ... else ...`.
// These forms keep that exact shape: the first condition tests RESET with
// the polarity of its edge (required by Quartus template matching, Error
// 10200 otherwise) and the body sits in the else branch, so a four-state
// RESET of X takes the else branch exactly as the original process did. A
// RESET asserted to 1 inhibits the body at any event; at a CLK edge (or a
// four-state 0->X reset event) the body runs when RESET is not 1. Two-state
// simulation cannot observe the X case.
`define N2A_ASSERT_CLOCKED_ARST_FATAL_UNGUARDED(CLK, RESET, CONDITION, MESSAGE) \
  always_ff @(posedge (CLK) or posedge (RESET)) begin \
    if (RESET) begin \
    end else begin \
      assert (CONDITION) else $fatal(1, MESSAGE); \
    end \
  end

`define N2A_ASSERT_CLOCKED_ARST_FATAL_WHEN_UNGUARDED(CLK, RESET, GUARD, CONDITION, MESSAGE) \
  always_ff @(posedge (CLK) or posedge (RESET)) begin \
    if (RESET) begin \
    end else begin \
      if (GUARD) begin \
        assert (CONDITION) else $fatal(1, MESSAGE); \
      end \
    end \
  end

`ifndef SYNTHESIS
  `define N2A_ASSERT_CLOCKED(CLK, RESET, CONDITION) \
    always_ff @(posedge (CLK)) begin \
      if (!(RESET)) begin \
        assert (CONDITION); \
      end \
    end

  `define N2A_ASSERT_CLOCKED_WHEN(CLK, RESET, GUARD, CONDITION) \
    always_ff @(posedge (CLK)) begin \
      if (!(RESET)) begin \
        if (GUARD) begin \
          assert (CONDITION); \
        end \
      end \
    end

  `define N2A_ASSERT_CLOCKED_FATAL(CLK, RESET, CONDITION, MESSAGE) \
    `N2A_ASSERT_CLOCKED_FATAL_UNGUARDED(CLK, RESET, CONDITION, MESSAGE)

  `define N2A_ASSERT_CLOCKED_ERROR(CLK, RESET, CONDITION, MESSAGE) \
    `N2A_ASSERT_CLOCKED_ERROR_UNGUARDED(CLK, RESET, CONDITION, MESSAGE)

  `define N2A_ASSERT_CLOCKED_FATAL_WHEN(CLK, RESET, GUARD, CONDITION, MESSAGE) \
    `N2A_ASSERT_CLOCKED_FATAL_WHEN_UNGUARDED(CLK, RESET, GUARD, CONDITION, MESSAGE)

  `define N2A_ASSERT_CLOCKED_ERROR_WHEN(CLK, RESET, GUARD, CONDITION, MESSAGE) \
    `N2A_ASSERT_CLOCKED_ERROR_WHEN_UNGUARDED(CLK, RESET, GUARD, CONDITION, MESSAGE)

  `define N2A_ASSERT_CLOCKED_ARST(CLK, RESET, CONDITION) \
    always_ff @(posedge (CLK) or posedge (RESET)) begin \
      if (RESET) begin \
      end else begin \
        assert (CONDITION); \
      end \
    end

  `define N2A_ASSERT_CLOCKED_ARST_WHEN(CLK, RESET, GUARD, CONDITION) \
    always_ff @(posedge (CLK) or posedge (RESET)) begin \
      if (RESET) begin \
      end else begin \
        if (GUARD) begin \
          assert (CONDITION); \
        end \
      end \
    end

  `define N2A_ASSERT_CLOCKED_ARST_FATAL(CLK, RESET, CONDITION, MESSAGE) \
    `N2A_ASSERT_CLOCKED_ARST_FATAL_UNGUARDED(CLK, RESET, CONDITION, MESSAGE)

  `define N2A_ASSERT_CLOCKED_ARST_FATAL_WHEN(CLK, RESET, GUARD, CONDITION, MESSAGE) \
    `N2A_ASSERT_CLOCKED_ARST_FATAL_WHEN_UNGUARDED(CLK, RESET, GUARD, CONDITION, MESSAGE)

  // Time-zero default-action check, simulation only.
  `define N2A_ASSERT_INITIAL(CONDITION) \
    initial begin \
      assert (CONDITION); \
    end
`else
  `define N2A_ASSERT_CLOCKED(CLK, RESET, CONDITION)
  `define N2A_ASSERT_CLOCKED_WHEN(CLK, RESET, GUARD, CONDITION)
  `define N2A_ASSERT_CLOCKED_FATAL(CLK, RESET, CONDITION, MESSAGE)
  `define N2A_ASSERT_CLOCKED_ERROR(CLK, RESET, CONDITION, MESSAGE)
  `define N2A_ASSERT_CLOCKED_FATAL_WHEN(CLK, RESET, GUARD, CONDITION, MESSAGE)
  `define N2A_ASSERT_CLOCKED_ERROR_WHEN(CLK, RESET, GUARD, CONDITION, MESSAGE)
  `define N2A_ASSERT_CLOCKED_ARST(CLK, RESET, CONDITION)
  `define N2A_ASSERT_CLOCKED_ARST_WHEN(CLK, RESET, GUARD, CONDITION)
  `define N2A_ASSERT_CLOCKED_ARST_FATAL(CLK, RESET, CONDITION, MESSAGE)
  `define N2A_ASSERT_CLOCKED_ARST_FATAL_WHEN(CLK, RESET, GUARD, CONDITION, MESSAGE)
  `define N2A_ASSERT_INITIAL(CONDITION)
`endif

// ---------------------------------------------------------------------------
// Elaboration check: module scope only (it owns its generate region). A false
// CONDITION terminates simulation at time zero with MESSAGE, in every
// configuration, like a generate `initial $fatal` block.
`define N2A_ASSERT_PARAM(LABEL, CONDITION, MESSAGE) \
  generate \
    if (!(CONDITION)) begin : LABEL \
      initial $fatal(1, MESSAGE); \
    end \
  endgenerate

// ---------------------------------------------------------------------------
// Concurrent, error severity ($error, simulation continues). Disabled only by
// N2A_DISABLE_ASSERTIONS; SYNTHESIS does not remove them. Define the switch
// before either header is included.
`ifndef N2A_DISABLE_ASSERTIONS

`define N2A_ASSERT_ERROR(NAME, CLK, RESET, PROPERTY) \
    NAME: assert property (@(posedge CLK) disable iff (RESET) (PROPERTY)) \
        else $error("N2A_ASSERT %m.%s failed", `"NAME`");

`define N2A_ASSERT_ERROR_NO_RESET(NAME, CLK, PROPERTY) \
    NAME: assert property (@(posedge CLK) (PROPERTY)) \
        else $error("N2A_ASSERT %m.%s failed", `"NAME`");

`define N2A_ASSERT_ERROR_NEVER(NAME, CLK, RESET, CONDITION) \
    `N2A_ASSERT_ERROR(NAME, CLK, RESET, !(CONDITION))

`define N2A_ASSERT_ERROR_KNOWN(NAME, CLK, RESET, SIGNAL) \
    `N2A_ASSERT_ERROR(NAME, CLK, RESET, !$isunknown(SIGNAL))

// A hold prevents the *next* update, so the hold term must be observed one
// cycle before $stable is checked. The term is sampled through a flop clocked
// like the held register instead of $past(HOLD): the flop and the DFF_RST_EN it
// guards then see the same value of HOLD at the same edge, even when a
// testbench changes an input of HOLD with a blocking assignment at that edge.
// Under Verilator --timing such a testbench process resumes before the
// clocked logic, so the flops advance while $past(HOLD) still holds the
// pre-edge sample; a preponed-sampling simulator has the same TB/DUT race on
// the flop, so only the flopped hold term is consistent in both.
// The declared NAME_held_prev flop is part of the assertion, not the design.
`define N2A_ASSERT_ERROR_STABLE_WHEN(NAME, CLK, RESET, HOLD, SIGNAL) \
    logic NAME``_held_prev; \
    `N2A_DFF_RST(NAME``_held_prev, (HOLD), CLK, RESET) \
    `N2A_ASSERT_ERROR(NAME, CLK, RESET, NAME``_held_prev |-> $stable(SIGNAL))

`else

`define N2A_ASSERT_ERROR(NAME, CLK, RESET, PROPERTY)
`define N2A_ASSERT_ERROR_NO_RESET(NAME, CLK, PROPERTY)
`define N2A_ASSERT_ERROR_NEVER(NAME, CLK, RESET, CONDITION)
`define N2A_ASSERT_ERROR_KNOWN(NAME, CLK, RESET, SIGNAL)
`define N2A_ASSERT_ERROR_STABLE_WHEN(NAME, CLK, RESET, HOLD, SIGNAL)

`endif

// Transition-only aliases for the cached-Linux call sites; deleted once
// nand2linux uses the N2A_ASSERT_ERROR names.
`define N2A_COMPAT_ASSERT(NAME, CLK, RST, EXPR) `N2A_ASSERT_ERROR(NAME, CLK, RST, EXPR)
`define N2A_COMPAT_ASSERT_NO_RST(NAME, CLK, EXPR) `N2A_ASSERT_ERROR_NO_RESET(NAME, CLK, EXPR)
`define N2A_COMPAT_ASSERT_NEVER(NAME, CLK, RST, EXPR) `N2A_ASSERT_ERROR_NEVER(NAME, CLK, RST, EXPR)
`define N2A_COMPAT_ASSERT_KNOWN(NAME, CLK, RST, SIG) `N2A_ASSERT_ERROR_KNOWN(NAME, CLK, RST, SIG)
`define N2A_COMPAT_ASSERT_STABLE_WHEN(NAME, CLK, RST, HOLD, SIG) `N2A_ASSERT_ERROR_STABLE_WHEN(NAME, CLK, RST, HOLD, SIG)

`endif
