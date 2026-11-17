"""Prepare visual evidence, block unreviewed assets, and package reviewed delivery files.

This does not detect anatomy automatically. A vision-capable agent must inspect
all evidence and record observations; hashes bind those decisions to exact files.
"""
import argparse
import hashlib
import json
import sys
import zipfile
from pathlib import Path
from PIL import Image, ImageOps, ImageDraw, ImageSequence
from gif_io import DEFAULT_LIMIT, STICKER_SIZE, fingerprint

CRITERIA = ('anatomy', 'identity', 'text', 'motion', 'framing')
FOLDERS = {'配图', '动图', '文案', '预览图'}
VISUAL = {'.png', '.jpg', '.jpeg', '.gif', '.webp'}
ART = {
    '横幅.jpg': ('JPEG', (750, 400), False),
    '封面.png': ('PNG', (240, 240), True),
    '图标.png': ('PNG', (50, 50), True),
}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inventory(root):
    return {p.relative_to(root).as_posix(): p for p in sorted(root.rglob('*')) if p.is_file()}


def inspect(path, sticker_limit=DEFAULT_LIMIT):
    errors = []
    frames = []
    with Image.open(path) as im:
        fmt, size, loop = im.format, im.size, im.info.get('loop')
        durations = []
        for f in ImageSequence.Iterator(im):
            frames.append(f.convert('RGBA').copy())
            durations.append(f.info.get('duration', 0))
    if path.suffix.lower() == '.gif':
        if fmt != 'GIF' or len(frames) < 2:
            errors.append('not_animated_gif')
        if len({fingerprint(frame) for frame in frames}) < 2:
            errors.append('identical_frames')
        if loop != 0:
            errors.append('not_looping')
        if any(d <= 0 for d in durations):
            errors.append('zero_frame_duration')
    if path.parent.name == '动图':
        if fmt != 'GIF':
            errors.append('sticker_not_gif')
        if size != STICKER_SIZE:
            errors.append('sticker_dimensions')
        if path.stat().st_size > sticker_limit:
            errors.append('sticker_file_size')
        if any(frame.getchannel('A').getextrema() != (0, 255) for frame in frames):
            errors.append('sticker_requires_transparent_margin_and_visible_subject')
    if path.parent.name == '配图' and path.name in ART:
        expected_format, expected_size, transparent = ART[path.name]
        if (fmt, size) != (expected_format, expected_size):
            errors.append('artwork_format_or_dimensions')
        alpha = frames[0].getchannel('A')
        if (transparent and (alpha.getextrema()[0] != 0 or not alpha.getbbox())
                or not transparent and alpha.getextrema() != (255, 255)):
            errors.append('artwork_transparency')
        if path.stat().st_size > (100_000 if path.name == '图标.png' else 500_000):
            errors.append('artwork_file_size')
    return frames, {'format': fmt, 'size': list(size), 'frames': len(frames),
                    'duration_ms': sum(durations), 'errors': errors}


def approved(row, criteria):
    checks = row.get('checks', {})
    return all(checks.get(key, {}).get('status') == 'pass'
               and checks[key].get('note', '').strip() for key in criteria)


