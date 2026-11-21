"""Independent Python model of the nand2mario PPU.

Written from the video HAS, not from the RTL: the frame renderer maps VRAM,
OAM and the LCD registers to a 160x144 frame of two-bit shades (background,
window and objects with the DMG selection and priority rules), and the
timing model walks the 456-dot, 154-line raster, the STAT mode schedule
(the renderer's mode 3 runs from dot 80 to 256 plus the SCX fine scroll
latched at the end of dot 80; STAT reports mode 3 from dot 84, the mode-0
interrupt source rises 3 dots after STAT reads mode 0, and LY reads the
previous line for the first 4 dots of lines 1-153, fitted to the Mooneye
ppu/ timing ROMs, #483),
the LY=LYC coincidence, the STAT interrupt line and the VBlank entry.
Nothing here reads RTL state.
"""
import struct
import zlib

DOTS_PER_LINE = 456
LINES_PER_FRAME = 154
VISIBLE_LINES = 144
VISIBLE_PIXELS = 160
MODE2_DOTS = 80
MODE3_BASE_DOTS = 172
MODE0_IRQ_DELAY = 3
LY_READ_DOT = 452
PIXEL_DOT = 92
DOTS_PER_FRAME = DOTS_PER_LINE * LINES_PER_FRAME

HBLANK, VBLANK, OAM_SCAN, TRANSFER = 0, 1, 2, 3

LCDC, STAT, SCY, SCX, LY, LYC, BGP = 0xFF40, 0xFF41, 0xFF42, 0xFF43, 0xFF44, 0xFF45, 0xFF47
OBP0, OBP1, WY, WX = 0xFF48, 0xFF49, 0xFF4A, 0xFF4B
PPU_REGISTERS = (LCDC, STAT, SCY, SCX, LY, LYC, BGP, OBP0, OBP1, WY, WX)
STORED_REGISTERS = (LCDC, SCY, SCX, LYC, BGP, OBP0, OBP1, WY, WX)

LCDC_ON, LCDC_WINDOW_MAP, LCDC_WINDOW, LCDC_TILE_DATA = 0x80, 0x40, 0x20, 0x10
LCDC_BG_MAP, LCDC_OBJ_SIZE, LCDC_OBJ, LCDC_BG = 0x08, 0x04, 0x02, 0x01
ATTR_PRIORITY, ATTR_Y_FLIP, ATTR_X_FLIP, ATTR_PALETTE = 0x80, 0x40, 0x20, 0x10
OBJECTS_PER_LINE = 10
WINDOW_LAST_WX = 166

# Ref: Pan Docs "LCD Status Registers": the enable bits sit in STAT[6:3].
STAT_LYC, STAT_MODE2, STAT_MODE1, STAT_MODE0 = 0x40, 0x20, 0x10, 0x08


def palette_shade(bgp, color):
    return (bgp >> (2 * color)) & 3


def map_offset(lcdc, row, column):
    """VRAM offset ($8000-relative) of a tile-map entry."""
    base = 0x1C00 if lcdc & 0x08 else 0x1800
    return base + (row & 31) * 32 + (column & 31)


def tile_offset(lcdc, tile, row, high_plane):
    """VRAM offset of one plane byte of one tile row."""
    if lcdc & 0x10:
        base = tile * 16
    else:
        base = 0x1000 + (tile - 256 if tile >= 128 else tile) * 16
    return base + row * 2 + int(high_plane)


def background_color(vram, lcdc, bx, by):
    """Raw two-bit color of background position (bx, by) in the 256x256 map."""
    bx &= 0xFF
    by &= 0xFF
    tile = vram[map_offset(lcdc, by >> 3, bx >> 3)]
    low = vram[tile_offset(lcdc, tile, by & 7, False)]
    high = vram[tile_offset(lcdc, tile, by & 7, True)]
    bit = 7 - (bx & 7)
    return ((high >> bit) & 1) << 1 | ((low >> bit) & 1)


