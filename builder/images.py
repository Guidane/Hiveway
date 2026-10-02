"""Uploaded pictures: turned upright, at most 2000 px, re-encoded (which drops
camera and GPS metadata)."""
from io import BytesIO

from django.core.files.base import ContentFile

MAX_UPLOAD = 20 * 1024 * 1024
MAX_SIDE = 2000


class BadImage(Exception):
    pass


def process(upload):
    from PIL import Image, ImageOps, UnidentifiedImageError

    if upload.size > MAX_UPLOAD:
        raise BadImage(f'{upload.name} is larger than 20 MB.')
    try:
        image = Image.open(upload)
        image.load()
    except (UnidentifiedImageError, OSError):
        raise BadImage(f'{upload.name} is not an image Osmia can read.')
    image = ImageOps.exif_transpose(image)
    image.thumbnail((MAX_SIDE, MAX_SIDE))
    buffer = BytesIO()
    stem = (upload.name.rsplit('.', 1)[0] or 'image')[:80]
    if image.mode in ('RGBA', 'LA', 'P'):
        image.save(buffer, 'PNG', optimize=True)
        name = f'{stem}.png'
    else:
        image.convert('RGB').save(buffer, 'JPEG', quality=88)
        name = f'{stem}.jpg'
    return ContentFile(buffer.getvalue(), name=name)