def prepare(root, report, sticker_limit=DEFAULT_LIMIT, reuse=None):
    if report.exists():
        raise ValueError('Report already exists; use a new review directory/version')
    if root == report.parent or root in report.parents:
        raise ValueError('Keep review evidence outside delivery folder')
    report.parent.mkdir(parents=True, exist_ok=True)
    old = {}
    if reuse:
        snapshot = json.loads(reuse.read_text(encoding='utf-8'))
        old = {row['file']: row for row in snapshot['assets']}
    records = []
    for index, (name, path) in enumerate(inventory(root).items(), 1):
        row = {'file': name, 'sha256': digest(path)}
        if path.suffix.lower() in VISUAL:
            frames, technical = inspect(path, sticker_limit)
            row['technical'] = technical
            evidence = []
            # Every decoded frame, in numbered pages. Do not sample away defects.
            for start in range(0, len(frames), 16):
                batch = frames[start:start + 16]
                page = Image.new('RGB', (1024, ((len(batch)+3)//4)*280), '#dce1e5')
                draw = ImageDraw.Draw(page)
                for k, frame in enumerate(batch):
                    tile = ImageOps.contain(frame, (248, 248))
                    x, y = (k % 4)*256, (k // 4)*280
                    page.paste(tile, (x+(256-tile.width)//2, y+24), tile)
                    draw.text((x+8, y+5), f'Frame {start+k+1}', fill='black')
                out = report.parent / f'{index:03d}-frames-{start+1:04d}.jpg'
                page.save(out, quality=95)
                evidence.append(str(out.resolve()))
            row.update(evidence=evidence, reviewed_frames=[], checks={c: {'status': 'pending', 'note': ''} for c in CRITERIA})
        else:
            row['checks'] = {'content': {'status': 'pending', 'note': ''}}
        prior = old.get(name)
        if prior and prior['sha256'] == row['sha256'] and approved(prior, row['checks']):
            technical = row.get('technical')
            if not technical:
                row['checks'] = prior['checks']
            elif not technical['errors'] and sorted(prior.get('reviewed_frames', [])) == list(range(1,technical['frames']+1)):
                row.update(checks=prior['checks'], reviewed_frames=prior['reviewed_frames'])
        records.append(row)
    report.write_text(json.dumps({'root': str(root), 'sticker_max_bytes': sticker_limit, 'assets': records},
                                 ensure_ascii=False, indent=2),encoding='utf-8')


def gate(root, report):
    data = json.loads(report.read_text(encoding='utf-8'))
    errors = []
    files = inventory(root)
    rows = data['assets']
    if not rows or len({r['file'] for r in rows}) != len(rows):
        errors.append('empty_or_duplicate_inventory')
    if set(files) != {r['file'] for r in rows}:
        errors.append('inventory_changed')
    for row in rows:
        name = row['file']
        path = files.get(name)
        if path is None:
            errors.append(f'{name}: missing'); continue
        if digest(path) != row['sha256']:
            errors.append(f'{name}: changed_since_review')
        visual = path.suffix.lower() in VISUAL
        required = CRITERIA if visual else ('content',)
        checks = row.get('checks', {})
        for key in required:
            check = checks.get(key, {})
            if check.get('status') != 'pass' or not check.get('note', '').strip():
                errors.append(f'{name}: {key}_not_passed_with_evidence')
        if visual:
            frames, tech = inspect(path,data['sticker_max_bytes'])
            errors.extend(f'{name}: {e}' for e in tech['errors'])
            if sorted(row.get('reviewed_frames', [])) != list(range(1, len(frames)+1)):
                errors.append(f'{name}: not_all_frames_reviewed')
    if errors:
        raise ValueError('\n'.join(errors))
    return files


def pack(root, report, destination):
    if destination.exists() or root in destination.parents:
        raise ValueError('Choose a new ZIP path outside delivery directory')
    files = gate(root, report)
    if {p.name for p in root.iterdir()} != FOLDERS:
        raise ValueError('Delivery must contain only 配图、动图、文案、预览图')
    if {p.name for p in (root/'配图').iterdir()} != set(ART):
        raise ValueError('配图 must contain exactly 横幅.jpg、封面.png、图标.png')
    if any(not list((root/f).iterdir()) for f in FOLDERS):
        raise ValueError('Delivery folders must not be empty')
    if any(path.parent not in {root/f for f in FOLDERS} for path in files.values()):
        raise ValueError('Delivery files must be direct children of the four folders')
    if any(path.suffix.lower() != '.gif' for path in (root/'动图').iterdir()):
        raise ValueError('动图 must contain only GIF stickers')
    if not any((root/'预览图').glob('*.gif')):
        raise ValueError('预览图 must include an animated GIF preview')
    # Exclusive creation also protects the previously reviewed package if
    # another process tries to write the same destination.
    output = destination.open('xb')
    try:
        with output:
            with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as z:
                for name, path in files.items():
                    z.write(path, name)
        expected = {row['file']: row['sha256']
                    for row in json.loads(report.read_text(encoding='utf-8'))['assets']}
        with zipfile.ZipFile(destination) as z:
            if z.testzip() or any(hashlib.sha256(z.read(n)).hexdigest() != expected[n] for n in files):
                raise ValueError('ZIP verification failed')
    except Exception:
        destination.unlink(missing_ok=True)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['prepare', 'gate', 'pack'])
    parser.add_argument('root', type=Path)
    parser.add_argument('report', type=Path)
    parser.add_argument('--zip', type=Path)
    parser.add_argument('--max-sticker-bytes', type=int, default=DEFAULT_LIMIT)
    parser.add_argument('--reuse', type=Path, help='Reuse passed checks only for byte-identical files of the same pack')
    args = parser.parse_args()
    root, report = args.root.resolve(), args.report.resolve()
    try:
        if args.command == 'prepare':
            if args.max_sticker_bytes <= 0:
                raise ValueError('--max-sticker-bytes must be positive')
            prepare(root, report,args.max_sticker_bytes,args.reuse)
        elif args.command == 'gate': gate(root, report)
        elif args.zip: pack(root, report, args.zip.resolve())
        else: raise ValueError('pack requires --zip')
    except (OSError, ValueError, KeyError) as exc:
        print(str(exc), file=sys.stderr); return 1
    print(args.command + ': OK'); return 0

if __name__ == '__main__':
    sys.exit(main())
