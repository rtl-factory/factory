`default_nettype none

// nand2mario timer types owned by the timer block: the TIMA overflow
// reload sequence state of n2m_timer and its reset divider value.
package n2m_timer_pkg;
  // DMG ABC boot-ROM handoff: internal counter $ABCC T-cycles = $2AF3
  // machine cycles (DIV $AB).
  localparam logic [13:0] DIVIDER_RESET = 14'h2AF3;

  // Reload sequence after TIMA passes $FF: PENDING is the machine cycle in
  // which TIMA reads $00, HOLD the machine cycle in which TIMA has just been
  // reloaded (a TIMA write is ignored, a TMA write also lands in TIMA).
  typedef enum logic [1:0] {
    RELOAD_IDLE    = 2'd0,
    RELOAD_PENDING = 2'd1,
    RELOAD_HOLD    = 2'd2
  } timer_reload_t;
endpackage
