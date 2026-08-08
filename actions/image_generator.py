"""
MARK XLIX — AI Image Generator

True text-to-image generation via Google Imagen (google-genai, already a
project dependency), with a graceful offline fallback that draws a stylised
gradient image with PIL.

Previously the ``generate_image`` tool only drew a PIL placeholder — this
module upgrades it to real AI generation. The fallback is kept so the feature
still works without network access / API quota.

Public API
----------
``generate_image(parameters: dict) -> dict``
    Returns ``{"result", "path", "image_bytes", "mime", "ai", "prompt"}``.
    ``ai`` is True when Imagen succeeded, False when the fallback was used.
"""

from __future__ import annotations

import time
from pathlib import Path

IMAGE_DIR = Path.home() / "Pictures" / "Jarvis"

# Imagen model (available to free-tier Gemini API keys).
IMAGEN_MODEL = "imagen-3.0-generate-002"

# Style → prompt hint injected to steer the model's output.
_STYLE_HINTS: dict[str, str] = {
    "realistic":    "photorealistic, natural lighting, sharp detail",
    "artistic":     "expressive artistic painting, bold composition",
    "cartoon":      "cartoon illustration, clean bold lines, vibrant colors",
    "pixel":        "pixel art, retro 8-bit video game sprite style",
    "watercolor":   "watercolor painting, soft washes, delicate paper texture",
    "sketch":       "pencil sketch, black and white line art",
    "abstract":     "abstract art, flowing shapes and color fields",
    "retro":        "retro 1980s aesthetic, vintage poster style",
    "neon":         "neon glow, dark background, electric vivid colors",
    "cyberpunk":    "cyberpunk cityscape, neon lights, futuristic atmosphere",
    "minimalist":   "minimalist composition, clean, generous negative space",
    "vintage":      "vintage photograph, faded tones, subtle film grain",
    "fantasy":      "epic fantasy art, magical atmosphere, rich detail",
    "anime":        "anime style, detailed cel shading, expressive",
    "oil_painting": "oil painting, classical style, textured brushstrokes",
}

# Human presets → Imagen aspect-ratio strings.
_ASPECT_RATIOS: dict[str, str] = {
    "square":    "1:1",
    "portrait":  "3:4",
    "landscape": "4:3",
    "wide":      "16:9",
    "cinematic": "16:9",
}

# Fallback size presets (used only when Imagen is unavailable).
_FALLBACK_SIZES: dict[str, tuple[int, int]] = {
    "square":    (512, 512),
    "portrait":  (512, 768),
    "landscape": (768, 512),
    "wide":      (1024, 576),
    "cinematic": (1024, 576),
}


def _log(message: str) -> None:
    """Console logging that survives legacy (cp1252) Windows consoles."""
    try:
        print(message)
    except UnicodeEncodeError:
        print(message.encode("ascii", "replace").decode("ascii"))


def _save_bytes(data: bytes, mime: str) -> Path:
    """Persist image bytes to ~/Pictures/Jarvis and return the path."""
    ext = "png" if (mime or "").endswith("png") else "jpg"
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    name = f"generated_{int(time.time() * 1000)}.{ext}"
    path = IMAGE_DIR / name
    path.write_bytes(data)
    return path


def _build_prompt(prompt: str, style: str, color_scheme: str | None) -> str:
    """Augment the user prompt with the selected style / color scheme."""
    parts = [prompt.strip()]
    hint = _STYLE_HINTS.get(style.lower())
    if hint:
        parts.append(f"Style: {hint}.")
    if color_scheme and color_scheme.lower() != "auto":
        parts.append(f"Color palette: {color_scheme}.")
    return " ".join(parts)


