"""Native byte-copy safety bounds, independent of loader state/internal handles."""
CLOCK_NS = 40
FRAME_NS = 70224 * 1e9 / 4194304
RESET_CLOCKS = 8192
# Storage HAS: up to 5 refresh-ready clocks + 9 response clocks. The
# request/response copy engine adds at most 2 clocks. On-chip takes 2 total.
BYTE_CLOCKS = {'onchip': 2, 'sdram': 5 + 9 + 2}
MAX_IMAGE_BYTES = 65536
# Longest no-frame transition: MBC1 image immediately exits, then menu/window.
SWITCH_RETURN_BYTES = 65536 + 32768 + 16384
POLL_NS = 4000


def frame_wait_ns(backing):
    margin = SWITCH_RETURN_BYTES * BYTE_CLOCKS[backing] * CLOCK_NS if backing == 'sdram' else 0
    return 5 * FRAME_NS + margin


def reset_wait_ns(backing):
    # Metadata is conservatively included, although validation precedes reset.
    return ((32 + MAX_IMAGE_BYTES) * BYTE_CLOCKS[backing] + RESET_CLOCKS) * CLOCK_NS + POLL_NS