def window_color(vram, lcdc, column, line):
    """Raw two-bit color of window position (column, line); the window map is
    LCDC.6, its tiles use the LCDC.4 addressing like the background."""
    map_select = lcdc | 0x08 if lcdc & LCDC_WINDOW_MAP else lcdc & ~0x08
    tile = vram[map_offset(map_select, line >> 3, column >> 3)]
    low = vram[tile_offset(lcdc, tile, line & 7, False)]
    high = vram[tile_offset(lcdc, tile, line & 7, True)]
    bit = 7 - (column & 7)
    return ((high >> bit) & 1) << 1 | ((low >> bit) & 1)


def window_lines(lcdc, wy, wx):
    """{LY: window line} for the lines that draw the window in a frame with
    static registers: the WY latch sets at the start of the line LY == WY
    while the window is enabled, every later line of the frame draws the
    window when LCDC.5 is set and WX is at most 166, and the window line
    counter advances only on such lines."""
    lines, hit, counter = {}, False, 0
    for ly in range(VISIBLE_LINES):
        if lcdc & LCDC_WINDOW and ly == wy:
            hit = True
        if lcdc & LCDC_WINDOW and hit and wx <= WINDOW_LAST_WX:
            lines[ly] = counter
            counter += 1
    return lines


def window_first_pixel(wx):
    """First pixel of a window line; WX below 7 starts at pixel 0 with the
    window's leftmost 7 - WX columns cut off."""
    return max(0, wx - 7)


def line_objects(oam, lcdc, ly):
    """(x, row, tile, attributes) of the objects kept for line ``ly`` in
    OAM order: an entry is on the line when LY + 16 - Y (eight bits) is below
    its height, X is not examined, at most ten are kept. The second result
    is the number of entries on the line before the limit."""
    assert len(oam) == 160
    height = 16 if lcdc & LCDC_OBJ_SIZE else 8
    kept, on_line = [], 0
    for index in range(40):
        y, x, tile, attributes = oam[4 * index:4 * index + 4]
        distance = (ly + 16 - y) & 0xFF
        if distance < height:
            on_line += 1
            if len(kept) < OBJECTS_PER_LINE:
                kept.append((x, distance, tile, attributes))
    return kept, on_line


def object_color(vram, lcdc, entry, x):
    """Raw color of object ``entry`` at pixel x, 0 when transparent or when
    the object does not cover the pixel (it covers X - 8 .. X - 1)."""
    ox, row, tile, attributes = entry
    dx = (x + 8 - ox) & 0xFF
    if dx >= 8:
        return 0
    tall = bool(lcdc & LCDC_OBJ_SIZE)
    if attributes & ATTR_Y_FLIP:
        row = (15 if tall else 7) - row
    if tall:
        tile = (tile & 0xFE) | (row >> 3)
    low = vram[tile * 16 + (row & 7) * 2]
    high = vram[tile * 16 + (row & 7) * 2 + 1]
    bit = dx if attributes & ATTR_X_FLIP else 7 - dx
    return ((high >> bit) & 1) << 1 | ((low >> bit) & 1)


def object_pixel(vram, lcdc, objects, x):
    """The candidate object at pixel x as (color, attributes, ties, beaten)
    or None: among the kept objects with a nonzero color at x the smallest
    X wins, the lowest OAM index on a tie. ``ties`` counts other candidates
    with the winner's X; ``beaten`` counts candidates with a lower index."""
    candidates = []
    for index, entry in enumerate(objects):
        color = object_color(vram, lcdc, entry, x)
        if color:
            candidates.append((entry[0], index, color, entry[3]))
    if not candidates:
        return None
    ox, index, color, attributes = min(candidates)
    ties = sum(1 for c in candidates if c[0] == ox) - 1
    beaten = sum(1 for c in candidates if c[1] < index)
    return color, attributes, ties, beaten


