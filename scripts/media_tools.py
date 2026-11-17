"""Prepare pet references, cut regular pose sheets, make artwork and preview a pack."""
import argparse
from bisect import bisect_right
from itertools import accumulate
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory

from PIL import Image, ImageDraw, ImageOps
from gif_io import RESAMPLING, read_gif, save_gif


def normalize(output, sources):
    """Use EXIF orientation and retain the original files."""
    output.mkdir(parents=True, exist_ok=True)
    for index, path in enumerate(sources, 1):
        name = output / f'{index:02d}-{path.stem}.png'
        if path.suffix.lower() in {'.heic', '.heif'}:
            sips = shutil.which('sips')
            if not sips:
                raise ValueError('Convert HEIC/HEIF to PNG with a HEIF decoder on this host')
            with TemporaryDirectory() as temp:
                decoded = Path(temp) / 'source.png'
                subprocess.run([sips,'-s','format','png',str(path.resolve()),'--out',str(decoded)],
                               check=True, capture_output=True)
                copy_reference(decoded, name)
        else:
            copy_reference(path, name)
        print(name)


def copy_reference(source, output):
    with Image.open(source) as image:
        oriented = ImageOps.exif_transpose(image).convert('RGBA')
        oriented.thumbnail((2000,2000),RESAMPLING)
        with output.open('xb') as file:
            oriented.save(file,format='PNG')


def cut_sheet(sheet, output, cols, rows, box):
    with Image.open(sheet) as image:
        region = image.convert('RGBA')
    if box:
        if not 0 <= box[0] < box[2] <= region.width or not 0 <= box[1] < box[3] <= region.height:
            raise ValueError('Sheet rectangle lies outside the source')
        region = region.crop(box)
    if cols <= 0 or rows <= 0 or region.width < cols or region.height < rows:
        raise ValueError('Grid dimensions exceed the sheet')
    output.mkdir(parents=True, exist_ok=False)
    width, height = region.width // cols, region.height // rows
    for row in range(rows):
        for col in range(cols):
            x, y = round(col * region.width / cols), round(row * region.height / rows)
            tile = region.crop((x, y, x+width, y+height))
            # Apply the same grid coordinate system to all cells. Do not
            # crop each dog to a bounding box or normalize each pose by height.
            result = ImageOps.pad(tile, (240,240), method=RESAMPLING, color=(0,0,0,0))
            result.save(output / f'{row*cols+col+1:03d}.png')


def artwork(portrait, face, output):
    with Image.open(portrait) as source:
        dog = cropped_subject(source)
    with Image.open(face) as source:
        head = cropped_subject(source)
    output.mkdir(parents=True, exist_ok=False)
    banner = Image.new('RGBA', (750,400), '#F1E5D6')
    draw = ImageDraw.Draw(banner)
    draw.ellipse((135,-58,635,450), fill='#F9F2E7')
    place(banner,dog,(390,334),(375,196))
    banner.convert('RGB').save(output/'横幅.jpg',quality=91,optimize=True)
    cover = Image.new('RGBA',(240,240))
    place(cover,dog,(214,217),(120,120))
    cover.save(output/'封面.png',optimize=True)
    icon = Image.new('RGBA',(50,50))
    place(icon,head,(48,48),(25,25))
    icon.save(output/'图标.png',optimize=True)


def cropped_subject(image):
    rgba = image.convert('RGBA')
    alpha = rgba.getchannel('A')
    if alpha.getextrema()[0] != 0 or not alpha.getbbox():
        raise ValueError('Artwork source must be a visible dog on transparent background')
    return rgba.crop(alpha.getbbox())


def place(canvas, subject, available, center):
    tile = ImageOps.contain(subject,available,method=RESAMPLING)
    canvas.alpha_composite(tile,(center[0]-tile.width//2,center[1]-tile.height//2))


def preview(stickers, output):
    paths = sorted(stickers.glob('*.gif'))
    if not paths:
        raise ValueError('No GIF stickers found')
    columns = min(5,len(paths))
    rows = (len(paths)+columns-1)//columns
    tile, pitch, margin = 144, 156, 10
    palette = Image.new('RGBA',(pitch*columns+2*margin,pitch*rows+2*margin),'#F2E9DB')
    draw = ImageDraw.Draw(palette)
    animations = []
    for index,path in enumerate(paths):
        frames, times, loop = read_gif(path)
        if loop != 0 or len(frames)<2 or min(times)<=0:
            raise ValueError(f'Invalid source animation: {path}')
        x = margin + index % columns * pitch
        y = margin + index // columns * pitch
        draw.rounded_rectangle((x,y,x+tile,y+tile),radius=14,fill='#FFF9EF')
        scaled = [ImageOps.contain(f,(tile-6,tile-6),method=RESAMPLING) for f in frames]
        animations.append((x,y,scaled,[0,*accumulate(times)]))
    longest = max(times[-1] for _,_,_,times in animations)
    step = max(30, ((longest+899)//900)*10)
    frames, timings = [], []
    for ms in range(0,longest,step):
        canvas = palette.copy()
        for x,y,tiles,times in animations:
            index = bisect_right(times,ms)-1 if ms < times[-1] else 0
            canvas.alpha_composite(tiles[index],(x+3,y+3))
        frames.append(canvas)
        timings.append(min(step,longest-ms))
    print(save_gif(frames,timings,output))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    intake = sub.add_parser('normalize')
    intake.add_argument('output', type=Path)
    intake.add_argument('sources', type=Path, nargs='+')
    grid = sub.add_parser('cut-sheet')
    grid.add_argument('sheet', type=Path)
    grid.add_argument('output', type=Path)
    grid.add_argument('--cols', type=int, required=True)
    grid.add_argument('--rows', type=int, required=True)
    grid.add_argument('--box', type=int, nargs=4)
    art = sub.add_parser('artwork')
    art.add_argument('portrait', type=Path)
    art.add_argument('face', type=Path)
    art.add_argument('output', type=Path)
    grid_preview = sub.add_parser('preview')
    grid_preview.add_argument('stickers', type=Path)
    grid_preview.add_argument('output', type=Path)
    args = parser.parse_args()
    try:
        if args.command == 'normalize':
            normalize(args.output,args.sources)
        elif args.command == 'cut-sheet':
            cut_sheet(args.sheet,args.output,args.cols,args.rows,args.box)
        elif args.command == 'artwork':
            artwork(args.portrait,args.face,args.output)
        else:
            preview(args.stickers,args.output)
    except (ValueError,OSError,subprocess.CalledProcessError) as exc:
        print(str(exc),file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
