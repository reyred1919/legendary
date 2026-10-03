"""Validated, deliberately small appearance vocabulary for server-rendered pages."""

import re
import struct
import uuid
import zlib

from django.core.exceptions import ValidationError


DEFAULT_NAME = "مرکز خدمات راهکارهای هوشمند"
DEFAULT_PRIMARY = "#1764a5"
DEFAULT_ACCENT = "#0d786c"
FONT_CHOICES = (
    ("system", "Vazirmatn (پیش‌فرض)"),
    ("tahoma", "Tahoma"),
    ("arial", "Arial"),
)
FONT_STACKS = {
    "system": '"Vazirmatn", Tahoma, "Segoe UI", system-ui, sans-serif',
    "tahoma": 'Tahoma, "Vazirmatn", Arial, sans-serif',
    "arial": 'Arial, Tahoma, "Vazirmatn", sans-serif',
}


def validate_brand_color(value):
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", value or ""):
        raise ValidationError("رنگ باید به صورت #RRGGBB وارد شود.")
    channels = [int(value[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4 for channel in channels]
    luminance = sum(channel * weight for channel, weight in zip(linear, (0.2126, 0.7152, 0.0722)))
    if 1.05 / (luminance + 0.05) < 4.5:
        raise ValidationError("این رنگ برای متن سفید خوانایی کافی ندارد؛ رنگ تیره‌تری انتخاب کنید.")


def darker(value, factor):
    return "#" + "".join(f"{round(int(value[i:i + 2], 16) * factor):02x}" for i in (1, 3, 5))


def brand_logo_path(instance, filename):
    return f"branding/{uuid.uuid4().hex}.png"


def validate_png_logo(file):
    if not file:
        return
    if not file.name.lower().endswith(".png") or file.size > 1024 * 1024 or file.size < 33:
        raise ValidationError("لوگو باید PNG معتبر و کوچک‌تر از یک مگابایت باشد.")
    position = file.tell()
    try:
        payload = file.read()
    finally:
        file.seek(position)
    if payload[:8] != b"\x89PNG\r\n\x1a\n" or payload[8:16] != b"\x00\x00\x00\rIHDR":
        raise ValidationError("ساختار فایل PNG معتبر نیست.")
    width, height = struct.unpack(">II", payload[16:24])
    if not (16 <= width <= 2048 and 16 <= height <= 2048):
        raise ValidationError("ابعاد لوگو باید بین ۱۶ و ۲۰۴۸ پیکسل باشد.")
    cursor, image_data, finished = 8, False, False
    while cursor + 12 <= len(payload):
        length = struct.unpack(">I", payload[cursor:cursor + 4])[0]
        end = cursor + 12 + length
        if end > len(payload): break
        kind = payload[cursor + 4:cursor + 8]
        chunk = payload[cursor + 8:end - 4]
        checksum = struct.unpack(">I", payload[end - 4:end])[0]
        if checksum != zlib.crc32(kind + chunk): break
        image_data |= kind == b"IDAT"
        cursor = end
        if kind == b"IEND":
            finished = True
            break
    if not image_data or not finished or cursor != len(payload):
        raise ValidationError("فایل PNG ناقص یا نامعتبر است.")
