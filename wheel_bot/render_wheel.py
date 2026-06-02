from __future__ import annotations

import io
import logging
import math
import shutil
import subprocess
import tempfile
from colorsys import hls_to_rgb
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

log = logging.getLogger(__name__)

_WHEEL_BG_RGB = (26, 30, 42)
_RING_RGB = (47, 54, 71)


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


def _pillow_xy(cx: float, cy: float, radius: float, deg_cw: float) -> tuple[float, float]:
    """Pillow angles: 0° = 3 o'clock, clockwise."""
    rad = math.radians(deg_cw)
    return cx + radius * math.cos(rad), cy + radius * math.sin(rad)


def _sector_polygon(cx: float, cy: float, radius: float, start_deg: float, end_deg: float, steps: int = 40) -> list[tuple[float, float]]:
    pts: list[tuple[float, float]] = [(cx, cy)]
    span = end_deg - start_deg
    for i in range(steps + 1):
        deg = start_deg + span * (i / steps)
        pts.append(_pillow_xy(cx, cy, radius, deg))
    return pts


def _fit_label_font(
    draw: ImageDraw.ImageDraw, nick: str, max_w: float, max_font: int, min_font: int
) -> tuple[str, ImageFont.ImageFont, float, float]:
    label = _cut(nick, 24)
    font_size = max_font
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
    size: int,
) -> None:
    tw, th = _text_bbox(ImageDraw.Draw(img), label, font)
    pad = max(6, size // 48)
    box = int(max(tw + pad * 2, th + pad * 2, 24))
    txt = Image.new("RGBA", (box, box), (0, 0, 0, 0))
    tdraw = ImageDraw.Draw(txt)
    tdraw.text(
        (box / 2 - tw / 2, box / 2 - th / 2),
        label,
        fill=(255, 255, 255, 255),
        font=font,
        stroke_width=max(1, size // 140),
        stroke_fill=(0, 0, 0, 230),
    )

    rot_deg = math.degrees(mid_rad)
    if math.cos(mid_rad) < 0:
        rot_deg += 180
    txt = txt.rotate(-rot_deg, expand=True, resample=Image.Resampling.BICUBIC)

    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    overlay.paste(txt, (int(lx - txt.width / 2), int(ly - txt.height / 2)), txt)
    base = img.convert("RGBA")
    img.paste(Image.alpha_composite(base, overlay).convert("RGB"))


def _wheel_layer(size: int, roster: list[tuple[str, str, int]]) -> Image.Image:
    """roster entries: (nick, description, hue_degrees). Sectors from 12 o'clock."""
    n = len(roster)
    if n < 2:
        raise ValueError("need at least 2 sectors")

    img = Image.new("RGB", (size, size), _WHEEL_BG_RGB)
    draw = ImageDraw.Draw(img)
    cx = cy = size / 2
    pad = max(8, size // 32)
    outer_r = size / 2 - pad
    step_deg = 360.0 / n
    label_r = outer_r * 0.62
    max_font = max(11, min(18, int(size / (6 + n * 0.4))))
    min_font = max(9, max_font - 5)

    for i in range(n):
        _nick, _desc, hue = roster[i]
        start = -90.0 + i * step_deg
        end = start + step_deg
        poly = _sector_polygon(cx, cy, outer_r, start, end)
        draw.polygon(poly, fill=_hue_to_rgb(hue), outline=(18, 18, 18))

    ring_bbox = (pad, pad, size - pad, size - pad)
    draw.ellipse(ring_bbox, outline=_RING_RGB, width=max(2, size // 128))

    measure = ImageDraw.Draw(img)
    for i in range(n):
        nick, _desc, _hue = roster[i]
        mid_deg = -90.0 + (i + 0.5) * step_deg
        mid_rad = math.radians(mid_deg)
        lx, ly = _pillow_xy(cx, cy, label_r, mid_deg)
        chord = 2 * label_r * math.sin(math.radians(step_deg / 2))
        max_w = max(chord * 1.15, outer_r * 0.22)
        label, font, _tw, _th = _fit_label_font(measure, nick, max_w, max_font, min_font)
        _paste_radial_label(img, label, font, lx, ly, mid_rad, size)

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


def _round_winner_overlay_layer(size: int, round_no: int, winner: str) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    round_text = f"Раунд {round_no}"
    winner_text = _cut(winner, 40)

    pad_x = max(10, size // 32)
    pad_y = max(6, size // 54)
    banner_w = size - pad_x * 2
    banner_x0 = pad_x
    banner_x1 = size - pad_x

    # Two-line banner near the top so it doesn't collide with the wheel labels.
    # Choose font sizes to fit without harsh truncation.
    top_font = _load_font(max(14, size // 26))
    top_tw, top_th = _text_bbox(draw, round_text, top_font)

    max_winner_font = max(16, size // 22)
    min_winner_font = max(12, max_winner_font - 6)

    cur_size = max_winner_font
    winner_font = _load_font(cur_size)
    w_tw, w_th = _text_bbox(draw, winner_text, winner_font)
    # If font cannot fit, decrease until it fits or we hit min_winner_font.
    while w_tw > banner_w - pad_x * 0.3 and cur_size > min_winner_font:
        cur_size -= 1
        winner_font = _load_font(cur_size)
        w_tw, w_th = _text_bbox(draw, winner_text, winner_font)

    banner_h = top_th + w_th + pad_y * 2 + max(4, size // 120)
    banner_y0 = pad_y
    banner_y1 = banner_y0 + banner_h

    draw.rounded_rectangle(
        (banner_x0, banner_y0, banner_x1, banner_y1),
        radius=max(10, size // 40),
        fill=(15, 18, 28, 220),
        outline=(60, 60, 80, 120),
        width=max(1, size // 140),
    )

    draw.text(
        (size / 2 - top_tw / 2, banner_y0 + pad_y / 2),
        round_text,
        fill=(255, 255, 255, 255),
        font=top_font,
    )
    draw.text(
        (size / 2 - w_tw / 2, banner_y0 + pad_y / 2 + top_th + pad_y * 0.3),
        winner_text,
        fill=(255, 255, 255, 255),
        font=winner_font,
    )
    return img


def _final_rotation_degrees(n: int, winner_slot: int, extra_turns: int = 5) -> float:
    step = 360.0 / n
    mid_w = -90.0 + (winner_slot + 0.5) * step
    target = 270.0
    return extra_turns * 360.0 + ((mid_w - target) % 360.0)


def _compose_frame(base: Image.Image, pointer: Image.Image, angle_deg: float, badge: Image.Image | None = None) -> Image.Image:
    rotated = base.rotate(angle_deg, resample=Image.Resampling.BICUBIC, expand=False, fillcolor=_WHEEL_BG_RGB)
    composed = Image.alpha_composite(rotated.convert("RGBA"), pointer)
    if badge is not None:
        composed = Image.alpha_composite(composed, badge)
    return composed.convert("RGB")


def _round_spin_frames(
    roster: list[tuple[str, str, int]],
    winner_slot: int,
    *,
    size: int = 420,
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


def _collect_multi_round_frames(
    rounds: list[tuple[list[tuple[str, str, int]], int, str]],
    *,
    size: int = 420,
    fps: int = 8,
    spin_sec: float = 2.0,
    hold_sec: float = 1.0,
    gap_sec: float = 0.2,
) -> list[Image.Image]:
    if not rounds:
        raise ValueError("no rounds")

    all_frames: list[Image.Image] = []
    hold_count = max(2, int(hold_sec * fps))
    gap_count = max(1, int(gap_sec * fps))

    for rnd_idx, (roster, winner_slot, winner_label) in enumerate(rounds, start=1):
        spin_frames = _round_spin_frames(roster, winner_slot, size=size, fps=fps, spin_sec=spin_sec)
        all_frames.extend(spin_frames)

        pointer = _pointer_layer(size)
        base = _wheel_layer(size, roster)
        r_final = _final_rotation_degrees(len(roster), winner_slot)
        overlay = _round_winner_overlay_layer(size, rnd_idx, winner_label)
        still = _compose_frame(base, pointer, r_final, overlay)

        for _ in range(hold_count):
            all_frames.append(still.copy())
        if rnd_idx < len(rounds):
            for _ in range(gap_count):
                all_frames.append(still.copy())

    return all_frames


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


def _save_mp4(frames: list[Image.Image], fps: int) -> bytes:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise OSError("ffmpeg not found")

    with tempfile.TemporaryDirectory(prefix="wheel_mp4_") as tmp:
        td = Path(tmp)
        for i, frame in enumerate(frames):
            frame.save(td / f"frame_{i:05d}.png", format="PNG")

        out_path = td / "wheel.mp4"
        cmd = [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-framerate",
            str(max(1, fps)),
            "-i",
            str(td / "frame_%05d.png"),
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-preset",
            "veryfast",
            "-crf",
            "28",
            "-movflags",
            "+faststart",
            "-an",
            str(out_path),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "ffmpeg failed").strip()
            raise RuntimeError(err)
        return out_path.read_bytes()


def render_multi_round_spin_media(
    rounds: list[tuple[list[tuple[str, str, int]], int, str]],
    *,
    size: int = 420,
    fps: int = 8,
    spin_sec: float = 2.0,
    hold_sec: float = 1.0,
    gap_sec: float = 0.2,
) -> tuple[bytes, str]:
    """Returns (file_bytes, extension without dot: mp4 or gif)."""
    frames = _collect_multi_round_frames(
        rounds,
        size=size,
        fps=fps,
        spin_sec=spin_sec,
        hold_sec=hold_sec,
        gap_sec=gap_sec,
    )
    try:
        return _save_mp4(frames, fps), "mp4"
    except Exception as e:
        log.warning("MP4 render failed, fallback to GIF: %s", e)
        return _save_gif(frames, fps), "gif"


def render_multi_round_spin_gif(
    rounds: list[tuple[list[tuple[str, str, int]], int, str]],
    **kwargs: object,
) -> bytes:
    data, ext = render_multi_round_spin_media(rounds, **kwargs)  # type: ignore[arg-type]
    if ext != "gif":
        log.info("render_multi_round_spin_gif: delivered %s", ext)
    return data


def render_spin_gif(
    roster: list[tuple[str, str, int]],
    winner_slot: int,
    duration_sec: float = 3.0,
    fps: int = 10,
) -> bytes:
    data, _ext = render_multi_round_spin_media(
        [(roster, winner_slot, str(roster[winner_slot][0] if 0 <= winner_slot < len(roster) else ""))],
        spin_sec=duration_sec,
        fps=fps,
        hold_sec=0.8,
        gap_sec=0.0,
    )
    return data
