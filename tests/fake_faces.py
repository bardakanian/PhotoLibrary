"""A stand-in for the face models: test photos carry their "people" in their colours.

`write_people_photo(path, taken, (2, 5))` writes a photo whose left half shows person 2 and
right half person 5; `FakeFaceEngine` reads them back and returns one face per half, with a
fingerprint close to that person's own random direction. Person 0 means no face.
"""

import itertools
from datetime import datetime
from pathlib import Path

import numpy as np
from PIL import Image

from photolibrary.core.faces import EMBEDDING_SIZE, Face

_noise = itertools.count()


def person_vector(person: int) -> np.ndarray:
    vector = np.random.default_rng(person * 7919).normal(size=EMBEDDING_SIZE)
    return vector / np.linalg.norm(vector)


def write_people_photo(path: Path, taken: datetime, people: tuple[int, ...]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (32, 16), (0, 0, 0))
    for slot, person in enumerate(people[:2]):
        image.paste((person * 20, 40, 40), (slot * 16, 0, slot * 16 + 16, 16))
    exif = Image.Exif()
    exif.get_ifd(0x8769)[36867] = taken.strftime("%Y:%m:%d %H:%M:%S")
    image.save(path, quality=100, subsampling=0, exif=exif)
    return path


class FakeFaceEngine:
    version = 1

    def __init__(self):
        self.calls = 0

    def detect(self, image: Image.Image) -> list[Face]:
        self.calls += 1
        rgb = image.convert("RGB")
        faces = []
        for slot in range(2):
            red = rgb.getpixel((slot * rgb.width // 2 + rgb.width // 4, rgb.height // 2))[0]
            person = round(red / 20)
            if person:
                noise = np.random.default_rng(next(_noise)).normal(scale=0.03, size=EMBEDDING_SIZE)
                vector = person_vector(person) + noise
                vector = (vector / np.linalg.norm(vector)).astype(np.float32)
                faces.append(Face(slot * 0.5 + 0.1, 0.2, 0.3, 0.6, 0.95, vector.tobytes()))
        return faces
