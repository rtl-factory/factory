"""Independent Python model of the nand2mario display tier.

Written from the display HAS, not from the RTL: the frame-bridge bank
policy (complete, swap at the raster origin, drop, repeat, LCD-off blank),
the 3x scaled and centered 640x480 image with the grey palette and the
black border, the VGA sync levels of the common 640x480 timing and the
analysis of a captured output frame, and the frame-snapshot record and pin
policy. Nothing here reads RTL state.
"""
import struct
import zlib

FRAME_WIDTH, FRAME_HEIGHT = 160, 144
FRAME_PIXELS = FRAME_WIDTH * FRAME_HEIGHT
SCALE = 3
VGA_WIDTH, VGA_HEIGHT = 640, 480
IMAGE_WIDTH, IMAGE_HEIGHT = FRAME_WIDTH * SCALE, FRAME_HEIGHT * SCALE          # 480 x 432
IMAGE_LEFT, IMAGE_TOP = (VGA_WIDTH - IMAGE_WIDTH) // 2, (VGA_HEIGHT - IMAGE_HEIGHT) // 2   # 80, 24

# Common n2a_vga_timing defaults: 800 x 525 total, active-low syncs.
H_VISIBLE, H_FRONT_PORCH, H_SYNC, H_BACK_PORCH = 640, 16, 96, 48
V_VISIBLE, V_FRONT_PORCH, V_SYNC, V_BACK_PORCH = 480, 10, 2, 33
H_TOTAL = H_VISIBLE + H_FRONT_PORCH + H_SYNC + H_BACK_PORCH    # 800
V_TOTAL = V_VISIBLE + V_FRONT_PORCH + V_SYNC + V_BACK_PORCH    # 525
FRAME_CLOCKS = H_TOTAL * V_TOTAL                               # 420000
OUTPUT_LATENCY = 2    # output clocks after the timing coordinate

# DMG shade (0 lightest) to 4:4:4 grey; border and blanking are black.
PALETTE = {0: (15, 15, 15), 1: (10, 10, 10), 2: (5, 5, 5), 3: (0, 0, 0)}
WHITE = PALETTE[0]
BORDER = (0, 0, 0)
BLANK = (0, 0, 0)


def sync_levels(x, y):
    """(hsync, vsync) at raster coordinate (x, y): low inside the sync segments."""
    hsync = int(not (H_VISIBLE + H_FRONT_PORCH <= x < H_VISIBLE + H_FRONT_PORCH + H_SYNC))
    vsync = int(not (V_VISIBLE + V_FRONT_PORCH <= y < V_VISIBLE + V_FRONT_PORCH + V_SYNC))
    return hsync, vsync


def in_image(x, y):
    return IMAGE_LEFT <= x < IMAGE_LEFT + IMAGE_WIDTH and IMAGE_TOP <= y < IMAGE_TOP + IMAGE_HEIGHT


