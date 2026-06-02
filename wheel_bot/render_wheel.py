from __future__ import annotations

import io
import math
from colorsys import hls_to_rgb
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def _ease_out_cubic(t: float) -> float:
    return 1 - pow(1 - t, 3)


def _cut(s: str, max_len: int) -> str:
    s = s.strip()
    if len(s) <= max_len:
        return s
    return s[: max_len - 1] + "…"


def _label_lines_for_sector(nick: str, desc: str) -> tuple[str, str]:
    nick_line = _cut(nick, 14)
    if desc.strip():
        desc_line = _cut(f"({desc.strip()})", 16)
    else:
        desc_line = ""
    return nick_line, desc_line


def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"),
        Path("C:/Windows/Fonts/arial.ttf"),
    ]
    for fp in candidates:
        try:
            if fp.exists():
                return ImageFont.truetype(str(fp), size=size)
        except Exception:
            continue
    return ImageFont.load_default()


def _wheel_layer(size: int, roster: list[tuple[str, str, int]]) -> Image.Image:
    """
    roster entries: (nick, description, hue_degrees)
    """
    n = len(roster)
    if n < 2:
        raise ValueError("need at least 2 sectors")

    img = Image.new("RGBA", (size, size), (255, 255, 255, 255))
    draw = ImageDraw.Draw(img)
    cx = cy = size // 2
    pad = max(8, size // 40)
    bbox = (pad, pad, size - pad, size - pad)

    font = _load_font(max(12, size // 30))

    for i in range(n):
        nick, desc, hue = roster[i]
        start = i * (360.0 / n)
        end = (i + 1) * (360.0 / n)
        r, g, b = hls_to_rgb((hue % 360) / 360.0, 0.62, 0.95)
        fill = (int(r * 255), int(g * 255), int(b * 255))
        draw.pieslice(bbox, start, end, fill=fill, outline=(40, 40, 40))

        mid = (i + 0.5) * (360.0 / n)
        rad = math.radians(mid)
        tr = (size // 2 - pad) * 0.52
        tx = cx + tr * math.cos(rad)
        # Pillow arcs on image coordinates (Y grows downward), so plus sin places labels
        # into the same visual sector as pieslice angles.
        ty = cy + tr * math.sin(rad)

        line1, line2 = _label_lines_for_sector(nick, desc)
        text = line1 if not line2 else f"{line1}\n{line2}"
        tw, th = _text_bbox(draw, text, font)
        draw.multiline_text(
            (tx - tw / 2, ty - th / 2),
            text,
            fill=(10, 10, 10),
            font=font,
            align="center",
            spacing=1,
        )

    return img


def _text_bbox(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> tuple[float, float]:
    bb = draw.multiline_textbbox((0, 0), text, font=font, spacing=1)
    return float(bb[2] - bb[0]), float(bb[3] - bb[1])


def _pointer_layer(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    cx = size // 2
    top = max(6, size // 48)
    w = max(10, size // 28)
    pts = [(cx, top), (cx - w, top + w * 2), (cx + w, top + w * 2)]
    draw.polygon(pts, fill=(220, 20, 20), outline=(60, 0, 0))
    return img


def render_spin_gif(roster: list[tuple[str, str, int]], winner_slot: int, duration_sec: float = 3.0, fps: int = 10) -> bytes:
    """
    roster order matches sectors 0..N-1 starting from 3 o'clock.
    winner_slot: index in roster for winning sector.
    """
    n = len(roster)
    if n < 2:
        raise ValueError("need at least 2 sectors")
    if winner_slot < 0 or winner_slot >= n:
        raise ValueError("bad winner_slot")

    size = 384
    frames = max(18, min(36, int(duration_sec * fps)))
    frame_duration_ms = int(round(1000 * duration_sec / max(1, frames - 1)))

    base = _wheel_layer(size, roster)
    pointer = _pointer_layer(size)

    sector_angle = 360.0 / n
    center_w = (winner_slot + 0.5) * sector_angle
    # Pointer is at 12 o'clock (270° in Pillow angle system). Since image
    # rotation direction is opposite to sector angle growth in our drawing
    # coordinates, use reversed delta.
    target_angle = 270.0
    extra_turns = 5
    r_final = extra_turns * 360.0 + ((center_w - target_angle) % 360.0)

    imgs: list[Image.Image] = []
    for i in range(frames):
        t = i / max(1, frames - 1)
        ang = _ease_out_cubic(t) * r_final
        rotated = base.rotate(ang, resample=Image.Resampling.BICUBIC, expand=False, fillcolor=(255, 255, 255, 255))
        composed = Image.alpha_composite(rotated, pointer)
        imgs.append(composed.convert("RGB"))

    try:
        pal = imgs[0].quantize(colors=96)
        quantized = [pal] + [im.quantize(palette=pal) for im in imgs[1:]]
        frames = quantized
    except Exception:
        frames = imgs

    buf = io.BytesIO()
    frames[0].save(
        buf,
        format="GIF",
        save_all=True,
        append_images=frames[1:],
        duration=frame_duration_ms,
        loop=0,
        optimize=True,
    )
    return buf.getvalue()