def render_frame(vram, oam, regs, stats=None):
    """The 144 rows of 160 shades the LCD shows for static registers: the
    background scrolled by SCY/SCX, the window from its first pixel on the
    lines that draw it, and the objects over them through OBP0/OBP1 unless
    the priority attribute and a nonzero tile color cover them. LCDC.0 clear
    makes both tile layers color 0 and white (shade 0) regardless of BGP;
    LCDC.1 clear hides every object. ``stats`` collects coverage counts."""
    assert len(vram) == 8192 and len(oam) == 160
    lcdc, scy, scx, bgp = regs[LCDC], regs[SCY], regs[SCX], regs[BGP]
    obp = (regs[OBP0], regs[OBP1])
    wy, wx = regs[WY], regs[WX]
    lines = window_lines(lcdc, wy, wx)
    first = window_first_pixel(wx)
    counts = {'window_pixels': 0, 'object_pixels': 0, 'priority_covered': 0, 'ties': 0,
              'lower_x_wins': 0, 'overflow_lines': 0, 'flip_x': 0, 'flip_y': 0, 'obp1': 0}
    frame = []
    for y in range(VISIBLE_LINES):
        objects, on_line = line_objects(oam, lcdc, y)
        counts['overflow_lines'] += int(on_line > OBJECTS_PER_LINE)
        row = []
        for x in range(VISIBLE_PIXELS):
            if y in lines and x >= first:
                color = window_color(vram, lcdc, x + 7 - wx, lines[y])
                counts['window_pixels'] += 1
            else:
                color = background_color(vram, lcdc, scx + x, scy + y)
            if not lcdc & LCDC_BG:
                color = 0
            shade = palette_shade(bgp, color) if lcdc & LCDC_BG else 0
            candidate = object_pixel(vram, lcdc, objects, x) if lcdc & LCDC_OBJ else None
            if candidate is not None:
                ocolor, attributes, ties, beaten = candidate
                if attributes & ATTR_PRIORITY and color:
                    counts['priority_covered'] += 1
                else:
                    shade = palette_shade(obp[int(bool(attributes & ATTR_PALETTE))], ocolor)
                    counts['object_pixels'] += 1
                    counts['ties'] += int(ties > 0)
                    counts['lower_x_wins'] += int(beaten > 0)
                    counts['flip_x'] += int(bool(attributes & ATTR_X_FLIP))
                    counts['flip_y'] += int(bool(attributes & ATTR_Y_FLIP))
                    counts['obp1'] += int(bool(attributes & ATTR_PALETTE))
            row.append(shade)
        frame.append(row)
    if stats is not None:
        stats.update(counts)
    return frame


def render_background(vram, lcdc, scy, scx, bgp):
    """The frame for static registers without window or objects (LCDC.5 and
    LCDC.1 ignored): the background-only view the system background test
    and the focused background target use."""
    regs = {LCDC: lcdc & ~(LCDC_WINDOW | LCDC_OBJ), SCY: scy, SCX: scx, BGP: bgp,
            OBP0: 0, OBP1: 0, WY: 0, WX: 0}
    return render_frame(vram, bytes(160), regs)


def write_png(path, frame):
    """Save a frame as a grayscale PNG (pure standard library); DMG shade 0 is white."""
    levels = (255, 170, 85, 0)
    raw = b''.join(b'\x00' + bytes(levels[shade] for shade in row) for row in frame)

    def chunk(kind, data):
        body = kind + data
        return struct.pack('>I', len(data)) + body + struct.pack('>I', zlib.crc32(body) & 0xFFFFFFFF)

    header = struct.pack('>IIBBBBB', len(frame[0]), len(frame), 8, 0, 0, 0, 0)
    png = b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', header) + chunk(b'IDAT', zlib.compress(raw, 9)) + chunk(b'IEND', b'')
    path.write_bytes(png)


def transfer_end(fine):
    """First HBlank dot of a visible line given its latched fine scroll."""
    return MODE2_DOTS + MODE3_BASE_DOTS + fine


