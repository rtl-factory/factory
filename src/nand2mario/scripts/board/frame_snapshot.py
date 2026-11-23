#!/usr/bin/env python3
"""nand2mario frame snapshot: capture over the UART loader and offline decode.

    python3 src/nand2mario/scripts/board/frame_snapshot.py plan [--out PATH]
    python3 src/nand2mario/scripts/board/frame_snapshot.py capture --port DEVICE [--baud 2000000] [--out PATH] [--deadline S]
    python3 src/nand2mario/scripts/board/frame_snapshot.py decode DUMP [--manifest JSON] [--png PATH] [--vga-png PATH]
    python3 src/nand2mario/scripts/board/frame_snapshot.py gif DUMP... --durations-ms MS... --out PATH [--vga] [--manifests JSON...]

A snapshot record (wiki/src/nand2mario/scripts/board/spec.md#frame-snapshot) is the
displayed 160x144 frame read back from the frame store: an eight-byte header
(magic "NM", version 1, on-screen flag and bank, 32-bit frame ID) and four
two-bit DMG shades per byte. `capture` pins, reads and releases it through
cart_loader.Session and writes <out>.bin plus a <out>.json manifest; `plan`
shows the request frames and opens nothing. `decode` and `gif` never open a
port: they validate records (length, magic, version, bank, ID, and with a
manifest its hash and identity) and write labelled PNG/GIF images, raw or as
the display's 640x480 reconstruction (3x, window at (80, 24), black border).
They are frame-store readbacks, not electrical VGA captures. While pinned the
board's picture is frozen on the pinned frame, so a capture series is a
sampled, paused view. Every output path must lie below the repository's
workdir/. Contract: wiki/src/nand2mario/scripts/board/spec.md#frame-snapshot.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import struct
import sys
import time
import zlib

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.nand2mario.scripts.board import cart_loader  # noqa: E402

WIDTH, HEIGHT = 160, 144
PIXELS = WIDTH * HEIGHT
HEADER_BYTES = cart_loader.SNAPSHOT_HEADER_BYTES
RECORD_BYTES = cart_loader.SNAPSHOT_RECORD_BYTES
MAGIC, VERSION = b'NM', 1
SCHEMA = 'n2m-frame-snapshot/1'
KIND = 'raw 160x144 frame-store snapshot read over the nand2mario UART loader (not a VGA capture)'
# Ref: n2m_display_pkg shade_rgb: shade 0 (lightest) .. 3 as 4-bit grey F, A, 5, 0,
# here in 8 bits; the border of the 640x480 reconstruction is black.
GREY = (0xFF, 0xAA, 0x55, 0x00)
VGA_WIDTH, VGA_HEIGHT, SCALE, LEFT, TOP = 640, 480, 3, 80, 24
MAX_GIF_FRAMES = 64
DEFAULT_DEADLINE = 30.0


@dataclass(frozen=True)
class Snapshot:
    frame_id: int
    bank: int
    on_screen: bool
    shades: tuple
    sha256: str


def parse_record(record: bytes) -> Snapshot:
    """Validate one record and unpack its shades; ValueError names the defect."""
    if not isinstance(record, (bytes, bytearray)):
        raise ValueError('a record is bytes')
    record = bytes(record)
    if len(record) != RECORD_BYTES:
        raise ValueError(f'record has {len(record)} bytes, expected {RECORD_BYTES}')
    if record[:2] != MAGIC:
        raise ValueError(f'bad magic {record[:2].hex()}')
    if record[2] != VERSION:
        raise ValueError(f'unsupported format version {record[2]}')
    flags = record[3]
    if flags & 0x7C or flags & 3 > 2:
        raise ValueError(f'bad bank/flags byte {flags:#04x}')
    frame_id = int.from_bytes(record[4:8], 'little')
    if frame_id < 1:
        raise ValueError('frame ID 0 names no frame')
    shades = tuple((byte >> (2 * j)) & 3 for byte in record[HEADER_BYTES:] for j in range(4))
    return Snapshot(frame_id, flags & 3, bool(flags >> 7), shades, hashlib.sha256(record).hexdigest())


def check_manifest(snapshot: Snapshot, manifest: dict) -> None:
    expected = {'schema': SCHEMA, 'record_bytes': RECORD_BYTES, 'sha256': snapshot.sha256,
                'frame_id': snapshot.frame_id, 'bank': snapshot.bank, 'format_version': VERSION}
    for key, value in expected.items():
        if manifest.get(key) != value:
            raise ValueError(f'manifest {key} is {manifest.get(key)!r}, record says {value!r}')


def apply_manifest(snapshot: Snapshot, manifest: dict | None):
    """Check a capture manifest against its record and fold the closing
    header's on-screen status in: a frame that was not on screen at the end
    of the read is reported as off screen. Returns (snapshot, on_screen_at_end)."""
    if manifest is None:
        return snapshot, None
    check_manifest(snapshot, manifest)
    at_end = manifest.get('on_screen_at_end')
    if at_end not in (None, True, False):
        raise ValueError(f'manifest on_screen_at_end is {at_end!r}, not a boolean')
    if at_end is True and not snapshot.on_screen:
        # Why: only a swap revalidates the display, and none happens while pinned.
        raise ValueError('manifest says on screen at the end, but the record was off screen when pinned')
    if at_end is False:
        snapshot = Snapshot(snapshot.frame_id, snapshot.bank, False, snapshot.shades, snapshot.sha256)
    return snapshot, at_end


def screen_note(snapshot: Snapshot, on_screen_at_end=None) -> str:
    """Where the frame was during its read. The record's flag says whether it
    was on screen when pinned; only the capture manifest's closing-header
    status says whether it stayed there, so without it that is unknown."""
    if not snapshot.on_screen or on_screen_at_end is False:
        return f'frame {snapshot.frame_id}: the last frame displayed before an LCD-off blank, not on screen at readout'
    if on_screen_at_end is True:
        return f'frame {snapshot.frame_id}: on screen and frozen there while read'
    return (f'frame {snapshot.frame_id}: on screen when pinned; whether it stayed on screen for the read is unknown '
            '(no closing status: no manifest or no on_screen_at_end)')


def raw_indices(snapshot: Snapshot) -> list:
    """Rows of palette indices (the shades themselves) of the raw frame."""
    return [list(snapshot.shades[y * WIDTH:(y + 1) * WIDTH]) for y in range(HEIGHT)]


def vga_indices(snapshot: Snapshot) -> list:
    """The 640x480 reconstruction as palette indices: shade s at the 3x3
    block of source pixel (sx, sy) at (80 + 3 sx, 24 + 3 sy), black border
    (index 3, black) elsewhere."""
    rows = []
    for y in range(VGA_HEIGHT):
        sy = (y - TOP) // SCALE
        if not 0 <= y - TOP < HEIGHT * SCALE:
            rows.append([3] * VGA_WIDTH)
            continue
        source = snapshot.shades[sy * WIDTH:(sy + 1) * WIDTH]
        rows.append([3] * LEFT + [source[x // SCALE] for x in range(WIDTH * SCALE)] + [3] * (VGA_WIDTH - LEFT - WIDTH * SCALE))
    return rows


def labels(snapshot: Snapshot, vga: bool, on_screen_at_end=None) -> dict:
    what = ('640x480 reconstruction of the display (3x scale, window at (80, 24), black border) from a raw '
            'frame-store snapshot; not an electrical raster capture' if vga else
            'raw 160x144 nand2mario frame-store snapshot (DMG shades); not a VGA capture')
    shown = screen_note(snapshot, on_screen_at_end).split(': ', 1)[1]
    return {'Title': f'nand2mario frame {snapshot.frame_id} (bank {snapshot.bank})',
            'Description': f'{what}; {shown}; palette shade 0-3 = grey FF/AA/55/00',
            'Source': f'nand2mario UART loader SNAP_READ record sha256 {snapshot.sha256}'}


def png_bytes(rows: list, text: dict) -> bytes:
    """8-bit RGB PNG of palette-index rows with tEXt fields (standard library only)."""
    raw = b''.join(b'\x00' + bytes(GREY[i] for i in row for _ in range(3)) for row in rows)

    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data) & 0xFFFFFFFF)

    header = struct.pack('>IIBBBBB', len(rows[0]), len(rows), 8, 2, 0, 0, 0)
    texts = b''.join(chunk(b'tEXt', key.encode('latin-1') + b'\x00' + value.encode('latin-1')) for key, value in text.items())
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', header) + texts + chunk(b'IDAT', zlib.compress(raw, 9))
            + chunk(b'IEND', b''))


def lzw_encode(indices, min_code_size=2) -> bytes:
    """GIF LZW code stream (variable width, LSB first) for palette indices."""
    clear, end = 1 << min_code_size, (1 << min_code_size) + 1
    out, bits, nbits = bytearray(), 0, 0

    def emit(code, size):
        nonlocal bits, nbits
        bits |= code << nbits
        nbits += size
        while nbits >= 8:
            out.append(bits & 0xFF)
            bits >>= 8
            nbits -= 8

    def reset():
        return {bytes([i]): i for i in range(clear)}, end + 1, min_code_size + 1

    table, next_code, size = reset()
    emit(clear, size)
    prefix = b''
    for index in indices:
        candidate = prefix + bytes([index])
        if candidate in table:
            prefix = candidate
            continue
        emit(table[prefix], size)
        if next_code == 4096:
            emit(clear, size)
            table, next_code, size = reset()
        else:
            table[candidate] = next_code
            # Ref: GIF89a LZW: the decoder widens its codes once the next code no longer fits.
            if next_code == (1 << size) and size < 12:
                size += 1
            next_code += 1
        prefix = bytes([index])
    emit(table[prefix], size)
    emit(end, size)
    if nbits:
        out.append(bits & 0xFF)
    return bytes(out)


def gif_bytes(frames: list, durations_ms: list, comment: str) -> bytes:
    """Looping GIF89a of equally sized palette-index frames with a four-grey
    global palette and one explicit delay per frame."""
    width, height = len(frames[0][0]), len(frames[0])
    palette = b''.join(bytes([level] * 3) for level in GREY)
    data = bytearray(b'GIF89a' + struct.pack('<HHBBB', width, height, 0x80 | 0x01, 0, 0) + palette)
    data += b'\x21\xff\x0bNETSCAPE2.0\x03\x01\x00\x00\x00'          # Ref: NETSCAPE2.0 extension, loop count 0 loops forever
    note = comment.encode('ascii')
    data += b'\x21\xfe' + b''.join(bytes([len(note[i:i + 255])]) + note[i:i + 255] for i in range(0, len(note), 255)) + b'\x00'
    for rows, duration in zip(frames, durations_ms):
        data += b'\x21\xf9\x04\x04' + struct.pack('<H', duration // 10) + b'\x00\x00'
        data += b'\x2c' + struct.pack('<HHHHB', 0, 0, width, height, 0)
        stream = lzw_encode([i for row in rows for i in row])
        data += b'\x02' + b''.join(bytes([len(stream[i:i + 255])]) + stream[i:i + 255]
                                   for i in range(0, len(stream), 255)) + b'\x00'
    return bytes(data + b'\x3b')


def output_path(name, root: Path) -> Path:
    path = Path(name)
    path = (path if path.is_absolute() else root / path).resolve()
    if not path.is_relative_to((root / 'workdir').resolve()):
        raise PermissionError(f'output {path} must lie below {root / "workdir"}')
    return path


def plan_frames() -> list:
    """Request frames of the capture sequence, in order."""
    frames = [cart_loader.encode_request(cart_loader.CMD_SNAP_RELEASE), cart_loader.encode_request(cart_loader.CMD_SNAP_PIN)]
    for offset in range(0, RECORD_BYTES, cart_loader.MAX_PAYLOAD):
        frames.append(cart_loader.encode_request(cart_loader.CMD_SNAP_READ, offset,
                                                 length=min(cart_loader.MAX_PAYLOAD, RECORD_BYTES - offset)))
    frames.append(cart_loader.encode_request(cart_loader.CMD_SNAP_READ, 0, length=HEADER_BYTES))
    frames.append(cart_loader.encode_request(cart_loader.CMD_SNAP_RELEASE))
    return frames


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')


def capture(transport, port: str, baud: int, deadline_s: float, *, clock=time.monotonic, log=None):
    """Run the snapshot sequence on an open transport; returns (record, manifest)."""
    session = cart_loader.Session(transport, log=log)
    utc_start, started = utc_now(), clock()
    record, closing = session.snapshot(deadline=started + deadline_s, clock=clock)
    snapshot = parse_record(record)
    manifest = {'schema': SCHEMA, 'kind': KIND, 'record_bytes': len(record), 'sha256': snapshot.sha256,
                'frame_id': snapshot.frame_id, 'bank': snapshot.bank, 'format_version': VERSION,
                'on_screen_at_pin': snapshot.on_screen, 'on_screen_at_end': bool(closing[3] >> 7),
                'port': port, 'baud': baud, 'utc_start': utc_start, 'utc_end': utc_now(),
                'elapsed_s': round(clock() - started, 3)}
    return record, manifest


def describe(snapshot: Snapshot, on_screen_at_end=None) -> dict:
    histogram = [snapshot.shades.count(shade) for shade in range(4)]
    result = {'frame_id': snapshot.frame_id, 'bank': snapshot.bank, 'on_screen': snapshot.on_screen,
              'sha256': snapshot.sha256, 'shade_histogram': histogram}
    if on_screen_at_end is not None:
        result['on_screen_at_end'] = on_screen_at_end
    return result


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog='frame_snapshot.py', description=__doc__.split('\n\n', 1)[0],
                                formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__.split('\n\n', 2)[2])
    sub = p.add_subparsers(dest='action', required=True)
    pl = sub.add_parser('plan', help='print the capture request sequence; opens nothing')
    pl.add_argument('--out', help='also write the request frames to this file below workdir/')
    c = sub.add_parser('capture', help='pin, read and release the displayed frame over --port')
    c.add_argument('--port', required=True, help='serial device of the 3.3 V adapter on the loader UART')
    c.add_argument('--baud', type=int, default=cart_loader.DEFAULT_BAUD)
    c.add_argument('--out', default='workdir/n2m_snapshot/snapshot', help='output stem below workdir/ (.bin and .json)')
    c.add_argument('--deadline', type=float, default=DEFAULT_DEADLINE,
                   help=f'whole-capture bound in seconds, checked between frames (default {DEFAULT_DEADLINE:g})')
    d = sub.add_parser('decode', help='validate a record and write labelled PNGs')
    d.add_argument('dump')
    d.add_argument('--manifest', help='capture manifest to check the record against')
    d.add_argument('--png', help='raw 160x144 PNG below workdir/')
    d.add_argument('--vga-png', help='640x480 reconstruction PNG below workdir/')
    g = sub.add_parser('gif', help='compose records in increasing frame-ID order into a looping GIF')
    g.add_argument('dumps', nargs='+')
    g.add_argument('--durations-ms', type=int, nargs='+', required=True, help='one sampled duration per record')
    g.add_argument('--out', required=True, help='GIF below workdir/')
    g.add_argument('--vga', action='store_true', help='640x480 reconstruction frames instead of raw 160x144')
    g.add_argument('--manifests', nargs='+', help='one capture manifest per record, checked and folded into the labels')
    return p


def main(argv=None, *, root: Path = ROOT, open_serial=None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.action == 'plan':
            frames = plan_frames()
            result = {'frames': len(frames), 'bytes': sum(len(f) for f in frames),
                      'sequence': ['SNAP_RELEASE', 'SNAP_PIN', f'SNAP_READ x{len(frames) - 4} (record)',
                                   'SNAP_READ (header again)', 'SNAP_RELEASE']}
            if args.out:
                path = output_path(args.out, root)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b''.join(frames))
                result['written'] = str(path)
            print(json.dumps(result, indent=2))
            return 0
        if args.action == 'capture':
            stem = output_path(args.out, root)
            if args.deadline <= 0:
                raise ValueError('--deadline must be positive')
            opener = open_serial or cart_loader.open_serial
            try:
                transport = opener(args.port, args.baud)
            except cart_loader.LoaderError as error:
                print(f'frame_snapshot: {error}', file=sys.stderr)
                return 2
            try:
                record, manifest = capture(transport, args.port, args.baud, args.deadline,
                                           log=lambda message: print(message, file=sys.stderr))
            except (cart_loader.LoaderError, ValueError, OSError) as error:
                print(f'frame_snapshot: capture failed: {error}', file=sys.stderr)
                return 1
            finally:
                if hasattr(transport, 'close'):
                    transport.close()
            stem.parent.mkdir(parents=True, exist_ok=True)
            stem.with_suffix('.bin').write_bytes(record)
            stem.with_suffix('.json').write_text(json.dumps(manifest, indent=2) + '\n')
            print(json.dumps(dict(manifest, record=str(stem.with_suffix('.bin'))), indent=2))
            return 0
        if args.action == 'decode':
            targets = [(output_path(name, root), rows, vga)
                       for name, rows, vga in ((args.png, raw_indices, False), (args.vga_png, vga_indices, True)) if name]
            try:
                manifest = json.loads(Path(args.manifest).read_text()) if args.manifest else None
                snapshot, on_screen_at_end = apply_manifest(parse_record(Path(args.dump).read_bytes()), manifest)
            except ValueError as error:
                print(f'frame_snapshot: invalid record: {error}', file=sys.stderr)
                return 1
            result = describe(snapshot, on_screen_at_end)
            for path, rows, vga in targets:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(png_bytes(rows(snapshot), labels(snapshot, vga, on_screen_at_end)))
                result['vga_png' if vga else 'png'] = str(path)
            print(json.dumps(result, indent=2))
            return 0
        path = output_path(args.out, root)
        if not 1 <= len(args.dumps) <= MAX_GIF_FRAMES:
            raise ValueError(f'a GIF takes 1 to {MAX_GIF_FRAMES} records')
        if len(args.durations_ms) != len(args.dumps):
            raise ValueError('give exactly one --durations-ms value per record')
        for duration in args.durations_ms:
            if not 10 <= duration <= 60000 or duration % 10:
                raise ValueError('durations are multiples of 10 ms in 10..60000')
        if args.manifests is not None and len(args.manifests) != len(args.dumps):
            raise ValueError('give exactly one --manifests file per record')
        try:
            manifests = ([json.loads(Path(name).read_text()) for name in args.manifests] if args.manifests
                         else [None] * len(args.dumps))
            checked = [apply_manifest(parse_record(Path(name).read_bytes()), manifest)
                       for name, manifest in zip(args.dumps, manifests)]
            snapshots = [snapshot for snapshot, _ in checked]
            closing = [at_end for _, at_end in checked]
        except ValueError as error:
            print(f'frame_snapshot: invalid record: {error}', file=sys.stderr)
            return 1
        ids = [s.frame_id for s in snapshots]
        if any(later <= earlier for earlier, later in zip(ids, ids[1:])):
            print(f'frame_snapshot: frame IDs must strictly increase, got {ids}', file=sys.stderr)
            return 1
        frames = [(vga_indices if args.vga else raw_indices)(s) for s in snapshots]
        comment = (f'nand2mario sampled frame-store snapshots, frame IDs {ids}; durations {args.durations_ms} ms are '
                   'editorial, not a real-time rate; ' + '; '.join(screen_note(s, at_end) for s, at_end in zip(snapshots, closing))
                   + ('; 640x480 reconstruction, not an electrical raster capture' if args.vga else ''))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(gif_bytes(frames, args.durations_ms, comment))
        print(json.dumps({'gif': str(path), 'frames': len(frames), 'frame_ids': ids,
                          'on_screen': [s.on_screen for s in snapshots], 'on_screen_at_end': closing,
                          'durations_ms': args.durations_ms, 'bytes': path.stat().st_size}, indent=2))
        return 0
    except (PermissionError, ValueError, OSError) as error:
        print(f'frame_snapshot: {error}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