def expected_rgb(frame, x, y):
    """RGB at raster coordinate (x, y) for a displayed ``frame`` (144 rows of
    160 shades) or a blank display (``frame`` None): black outside the
    visible area and in the border, white in the image window while blank,
    else the palette shade of source pixel ((x - 80) // 3, (y - 24) // 3)."""
    if not (x < H_VISIBLE and y < V_VISIBLE):
        return BLANK
    if not in_image(x, y):
        return BORDER
    if frame is None:
        return WHITE
    return PALETTE[frame[(y - IMAGE_TOP) // SCALE][(x - IMAGE_LEFT) // SCALE]]


def expected_line(frame, y):
    """Every output clock of raster line ``y`` as (hsync, vsync, (r, g, b))."""
    return [(*sync_levels(x, y), expected_rgb(frame, x, y)) for x in range(H_TOTAL)]


def expected_frame(frame):
    """Every output clock of one frame as (hsync, vsync, (r, g, b)), in
    raster order from coordinate (0, 0)."""
    clocks = []
    for y in range(V_TOTAL):
        clocks.extend(expected_line(frame, y))
    return clocks


def compare_clocks(observed, expected, first_clock=0, label='frame'):
    """Exact comparison of captured output clocks with the first mismatch
    named by its raster coordinate."""
    assert len(observed) == len(expected), (label, len(observed), len(expected))
    for offset, (got, want) in enumerate(zip(observed, expected)):
        if got != want:
            clock = first_clock + offset
            raise AssertionError(f'{label}: clock {clock} coordinate ({clock % H_TOTAL},{clock // H_TOTAL}) '
                                 f'observed (hsync,vsync,rgb)={got} expected {want}')


class FrameBridgeModel:
    """Bank policy as events. Frames are opaque identities; ``complete`` is the
    pixel that completes one, ``origin`` the VGA raster origin. The LCD level
    is sampled with every event."""

    def __init__(self):
        self.ready = None        # fresh complete frame waiting for the origin
        self.displayed = None    # frame on screen, None while blank
        self.swaps = self.drops = self.repeats = 0

    def lcd_off(self):
        self.ready = None

    def complete(self, frame, lcd_on=True):
        """The frame becomes the ready frame (dropping a fresh one); with the
        LCD off it is discarded immediately and never becomes ready."""
        if self.ready is not None:
            self.drops += 1
        self.ready = frame if lcd_on else None

    def origin(self, lcd_on=True):
        """Returns 'swap', 'repeat', 'blank' or None (blank display, nothing
        to show)."""
        if not lcd_on:
            self.ready = None
            self.displayed = None
            return 'blank'
        if self.ready is not None:
            self.displayed, self.ready = self.ready, None
            self.swaps += 1
            return 'swap'
        if self.displayed is not None:
            self.repeats += 1
            return 'repeat'
        return None


# Frame snapshot record (display HAS, frame snapshot).
SNAPSHOT_MAGIC = b'NM'
SNAPSHOT_VERSION = 1
SNAPSHOT_HEADER_BYTES = 8
SNAPSHOT_RECORD_BYTES = SNAPSHOT_HEADER_BYTES + FRAME_PIXELS // 4     # 5768


def snapshot_header(frame_id, bank, on_screen=True):
    return SNAPSHOT_MAGIC + bytes([SNAPSHOT_VERSION, (on_screen << 7) | bank]) + frame_id.to_bytes(4, 'little')


def snapshot_record(frame, frame_id, bank, on_screen=True):
    """The 5,768-byte record of a pinned ``frame`` (144 rows of 160 shades):
    header, then four row-major shades per byte, pixel 4m+j in bits [2j+1:2j]."""
    shades = [shade for row in frame for shade in row]
    pixels = bytes(sum(shades[4 * m + j] << (2 * j) for j in range(4)) for m in range(FRAME_PIXELS // 4))
    return snapshot_header(frame_id, bank, on_screen) + pixels


class SnapshotBridgeModel(FrameBridgeModel):
    """The bank policy with frame IDs and the snapshot pin. Frames are
    numbered by completion (the first is 1, LCD-off completions included);
    while pinned an origin that would swap repeats instead and the fresh
    frame stays fresh. ``pin``/``release`` are applied before a same-cycle
    origin, as the HAS orders them."""

    def __init__(self):
        super().__init__()
        self.count = 0
        self.ready_id = None
        self.displayed_id = None   # ID of the frame in the displayed bank (kept when blanked)
        self.valid = False
        self.pinned = False

    def complete(self, frame, lcd_on=True):
        self.count += 1
        super().complete(frame, lcd_on)
        self.ready_id = self.count if lcd_on else None

    def pin(self):
        if self.valid:
            self.pinned = True

    def release(self):
        self.pinned = False

    def origin(self, lcd_on=True):
        if not lcd_on:
            self.ready = None
            self.valid = False
            return 'blank'
        if self.ready is not None and not self.pinned:
            self.displayed, self.ready = self.ready, None
            self.displayed_id, self.valid = self.ready_id, True
            self.swaps += 1
            return 'swap'
        if self.valid:
            self.repeats += 1
            return 'repeat'
        return None

    def reset(self):
        self.__init__()


def write_rgb_png(path, rows):
    """Save rows of 4:4:4 (r, g, b) tuples as an 8-bit RGB PNG (standard library only)."""
    raw = b''.join(b'\x00' + bytes(level * 17 for pixel in row for level in pixel) for row in rows)

    def chunk(kind, data):
        body = kind + data
        return struct.pack('>I', len(data)) + body + struct.pack('>I', zlib.crc32(body) & 0xFFFFFFFF)

    header = struct.pack('>IIBBBBB', len(rows[0]), len(rows), 8, 2, 0, 0, 0)
    png = b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', header) + chunk(b'IDAT', zlib.compress(raw, 9)) + chunk(b'IEND', b'')
    path.write_bytes(png)


def visible_rows(clocks):
    """The 480 x 640 RGB image of one captured frame (raster order from (0, 0))."""
    return [[clocks[y * H_TOTAL + x][2] for x in range(H_VISIBLE)] for y in range(V_VISIBLE)]
