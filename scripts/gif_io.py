"""Shared-palette GIF encoding and decoded measurements."""
from hashlib import sha256
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageSequence

RESAMPLING = Image.Resampling.LANCZOS
STICKER_SIZE = (240, 240)
DEFAULT_LIMIT = 500_000


def read_gif(source):
    with Image.open(source) as gif:
        if gif.format != 'GIF':
            raise ValueError('Expected GIF data')
        loop = gif.info.get('loop')
        frames, timings = [], []
        for frame in ImageSequence.Iterator(gif):
            frames.append(frame.convert('RGBA'))
            timings.append(frame.info.get('duration', 0))
    return frames, timings, loop


def visible(image):
    """Canonicalize fully transparent RGB without affecting visible pixels."""
    clean = Image.new('RGBA', image.size)
    clean.alpha_composite(image)
    return clean


def fingerprint(image):
    return sha256(visible(image).tobytes()).digest()


def encode_gif(frames, timings, colors):
    if not frames or len(frames) != len(timings):
        raise ValueError('frames and durations must have the same nonzero length')
    if any(type(ms) is not int or ms < 10 or ms % 10 or ms > 655_350 for ms in timings):
        raise ValueError('GIF durations must be positive multiples of 10 ms (at most 655350 ms)')
    if any(frame.mode != 'RGBA' or frame.size != frames[0].size for frame in frames):
        raise ValueError('Frames must share the same RGBA canvas size')

    # Give the encoder one palette for all frames. Palette changes can make a
    # stationary white dog flicker between hues.
    matte = (255, 247, 232)
    chip_size = (160, 160)
    samples = min(36, len(frames))
    atlas = Image.new('RGB', (6 * chip_size[0], ((samples+5)//6) * chip_size[1]), matte)
    for index in range(samples):
        frame = frames[index * len(frames) // samples].copy()
        frame.thumbnail(chip_size, RESAMPLING)
        atlas.paste(frame, (index % 6 * chip_size[0], index // 6 * chip_size[1]), frame.getchannel('A'))

    palette = atlas.quantize(colors=colors).getpalette()[:3 * colors]
    palette.extend(palette[:3] * (256-colors))
    reference = Image.new('P', (1, 1))
    reference.putpalette(palette)
    transparent = [255 if value < 112 else 0 for value in range(256)]
    encoded = []
    for frame in frames:
        flat = Image.new('RGB', frame.size, matte)
        alpha = frame.getchannel('A')
        flat.paste(frame, (0, 0), alpha)
        mapped = flat.quantize(palette=reference, dither=Image.Dither.NONE)
        mapped = mapped.point(list(range(255)) + [0])
        mapped.putpalette(palette)
        mapped.paste(255, mask=alpha.point(transparent))
        encoded.append(mapped)
    output = BytesIO()
    encoded[0].save(output, format='GIF', save_all=True, append_images=encoded[1:],
                    duration=timings, loop=0, disposal=2, transparency=255, optimize=False)
    return output.getvalue()


def save_gif(frames, timings, output, byte_limit=None, locked_boxes=()):
    output = Path(output)
    if output.exists():
        raise FileExistsError(f'Choose a new output path: {output}')
    for colors in (160, 128, 104, 80, 64):
        data = encode_gif(frames, timings, colors)
        if byte_limit is not None and len(data) > byte_limit:
            continue
        actual, durations, loop = read_gif(BytesIO(data))
        if len({fingerprint(frame) for frame in actual}) < 2:
            raise ValueError('Encoded GIF has no visible animation')
        if sum(durations) != sum(timings) or min(durations) <= 0 or loop != 0:
            raise ValueError('Encoded timing or loop changed')
        for box in locked_boxes:
            expected = fingerprint(actual[0].crop(box))
            if any(fingerprint(frame.crop(box)) != expected for frame in actual[1:]):
                raise ValueError(f'Locked region moved in decoded GIF: {box}')
        with output.open('xb') as handle:
            handle.write(data)
        return {'frames': len(actual), 'duration_ms': sum(durations), 'bytes': len(data), 'colors': colors}
    raise ValueError('GIF exceeds the size target at the supported palettes. Shorten inactive time, cut redundant motion or simplify frame detail and recheck.')
