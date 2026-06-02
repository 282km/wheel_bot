from __future__ import annotations

import io
import math
from colorsys import hls_to_rgb
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

_WHEEL_BG = (26, 30, 42, 255)
_RING = (47, 54, 71, 255)


def _ease_out_cubic(t: float) -> float:
    return 1 - pow(1 - t, 3)


def _cut(s: str, max_len: int) -> str:
    s = s.strip()
    if len(s) <= max_len:
        return s
    return s[: max_len - 1] + "…"


def _hue_to_rgb(hue_deg: int) -> tuple[int, int, int]:
    h = (int(hue_deg) % 360) / 360.0
    r, g, b = hls_to_rgb(h, 0.48, 0.70)
    return int(r * 255), int(g * 255), int(b * 255)


def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"),
        Path("C:/Windows/Fonts/arialbd.ttf"),
        Path("C:/Windows/Fonts/arial.ttf"),
    ]
    for fp in candidates:
        try:
            if fp.exists():
                return ImageFont.truetype(str(fp), size=size)
        except Exception:
            continue
    return ImageFont.load_default()


def _text_bbox(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> tuple[float, float]:
    bb = draw.textbbox((0, 0), text, font=font)
    return float(bb[2] - bb[0]), float(bb[3] - bb[1])


def _fit_label_font(
    draw: ImageDraw.ImageDraw, nick: str, max_w: float, max_font: int, min_font: int
) -> tuple[str, ImageFont.ImageFont, float, float]:
    label = _cut(nick, 18)
    font_size = max_font
    tw, th = 0.0, 0.0
    font = _load_font(font_size)
    while font_size >= min_font:
        font = _load_font(font_size)
        tw, th = _text_bbox(draw, label, font)
        if tw <= max_w:
            return label, font, tw, th
        font_size -= 1
    font = _load_font(min_font)
    while len(label) > 1:
        label = label[:-1]
        tw, th = _text_bbox(draw, f"{label}…", font)
        if tw <= max_w:
            return f"{label}…", font, tw, th
    return label, font, tw, th


def _paste_radial_label(
    img: Image.Image,
    label: str,
    font: ImageFont.ImageFont,
    lx: float,
    ly: float,
    mid_rad: float,
    bbox: tuple[int, int, int, int],
    start: float,
    end: float,
    size: int,
) -> Image.Image:
    tw, th = _text_bbox(ImageDraw.Draw(img), label, font)
    pad = max(4, size // 64)
    box = int(max(tw, th) + pad * 2)
    txt = Image.new("RGBA", (box, box), (0, 0, 0, 0))
    tdraw = ImageDraw.Draw(txt)
    tdraw.text(
        (box / 2 - tw / 2, box / 2 - th / 2),
        label,
        fill=(255, 255, 255, 255),
        font=font,
        stroke_width=max(1, size // 160),
        stroke_fill=(0, 0, 0, 220),
    )

    rot_deg = math.degrees(mid_rad)
    if math.cos(mid_rad) < 0:
        rot_deg += 180
    txt = txt.rotate(-rot_deg, expand=True, resample=Image.Resampling.BICUBIC)

    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    layer.paste(txt, (int(lx - txt.width / 2), int(ly - txt.height / 2)), txt)

    sector_mask = Image.new("L", img.size, 0)
    smask = ImageDraw.Draw(sector_mask)
    smask.pieslice(bbox, start, end, fill=255)
    return Image.composite(layer, img, sector_mask)


def _wheel_layer(size: int, roster: list[tuple[str, str, int]]) -> Image.Image:
    """
    roster entries: (nick, description, hue_degrees)
    Sectors start at 12 o'clock, clockwise.
    """
    n = len(roster)
    if n < 2:
        raise ValueError("need at least 2 sectors")

    img = Image.new("RGBA", (size, size), _WHEEL_BG)
    draw = ImageDraw.Draw(img)
    cx = cy = size // 2
    pad = max(8, size // 32)
    outer_r = size // 2 - pad
    bbox = (pad, pad, size - pad, size - pad)
    step_deg = 360.0 / n
    label_r = outer_r * 0.6
    max_font = max(10, min(17, int(size / (7 + n * 0.45))))
    min_font = max(8, max_font - 6)

    for i in range(n):
        nick, _desc, hue = roster[i]
        start = -90.0 + i * step_deg
        end = start + step_deg
        r, g, b = _hue_to_rgb(hue)
        draw.pieslice(bbox, start, end, fill=(r, g, b, 255), outline=(20, 20, 20, 255))

    draw.ellipse(bbox, outline=_RING, width=max(2, size // 128))

    measure = ImageDraw.Draw(img)
    for i in range(n):
        nick, _desc, _hue = roster[i]
        start = -90.0 + i * step_deg
        end = start + step_deg
        mid_rad = math.radians(-90.0 + (i + 0.5) * step_deg)
        lx = cx + label_r * math.cos(mid_rad)
        ly = cy + label_r * math.sin(mid_rad)
        max_w = 2 * label_r * math.sin(math.radians(step_deg / 2)) * 0.96
        label, font, _tw, _th = _fit_label_font(measure, nick, max_w, max_font, min_font)
        img = _paste_radial_label(img, label, font, lx, ly, mid_rad, bbox, start, end, size)

    return img


def _pointer_layer(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    cx = size // 2
    top = max(6, size // 48)
    w = max(10, size // 28)
    pts = [(cx, top), (cx - w, top + w * 2), (cx + w, top + w * 2)]
    draw.polygon(pts, fill=(255, 209, 102, 255), outline=(120, 80, 0))
    return img


def _round_badge_layer(size: int, round_no: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    text = f"Раунд {round_no}"
    font = _load_font(max(14, size // 24))
    tw, th = _text_bbox(draw, text, font)
    pad_x = max(8, size // 40)
    pad_y = max(4, size // 80)
    x0 = (size - tw) / 2 - pad_x
    y0 = size - th - pad_y * 3
    x1 = (size + tw) / 2 + pad_x
    y1 = size - pad_y
    draw.rounded_rectangle((x0, y0, x1, y1), radius=8, fill=(15, 18, 28, 210))
    draw.text((size / 2 - tw / 2, y0 + pad_y), text, fill=(255, 255, 255, 255), font=font)
    return img


def _final_rotation_degrees(n: int, winner_slot: int, extra_turns: int = 5) -> float:
    step = 360.0 / n
    mid_w = -90.0 + (winner_slot + 0.5) * step
    target = 270.0
    return extra_turns * 360.0 + ((mid_w - target) % 360.0)


def _compose_frame(base: Image.Image, pointer: Image.Image, angle_deg: float, badge: Image.Image | None = None) -> Image.Image:
    rotated = base.rotate(
        angle_deg,
        resample=Image.Resampling.BICUBIC,
        expand=False,
        fillcolor=_WHEEL_BG[:3],
    )
    composed = Image.alpha_composite(rotated, pointer)
    if badge is not None:
        composed = Image.alpha_composite(composed, badge)
    return composed.convert("RGB")


def _round_spin_frames(
    roster: list[tuple[str, str, int]],
    winner_slot: int,
    *,
    size: int = 384,
    fps: int = 8,
    spin_sec: float = 2.0,
) -> list[Image.Image]:
    n = len(roster)
    if n < 2:
        raise ValueError("need at least 2 sectors")
    if winner_slot < 0 or winner_slot >= n:
        raise ValueError("bad winner_slot")

    frame_count = max(14, min(28, int(spin_sec * fps)))
    base = _wheel_layer(size, roster)
    pointer = _pointer_layer(size)
    r_final = _final_rotation_degrees(n, winner_slot)

    frames: list[Image.Image] = []
    for i in range(frame_count):
        t = i / max(1, frame_count - 1)
        ang = _ease_out_cubic(t) * r_final
        frames.append(_compose_frame(base, pointer, ang))
    return frames


def _save_gif(frames: list[Image.Image], fps: int) -> bytes:
    if not frames:
        raise ValueError("no frames")
    duration_ms = int(round(1000 / max(1, fps)))
    buf = io.BytesIO()
    frames[0].save(
        buf,
        format="GIF",
        save_all=True,
        append_images=frames[1:],
        duration=duration_ms,
        loop=0,
        optimize=True,
        disposal=2,
    )
    return buf.getvalue()


def render_spin_gif(
    roster: list[tuple[str, str, int]],
    winner_slot: int,
    duration_sec: float = 3.0,
    fps: int = 10,
) -> bytes:
    """Single-round GIF (kept for compatibility)."""
    frames = _round_spin_frames(roster, winner_slot, size=384, fps=fps, spin_sec=duration_sec)
    return _save_gif(frames, fps)


def render_multi_round_spin_gif(
    rounds: list[tuple[list[tuple[str, str, int]], int]],
    *,
    size: int = 384,
    fps: int = 8,
    spin_sec: float = 2.0,
    hold_sec: float = 1.0,
    gap_sec: float = 0.2,
) -> bytes:
    """
    One GIF: spin + pause for each round in order.
    rounds: list of (roster, winner_slot_index).
    """
    if not rounds:
        raise ValueError("no rounds")

    all_frames: list[Image.Image] = []
    hold_count = max(2, int(hold_sec * fps))
    gap_count = max(1, int(gap_sec * fps))

    for rnd_idx, (roster, winner_slot) in enumerate(rounds, start=1):
        spin_frames = _round_spin_frames(roster, winner_slot, size=size, fps=fps, spin_sec=spin_sec)
        all_frames.extend(spin_frames)

        badge = _round_badge_layer(size, rnd_idx)
        pointer = _pointer_layer(size)
        base = _wheel_layer(size, roster)
        r_final = _final_rotation_degrees(len(roster), winner_slot)
        still = _compose_frame(base, pointer, r_final, badge)

        for _ in range(hold_count):
            all_frames.append(still.copy())

        if rnd_idx < len(rounds):
            for _ in range(gap_count):
                all_frames.append(still.copy())

    return _save_gif(all_frames, fps)
