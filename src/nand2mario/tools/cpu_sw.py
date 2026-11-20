#!/usr/bin/env python3
"""Build original LR35902 direct-entry programs using only local Python tools."""
import argparse
import json
from pathlib import Path
import sys

if __package__:
    from .sw.assembler import assemble
    from .sw.linker import link
    from .sw.package import package
    from .sw.expressions import AssemblyError
    from .sw.assets import load_shades, encode_shades
else:
    from sw.assembler import assemble
    from sw.linker import link
    from sw.package import package
    from sw.expressions import AssemblyError
    from sw.assets import load_shades, encode_shades

ROOT = Path(__file__).resolve().parents[3]


def output_path(path, directory=False):
    """Reject existing links/special files before creating any output artifact."""
    path = Path(path).absolute()
    for ancestor in reversed(path.parents):
        if ancestor.is_symlink() or (ancestor.exists() and not ancestor.is_dir()):
            raise ValueError('output ancestors must be directories, not symlinks')
    if path.is_symlink():
        raise ValueError('output must not be a symlink')
    if path.exists() and not (path.is_dir() if directory else path.is_file()):
        raise ValueError('output has an incompatible file type')
    resolved = path.resolve()
    if not resolved.is_relative_to(ROOT / 'workdir') or resolved == ROOT / 'workdir':
        raise ValueError('output must be below repository workdir')
    return resolved


def parse_assets(declarations):
    """NAME=PATH shade-JSON declarations to encoded 2bpp tile bytes."""
    assets = {}
    for declaration in declarations or ():
        name, separator, path = declaration.partition('=')
        if not separator or not name.isidentifier() or not path:
            raise ValueError('asset must be NAME=PATH with an identifier name')
        if name in assets:
            raise ValueError(f'duplicate asset {name}')
        source = Path(path).resolve()
        if not source.is_file() or not source.is_relative_to(ROOT):
            raise ValueError('asset must be a local repository file')
        relative = str(source.relative_to(ROOT))
        assets[name] = encode_shades(load_shades(source, relative), relative)
    return assets


def build(sources, layout, entry, output, dry_run=False, assets=None, title='CPU SMOKE', profile='dmg-direct-v1'):
    sources = [Path(s).resolve() for s in sources]
    output = output_path(output, directory=True)
    if not sources or len({p.stem for p in sources}) != len(sources):
        raise ValueError('sources require unique unit names (filename stems)')
    for source in sources:
        if not source.is_file() or not source.is_relative_to(ROOT):
            raise ValueError('source must be a local repository file')
    # Validate every destination before any mkdir/write, including dry-run.
    names = ['program.gb', 'map.json', 'symbols.json', 'listing.json']
    names.extend(source.stem + '.object.json' for source in sources)
    for name in names:
        output_path(output / name)
    layout_data = json.loads(Path(layout).read_text())
    unit, separator, symbol = entry.partition('::')
    if not separator or not unit or not symbol:
        raise ValueError('entry must be unit::symbol')
    # Dry-run performs assembly/link validation too. The prelude is the
    # generated DMG interface constants (src/nand2mario/cfg/sw-interfaces.json).
    prelude = ROOT / 'src/nand2mario/sw/cpu/interfaces.inc'
    encoded = parse_assets(assets)
    objects = [(source.stem, assemble(source, ROOT, prelude, encoded)) for source in sources]
    linked = link(objects, layout_data, {'unit': unit, 'symbol': symbol}, profile)
    image = package(linked, title, 1, profile)
    plan = {'sources': [str(p.relative_to(ROOT)) for p in sources],
            'entry': entry, 'profile': profile, 'bytes': len(image), 'output': str(output),
            'assets': {name: len(data) for name, data in sorted(encoded.items())},
            'dry_run': dry_run}
    if not dry_run:
        output.mkdir(parents=True, exist_ok=True)
        (output / 'program.gb').write_bytes(image)
        for name, obj in objects:
            (output / (name + '.object.json')).write_text(json.dumps(obj, indent=2) + '\n')
        for name in ('map', 'symbols', 'listing'):
            (output / (name + '.json')).write_text(json.dumps(linked[name], indent=2) + '\n')
    return plan


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', action='append', required=True,
                        help='local assembly file; repeat for multiple units')
    parser.add_argument('--layout', required=True, help='version-one section layout JSON')
    parser.add_argument('--entry', required=True, help='entry as unit::symbol')
    parser.add_argument('--output', required=True, help='directory below repository workdir')
    parser.add_argument('--asset', action='append', metavar='NAME=PATH',
                        help='shade JSON encoded as 2bpp tiles for ASSET "NAME"; repeatable')
    parser.add_argument('--title', default='CPU SMOKE',
                        help='cartridge header title (ASCII, at most 16 bytes); default CPU SMOKE')
    parser.add_argument('--profile', choices=('dmg-direct-v1', 'dmg-loader-v1'), default='dmg-direct-v1')
    parser.add_argument('--dry-run', action='store_true', help='validate and print plan without writing')
    args = parser.parse_args(argv)
    try:
        print(json.dumps(build(args.source, args.layout, args.entry, args.output, args.dry_run, args.asset, args.title, args.profile), indent=2))
        return 0
    except AssemblyError as error:
        print(json.dumps(error.diagnostic), file=sys.stderr)
    except (OSError, ValueError) as error:
        print(f'error: {error}', file=sys.stderr)
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