def _generate_with_imagen(
    prompt: str,
    style: str,
    aspect_ratio: str | None,
    color_scheme: str | None = None,
) -> tuple[bytes, str]:
    """
    Try real AI generation via Google Imagen.

    Raises on any failure so the caller can fall back to PIL.
    """
    from google import genai
    from google.genai import types

    from utils import get_api_key

    client = genai.Client(
        api_key=get_api_key(),
        http_options={"api_version": "v1beta"},
    )

    enhanced = _build_prompt(prompt, style, color_scheme)
    ar = _ASPECT_RATIOS.get((aspect_ratio or "square").lower(), "1:1")

    response = client.models.generate_images(
        model=IMAGEN_MODEL,
        prompt=enhanced,
        config=types.GenerateImagesConfig(
            number_of_images=1,
            aspect_ratio=ar,
            output_mime_type="image/png",
            enhance_prompt=True,
        ),
    )

    generated = (response.generated_images or []) if response else []
    if not generated:
        raise RuntimeError("Imagen returned no images")
    image = generated[0].image
    if not image or not image.image_bytes:
        raise RuntimeError("Imagen returned an empty image payload")
    return bytes(image.image_bytes), "image/png"


# ── PIL fallback (kept from the original placeholder generator) ────────────────

def _fallback_with_pil(
    prompt: str,
    style: str,
    width: int,
    height: int,
    color_scheme: str | None,
    complexity: str,
) -> tuple[bytes, str]:
    """Draw a stylised gradient image with PIL — offline fallback."""
    import random

    import PIL.Image
    import PIL.ImageDraw
    import PIL.ImageFont

    img = PIL.Image.new("RGB", (width, height))
    draw = PIL.ImageDraw.Draw(img)

    # Colour-scheme defaults per style
    if not color_scheme:
        if style in ("neon", "cyberpunk"):
            color_scheme = "vibrant"
        elif style in ("watercolor", "sketch"):
            color_scheme = "muted"
        elif style in ("dark", "cyberpunk"):
            color_scheme = "dark"
        else:
            color_scheme = "auto"

    def _gradient(y: int) -> tuple[int, int, int]:
        t = y / max(1, height)
        if style == "artistic" or color_scheme == "vibrant":
            r, g, b = int(255 * (1 - t)), int(100 * t), int(255 * t)
        elif style == "cartoon" or color_scheme == "bright":
            r, g, b = int(100 + 155 * t), int(200 * (1 - t)), int(100 + 100 * t)
        elif style == "watercolor" or color_scheme == "muted":
            r, g, b = int(180 + 75 * t), int(150 + 100 * (1 - t)), int(200 + 55 * t)
        elif style == "sketch" or color_scheme == "monochrome":
            v = int(200 + 55 * t)
            r, g, b = v, v, v
        elif style in ("dark", "cyberpunk") or color_scheme == "dark":
            r, g, b = int(20 + 30 * t), int(10 + 20 * t), int(40 + 60 * t)
        elif style == "neon":
            r, g, b = int(50 + 200 * t), int(255 * (1 - t)), int(100 + 155 * t)
        elif style in ("retro", "vintage"):
            r, g, b = int(180 + 75 * t), int(120 + 80 * (1 - t)), int(80 + 70 * t)
        elif color_scheme == "warm":
            r, g, b = int(255 * (1 - t * 0.3)), int(150 + 50 * t), int(100 + 50 * t)
        elif color_scheme == "cool":
            r, g, b = int(100 + 50 * t), int(150 + 100 * (1 - t)), int(200 + 55 * t)
        elif color_scheme == "pastel":
            r, g, b = int(200 + 55 * t), int(200 + 55 * (1 - t)), int(220 + 35 * t)
        else:  # realistic / default — blue sky
            r, g, b = int(135 + 120 * t), int(206 + 49 * t), int(235 + 20 * t)
        return min(255, r), min(255, g), min(255, b)

    for y in range(height):
        draw.line([(0, y), (width, y)], fill=_gradient(y))

    n = 5 if complexity == "simple" else (10 if complexity == "medium" else 15)
    for _ in range(n):
        x1, y1 = random.randint(0, width), random.randint(0, height)
        size = random.randint(5, 100)
        x2, y2 = min(x1 + size, width), min(y1 + size, height)
        if style == "pixel":
            color = (random.randint(0, 255), random.randint(0, 255), random.randint(0, 255))
            draw.rectangle([x1, y1, x2, y2], fill=color)
        elif style == "sketch":
            v = random.randint(100, 255)
            xo, yo = random.randint(-10, 10), random.randint(-10, 10)
            draw.line([(x1, y1), (x2 + xo, y2 + yo)], fill=(v, v, v), width=2)
        else:
            color = (random.randint(100, 255), random.randint(100, 255), random.randint(100, 255))
            draw.ellipse([x1, y1, x2, y2], fill=color)

    # Title text at the bottom
    try:
        font = PIL.ImageFont.truetype("arial.ttf", 16)
    except Exception:
        font = PIL.ImageFont.load_default()
    words, lines, current = prompt.split(), [], []
    for word in words:
        if draw.textbbox((0, 0), " ".join(current + [word]), font=font)[2] <= width - 20:
            current.append(word)
        else:
            if current:
                lines.append(" ".join(current))
            current = [word]
    if current:
        lines.append(" ".join(current))

    y_pos = height - 22 * len(lines) - 10
    for line in lines:
        bw = draw.textbbox((0, 0), line, font=font)
        draw.text(((width - bw[2]) // 2, y_pos), line, fill="white", font=font)
        y_pos += 22

    import io
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue(), "image/png"


# ── Public entry point ─────────────────────────────────────────────────────────

def generate_image(parameters: dict | None) -> dict:
    """
    Generate an image from text. Tries Google Imagen first, falls back to PIL.

    Returns a dict with ``result`` (human-readable summary), ``path``,
    ``image_bytes`` (PNG/JPEG payload for the dashboard), ``mime``, ``ai``
    (True if Imagen was used) and ``prompt``.
    """
    params = parameters or {}

    prompt = str(params.get("prompt") or "a beautiful landscape").strip()
    if not prompt:
        prompt = "a beautiful landscape"

    style        = str(params.get("style") or "realistic").strip().lower()
    aspect_ratio = (params.get("aspect_ratio") or "").strip().lower()
    color_scheme = params.get("color_scheme")
    complexity   = str(params.get("complexity") or "medium").strip().lower()
    if style not in _STYLE_HINTS:
        style = "realistic"

    try:
        width  = int(params.get("width") or 0)
        height = int(params.get("height") or 0)
    except (TypeError, ValueError):
        width, height = 0, 0
    width, height = max(0, width), max(0, height)

    if aspect_ratio in _FALLBACK_SIZES and (not width or not height):
        width, height = _FALLBACK_SIZES[aspect_ratio]
    if not width or not height:
        width, height = 512, 512

    # 1) Real AI generation (Imagen sizes output via aspect ratio, not px)
    used_ai = False
    try:
        data, mime = _generate_with_imagen(prompt, style, aspect_ratio, color_scheme)
        used_ai = True
    except Exception as e:
        _log(f"[ImageGen] Imagen unavailable ({e}) - using PIL fallback")

    # 2) Offline fallback
    if not used_ai:
        try:
            data, mime = _fallback_with_pil(
                prompt, style, width, height, color_scheme, complexity,
            )
        except ImportError:
            return {
                "result": (
                    "Image generation requires Pillow and an active internet "
                    "connection. Run: pip install Pillow"
                ),
                "path": "", "image_bytes": b"", "mime": "", "ai": False,
                "prompt": prompt,
            }
        except Exception as e:
            return {
                "result": f"Image generation failed: {e}",
                "path": "", "image_bytes": b"", "mime": "", "ai": False,
                "prompt": prompt,
            }

    path = _save_bytes(data, mime)

    engine = "AI (Imagen)" if used_ai else "offline fallback"
    result = (
        f"Image generated with {engine} and saved to: {path}\n"
        f"Prompt: {prompt}\nStyle: {style}"
    )
    if aspect_ratio:
        result += f"\nAspect ratio: {aspect_ratio}"
    if color_scheme:
        result += f"\nColor scheme: {color_scheme}"

    return {
        "result": result,
        "path": str(path),
        "image_bytes": data,
        "mime": mime,
        "ai": used_ai,
        "prompt": prompt,
    }
