"""Create a synthetic photo library for trying the app and checking performance.

    python tools/make_demo_library.py ~/Desktop/demo-library --photos 50000 --videos 20

Images are small procedural landscapes with spread-out EXIF dates in a few album folders.
Nothing here contains real media or personal data.
"""

import argparse
import colorsys
import random
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

from PIL import Image, ImageDraw

ALBUMS = ["", "Holidays", "Family", "Screenshots", "Trips/Iceland", "Trips/Japan", "Pets"]


# Sky palettes (top colour, horizon colour, sun colour) for different times of day.
PALETTES = [
    ((70, 130, 210), (190, 220, 245), (255, 250, 225)),   # clear day
    ((40, 60, 140), (250, 150, 90), (255, 215, 130)),     # sunset
    ((25, 30, 70), (190, 110, 140), (255, 190, 160)),     # dusk
    ((12, 16, 40), (40, 55, 100), (235, 235, 215)),       # night
    ((120, 170, 200), (235, 225, 200), (255, 245, 210)),  # hazy morning
]


def _mix(a, b, t):
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def make_photo(path: Path, taken: datetime, index: int, rng: random.Random, *,
               size: tuple[int, int] | None = None, palette: int | None = None, lake: bool | None = None,
               sun_at: tuple[float, float] | None = None) -> None:
    """A procedural landscape (sky, sun or moon, mountain ridges, sometimes a lake).

    `size`, `palette` (index into PALETTES), `lake` and `sun_at` (fractions of width and height)
    pin down an otherwise random picture.
    """
    portrait = rng.random() < 0.25
    width, height = size or ((360, 480) if portrait else (480, 360))
    scale = width / 480
    top, horizon, sun = PALETTES[palette] if palette is not None else rng.choice(PALETTES)
    image = Image.new("RGB", (width, height))
    draw = ImageDraw.Draw(image)
    for y in range(height):
        draw.line((0, y, width, y), fill=_mix(top, horizon, min(1.0, y / (height * 0.7))))
    if top[2] < 50 and rng.random() < 0.8:  # stars at night
        for _ in range(int(60 * scale * scale)):
            x, y = rng.randrange(width), rng.randrange(int(height * 0.5))
            draw.point((x, y), fill=(230, 230, 255))
    radius = int(rng.randint(18, 42) * scale)
    sun_x, sun_y = rng.randint(radius, width - radius), rng.randint(int(height * 0.12), int(height * 0.45))
    if sun_at is not None:
        sun_x, sun_y = int(width * sun_at[0]), int(height * sun_at[1])
    draw.ellipse((sun_x - radius, sun_y - radius, sun_x + radius, sun_y + radius), fill=sun)

    hue = rng.random()
    ridges = rng.randint(2, 4)
    lake = rng.random() < 0.35 if lake is None else lake
    ground = height * (0.72 if lake else 1.0)
    for layer in range(ridges):
        depth = (layer + 1) / ridges
        colour = tuple(int(c * 255) for c in colorsys.hsv_to_rgb(
            (hue + layer * 0.03) % 1, 0.35 + 0.3 * depth, 0.75 - 0.5 * depth))
        colour = _mix(colour, horizon, 0.45 * (1 - depth))  # distant ridges fade into the haze
        base = height * (0.45 + 0.12 * layer)
        points = [(0, ground)]
        y = base + rng.uniform(-30, 30) * scale
        step = max(1, int(24 * scale))
        for x in range(0, width + step, step):
            y = min(ground - 10 * scale, max(height * 0.3, y + rng.uniform(-28, 28) * scale))
            points.append((x, y))
        points.append((width, ground))
        draw.polygon(points, fill=colour)
    if lake:
        for y in range(int(ground), height):
            t = (y - ground) / max(1, height - ground)
            draw.line((0, y, width, y), fill=_mix(_mix(horizon, top, 0.3), top, t))
        draw.line((0, int(ground), width, int(ground)), fill=_mix(horizon, (255, 255, 255), 0.4),
                  width=max(1, int(scale)))
        # A soft reflection of the sun on the water.
        for row in range(int(ground) + int(6 * scale), height, max(2, int(7 * scale))):
            half = radius * (1 - (row - ground) / (height - ground)) * 0.9
            draw.line((sun_x - half, row, sun_x + half, row), fill=_mix(sun, top, 0.45), width=max(1, int(2 * scale)))

    exif = Image.Exif()
    exif[271], exif[272] = "Demo", rng.choice(["Camera One", "Camera Two", "Phone X"])
    exif.get_ifd(0x8769)[36867] = taken.strftime("%Y:%m:%d %H:%M:%S")
    image.save(path, quality=85, exif=exif)


def make_video(path: Path, taken: datetime, rng: random.Random) -> bool:
    try:
        import imageio_ffmpeg
    except ImportError:
        return False
    colour = rng.choice(["teal", "orange", "purple", "navy", "olive"])
    seconds = rng.choice([0.5, 2, 4, 7])
    subprocess.run([
        imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", f"testsrc2=s=640x360:d={seconds}",
        "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
        "-vf", f"drawbox=c={colour}@0.4:t=fill", "-pix_fmt", "yuv420p", "-shortest",
        "-metadata", f"creation_time={taken:%Y-%m-%dT%H:%M:%S}Z", str(path),
    ], check=True)
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("folder", type=Path)
    parser.add_argument("--photos", type=int, default=2000)
    parser.add_argument("--videos", type=int, default=10)
    parser.add_argument("--years", type=int, default=12)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()
    rng = random.Random(args.seed)
    end = datetime(2026, 10, 1)
    span = timedelta(days=365 * args.years)

    def random_date() -> datetime:
        return (end - span * rng.random()).replace(microsecond=0)

    for index in range(args.photos):
        folder = args.folder / rng.choice(ALBUMS)
        folder.mkdir(parents=True, exist_ok=True)
        make_photo(folder / f"IMG_{index:05d}.jpg", random_date(), index, rng)
        if index and index % 1000 == 0:
            print(f"{index:,} photos…")
    videos = 0
    for index in range(args.videos):
        folder = args.folder / rng.choice(ALBUMS)
        folder.mkdir(parents=True, exist_ok=True)
        videos += make_video(folder / f"MOV_{index:04d}.mp4", random_date(), rng)
    print(f"Created {args.photos:,} photos and {videos} videos in {args.folder}")


if __name__ == "__main__":
    main()