def ly_read(ly, dot):
    """LY as the register reads it."""
    if dot >= LY_READ_DOT:
        return 0 if ly == LINES_PER_FRAME - 1 else ly + 1
    return ly


def mode_at(ly, dot, fine, lcd_on=True):
    """Mode as STAT reports it; the renderer and VRAM/OAM blocking share it."""
    if not lcd_on:
        return HBLANK
    if ly >= VISIBLE_LINES:
        return VBLANK
    if dot < MODE2_DOTS:
        return OAM_SCAN
    if dot < transfer_end(fine):
        return TRANSFER
    return HBLANK


class PpuTiming:
    """Dot-stepped raster model.

    ``dot`` and ``ly`` name the current dot; ``end_dot(scx)`` applies the
    events of the enable edge that ends it (fine-scroll latch at dot 80 of a
    visible line, pixel emission, VBlank entry) and advances to the next dot.
    The fine scroll is retained across lines until the next latch, like the
    hardware register it models.
    """

    def __init__(self):
        self.lcd_on = False
        self.ly = 0
        self.dot = 0
        self.fine = 0

    def enable(self):
        self.lcd_on = True
        self.ly = 0
        self.dot = 0

    def disable(self):
        self.lcd_on = False
        self.ly = 0
        self.dot = 0

    @property
    def mode(self):
        return mode_at(self.ly, self.dot, self.fine, self.lcd_on)

    @property
    def vram_blocked(self):
        return self.lcd_on and self.mode == TRANSFER

    @property
    def oam_blocked(self):
        return self.lcd_on and self.mode in (OAM_SCAN, TRANSFER)

    def end_dot(self, scx):
        """Events at the edge ending the current dot: (pixel or None, vblank_entry)."""
        assert self.lcd_on
        visible = self.ly < VISIBLE_LINES
        if visible and self.dot == MODE2_DOTS:
            self.fine = scx & 7
        pixel = None
        if visible and PIXEL_DOT + self.fine <= self.dot < PIXEL_DOT + self.fine + VISIBLE_PIXELS:
            pixel = (self.dot - PIXEL_DOT - self.fine, self.ly)
        vblank = self.ly == VISIBLE_LINES - 1 and self.dot == DOTS_PER_LINE - 1
        if self.dot == DOTS_PER_LINE - 1:
            self.dot = 0
            self.ly = 0 if self.ly == LINES_PER_FRAME - 1 else self.ly + 1
        else:
            self.dot += 1
        return pixel, vblank


def stat_line(ly, dot, fine, enables, lyc, lcd_on=True, vblank_entry=False):
    """Level of the STAT interrupt line for one dot (the mode-2 source also
    covers the VBlank entry cycle at line 144, dot 0)."""
    mode = mode_at(ly, dot, fine, lcd_on)
    sources = False
    if lcd_on:
        mode0 = mode == HBLANK and not (
            ly < VISIBLE_LINES and dot < transfer_end(fine) + MODE0_IRQ_DELAY)
        sources = ((enables & STAT_MODE0 and mode0)
                   or (enables & STAT_MODE1 and mode == VBLANK)
                   or (enables & STAT_MODE2 and (mode == OAM_SCAN or vblank_entry)))
    coincidence = (ly == lyc) if lcd_on else False
    return bool(sources or (enables & STAT_LYC and coincidence))


def stat_edges(enables, lyc, scx, start_line_low=True):
    """(ly, dot) positions where the STAT line rises during one enabled frame
    starting at line 0, dot 0 with the fine scroll of the register value."""
    fine = scx & 7
    edges = []
    previous = not start_line_low
    for ly in range(LINES_PER_FRAME):
        for dot in range(DOTS_PER_LINE):
            level = stat_line(ly, dot, fine, enables, lyc,
                              vblank_entry=(ly == VISIBLE_LINES and dot == 0))
            if level and not previous:
                edges.append((ly, dot))
            previous = level
    return edges
