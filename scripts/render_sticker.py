"""Render real pose sequences or controlled head motion on a shared canvas."""
import argparse
from bisect import bisect_right
import json
import math
from pathlib import Path
import sys

from PIL import Image, ImageDraw, ImageFont
from gif_io import DEFAULT_LIMIT, STICKER_SIZE, save_gif

MAC_CJK_FONTS = (
    Path('/System/Library/Fonts/Hiragino Sans GB.ttc'),
    Path('/System/Library/Fonts/PingFang.ttc'),
    Path('/System/Library/Fonts/PingFang SC.ttc'),
)


def typeface(size, user_font=None):
    path = Path(user_font) if user_font else next((f for f in MAC_CJK_FONTS if f.is_file()), None)
    if not path:
        raise ValueError('Supply --font with a CJK font file available on this host')
    return ImageFont.truetype(str(path), size)


def load_pose(path):
    with Image.open(path) as source:
        image = source.convert('RGBA')
    if image.size != STICKER_SIZE:
        raise ValueError(f'Place every pose on the same 240×240 canvas: {path}')
    minimum, maximum = image.getchannel('A').getextrema()
    if minimum != 0 or maximum == 0:
        raise ValueError(f'Expected a visible subject on a transparent canvas: {path}')
    return image


def ease(part):
    return part * part * (3 - 2 * part)


def head_transform(base, pivot, blend_top, lock_y, movement):
    shift_x, shift_y, degrees, pitch = movement
    if not any(movement):
        return base.copy()
    px, py = pivot
    angle = math.radians(degrees)
    cosine, sine = math.cos(angle), math.sin(angle)

    def source_point(x, y):
        weight = ease(max(0, min(1, (lock_y - y) / (lock_y - blend_top))))
        dx, dy = x-px, y-py
        return (
            x + weight * ((cosine-1)*dx + sine*dy - shift_x),
            y + weight * (-sine*dx + (cosine-1)*dy - shift_y + dy*pitch),
        )

    # Use one continuous inverse map from the single source. The grid ends at
    # the neckline so the chest, silhouette and toes below it remain exact.
    width, height = base.size
    xs = [*range(0, width, 8), width]
    ys = [*range(0, lock_y, 8), lock_y]
    mesh = []
    for y1, y2 in zip(ys, ys[1:]):
        for x1, x2 in zip(xs, xs[1:]):
            corners = (source_point(x1, y1), source_point(x1, y2),
                       source_point(x2, y2), source_point(x2, y1))
            mesh.append(((x1,y1,x2,y2), tuple(coordinate for pair in corners for coordinate in pair)))
    frame = base.transform(base.size, Image.Transform.MESH, mesh, Image.Resampling.BICUBIC)
    body = (0, lock_y, width, height)
    frame.paste(base.crop(body), body[:2])
    return frame


def head_frames(plan, directory):
    base = load_pose(directory / plan['base'])
    pivot = plan['pivot']
    top, lock_y = plan['blend_top'], plan['lock_y']
    step = plan.get('step_ms', 30)
    track = plan['track']
    if type(top) is not int or type(lock_y) is not int or not 0 <= top < lock_y < base.height:
        raise ValueError('Use 0 <= blend_top < lock_y < 240')
    if type(step) is not int or not 10 <= step <= 100 or step % 10:
        raise ValueError('Use a sampling step of 10 to 100 ms in 10 ms increments')
    moments = [key['at_ms'] for key in track]
    if len(moments) < 3 or moments[0] != 0 or moments[-1] > 30_000:
        raise ValueError('Provide start, motion and return keys in a loop of up to 30 seconds')
    if (any(type(moment) is not int or moment % 10 for moment in moments)
            or any(right <= left for left, right in zip(moments, moments[1:]))):
        raise ValueError('Key times must strictly increase in 10 ms increments')
    controls = ('x', 'y', 'roll_deg', 'pitch')
    shifts = [tuple(float(key.get(k,0)) for k in controls) for key in track]
    if not all(math.isfinite(value) for shift in shifts for value in shift):
        raise ValueError('Motion controls must be finite numbers')
    if shifts[0] != shifts[-1]:
        raise ValueError('Match the first and final head pose for a closed loop')
    frames, times = [], []
    for time in range(0, moments[-1], step):
        left = bisect_right(moments, time)-1
        right = left+1
        elapsed = (time-moments[left])/(moments[right]-moments[left])
        # Smoothly change direction at a key pose. Additional neutral holds
        # should appear only when their timing matters to the gesture.
        weight = ease(elapsed)
        pose = tuple(a+(b-a)*weight for a,b in zip(shifts[left], shifts[right]))
        frames.append(head_transform(base, pivot, top, lock_y, pose))
        times.append(min(step, moments[-1]-time))
    return frames, times, [(0,lock_y,*base.size)]


def render(plan_path, output, font_path, byte_limit):
    plan = json.loads(plan_path.read_text(encoding='utf-8'))
    directory = plan_path.parent
    if plan['mode'] == 'keyframes':
        frames = [load_pose(directory / frame['file']) for frame in plan['frames']]
        times = [frame['duration_ms'] for frame in plan['frames']]
        boxes = []
    elif plan['mode'] == 'anchored-head':
        frames, times, boxes = head_frames(plan, directory)
    else:
        raise ValueError('Use mode keyframes or anchored-head')
    boxes.extend(tuple(box) for box in plan.get('locked_boxes', ()))
    caption = plan.get('caption')
    if caption:
        size = plan.get('font_size', 36 if len(caption) <= 4 else 30)
        font = typeface(size, font_path)
        position = tuple(plan.get('caption_center', [120, 211]))
        for frame in frames:
            draw = ImageDraw.Draw(frame)
            text_box = draw.textbbox(position,caption,font=font,anchor='mm',stroke_width=2)
            if text_box[0] < 0 or text_box[1] < 0 or text_box[2] > 240 or text_box[3] > 240:
                raise ValueError('Caption overflows the frame. Use a smaller font size or reposition it.')
            draw.text(position,caption,font=font,anchor='mm',fill=plan.get('text_color','#543B31'),
                      stroke_width=2,stroke_fill='#FFF7E8')
    return save_gif(frames, times, output, byte_limit, boxes)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('plan', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--font', type=Path)
    parser.add_argument('--max-bytes', type=int, default=DEFAULT_LIMIT)
    args = parser.parse_args()
    try:
        stats = render(args.plan,args.output,args.font,args.max_bytes)
    except (KeyError, OSError, ValueError) as exc:
        print(str(exc),file=sys.stderr)
        return 1
    print(json.dumps(stats,ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
