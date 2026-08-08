#!/usr/bin/env python3
"""
Generate images with all available styles and customization options.
This script demonstrates the new image generation features.
"""

import time
from pathlib import Path

try:
    import PIL.Image
    import PIL.ImageDraw
    import PIL.ImageFont
    import random
    print("[OK] PIL (Pillow) is available")
except ImportError:
    print("[ERROR] PIL (Pillow) is not installed. Run: pip install Pillow")
    exit(1)


def generate_image(
    prompt: str,
    style: str = "realistic",
    width: int = 512,
    height: int = 512,
    aspect_ratio: str = None,
    color_scheme: str = None,
    complexity: str = "medium"
):
    """Generate an image with the specified parameters."""
    
    # Handle aspect ratio presets
    if aspect_ratio:
        if aspect_ratio == "portrait":
            width, height = 512, 768
        elif aspect_ratio == "landscape":
            width, height = 768, 512
        elif aspect_ratio == "wide":
            width, height = 1024, 576
        elif aspect_ratio == "cinematic":
            width, height = 1024, 576
        else:  # square
            width, height = 512, 512
    
    # Color scheme defaults based on style
    if not color_scheme:
        if style in ["neon", "cyberpunk"]:
            color_scheme = "vibrant"
        elif style in ["watercolor", "sketch"]:
            color_scheme = "muted"
        elif style in ["dark", "cyberpunk"]:
            color_scheme = "dark"
        else:
            color_scheme = "auto"
    
    print(f"  [GENERATING] Style='{style}', size={width}x{height}, color_scheme={color_scheme}")
    
    # Create image with gradient background
    img = PIL.Image.new('RGB', (width, height))
    draw = PIL.ImageDraw.Draw(img)
    
    # Generate gradient based on style and color scheme
    def generate_gradient(y, height, style, color_scheme):
        t = y / height  # normalized position (0 to 1)
        
        if style == "artistic" or color_scheme == "vibrant":
            r = int(255 * (1 - t))
            g = int(100 * t)
            b = int(255 * t)
        elif style == "cartoon" or color_scheme == "bright":
            r = int(100 + 155 * t)
            g = int(200 * (1 - t))
            b = int(100 + 100 * t)
        elif style == "watercolor" or color_scheme == "muted":
            r = int(180 + 75 * t)
            g = int(150 + 100 * (1 - t))
            b = int(200 + 55 * t)
        elif style == "sketch" or color_scheme == "monochrome":
            val = int(200 + 55 * t)
            r, g, b = val, val, val
        elif style in ["dark", "cyberpunk"] or color_scheme == "dark":
            r = int(20 + 30 * t)
            g = int(10 + 20 * t)
            b = int(40 + 60 * t)
        elif style == "neon" or color_scheme == "vibrant":
            r = int(50 + 200 * t)
            g = int(255 * (1 - t))
            b = int(100 + 155 * t)
        elif style == "retro" or style == "vintage":
            r = int(180 + 75 * t)
            g = int(120 + 80 * (1 - t))
            b = int(80 + 70 * t)
        elif color_scheme == "warm":
            r = int(255 * (1 - t * 0.3))
            g = int(150 + 50 * t)
            b = int(100 + 50 * t)
        elif color_scheme == "cool":
            r = int(100 + 50 * t)
            g = int(150 + 100 * (1 - t))
            b = int(200 + 55 * t)
        elif color_scheme == "pastel":
            r = int(200 + 55 * t)
            g = int(200 + 55 * (1 - t))
            b = int(220 + 35 * t)
        else:  # realistic or default
            r = int(135 + 120 * t)
            g = int(206 + 49 * t)
            b = int(235 + 20 * t)
        
        return (min(255, r), min(255, g), min(255, b))
    
    # Apply gradient
    for y in range(height):
        color = generate_gradient(y, height, style, color_scheme)
        draw.line([(0, y), (width, y)], fill=color)
    
    # Add decorative elements based on complexity
    num_elements = 5 if complexity == "simple" else (10 if complexity == "medium" else 15)
    for _ in range(num_elements):
        x1 = random.randint(0, width)
        y1 = random.randint(0, height)
        size = random.randint(20, 80) if complexity == "simple" else (random.randint(10, 100) if complexity == "medium" else random.randint(5, 120))
        x2 = min(x1 + size, width)
        y2 = min(y1 + size, height)
        
        # Color varies by style
        if style in ["neon", "cyberpunk"]:
            color = (random.randint(0, 255), random.randint(200, 255), random.randint(0, 255))
        elif style in ["watercolor", "pastel"]:
            color = (random.randint(150, 255), random.randint(150, 255), random.randint(150, 255))
        elif style in ["sketch", "monochrome"]:
            val = random.randint(100, 255)
            color = (val, val, val)
        else:
            color = (random.randint(100, 255), random.randint(100, 255), random.randint(100, 255))
        
        if style == "pixel":
            draw.rectangle([x1, y1, x2, y2], fill=color)
        elif style == "sketch":
            for i in range(3):
                x_offset = random.randint(-10, 10)
                y_offset = random.randint(-10, 10)
                draw.line([(x1, y1), (x2 + x_offset, y2 + y_offset)], fill=color, width=2)
        else:
            draw.ellipse([x1, y1, x2, y2], fill=color, outline=None)
    
    # Add prompt text
    font_size = 14 if complexity == "simple" else (16 if complexity == "medium" else 18)
    try:
        font = PIL.ImageFont.truetype("arial.ttf", font_size)
    except:
        font = PIL.ImageFont.load_default()
    
    # Wrap text
    words = prompt.split()
    lines = []
    current_line = []
    for word in words:
        test_line = ' '.join(current_line + [word])
        bbox = draw.textbbox((0, 0), test_line, font=font)
        if bbox[2] <= width - 20:
            current_line.append(word)
        else:
            if current_line:
                lines.append(' '.join(current_line))
            current_line = [word]
    if current_line:
        lines.append(' '.join(current_line))
    
    # Draw text at bottom
    y_pos = height - (font_size + 4) * len(lines) - 10
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        text_width = bbox[2] - bbox[0]
        x_pos = (width - text_width) // 2
        
        # Text color varies by style
        if style in ["neon", "cyberpunk"]:
            text_color = (0, 255, 255)  # Cyan
        elif style in ["dark", "cyberpunk"]:
            text_color = (200, 200, 200)  # Light gray
        elif style in ["watercolor", "pastel"]:
            text_color = (100, 100, 100)  # Dark gray
        else:
            text_color = "white"
        
        draw.text((x_pos, y_pos), line, fill=text_color, font=font)
        y_pos += font_size + 4
    
    # Save image
    image_dir = Path.home() / "Pictures" / "Jarvis"
    image_dir.mkdir(parents=True, exist_ok=True)
    image_path = image_dir / f"generated_{style}_{int(time.time())}.png"
    img.save(image_path, "PNG")
    
    return image_path


def main():
    """Generate images with all available styles."""
    print("[START] Generating images with all available styles...\n")
    
    # Define all styles with sample prompts
    styles = [
        ("realistic", "A beautiful sunset over mountains"),
        ("artistic", "Abstract composition with flowing colors"),
        ("cartoon", "A cute cartoon character"),
        ("pixel", "Retro video game scene"),
        ("watercolor", "Flowers in a garden"),
        ("sketch", "Portrait of a person"),
        ("abstract", "Geometric patterns"),
        ("retro", "Vintage advertisement"),
        ("neon", "Futuristic city at night"),
        ("cyberpunk", "Cyberpunk street scene"),
        ("minimalist", "Simple geometric shapes"),
        ("vintage", "Old photograph style"),
        ("fantasy", "Magical forest with glowing lights"),
        ("anime", "Anime character"),
        ("oil_painting", "Classical landscape painting"),
    ]
    
    # Generate images for each style
    for style, prompt in styles:
        print(f"[STYLE] {style}: {prompt}")
        path = generate_image(
            prompt=prompt,
            style=style,
            complexity="medium"
        )
        print(f"  [SAVED] {path}\n")
        time.sleep(0.1)  # Small delay to ensure unique filenames
    
    # Generate images with different color schemes
    print("[COLOR SCHEMES] Testing different color schemes...\n")
    color_schemes = ["warm", "cool", "vibrant", "muted", "monochrome", "pastel", "dark", "bright"]
    
    for color_scheme in color_schemes:
        print(f"[COLOR] {color_scheme}")
        path = generate_image(
            prompt=f"Test image with {color_scheme} color scheme",
            style="realistic",
            color_scheme=color_scheme,
            complexity="simple"
        )
        print(f"  [SAVED] {path}\n")
        time.sleep(0.1)
    
    # Generate images with different aspect ratios
    print("[ASPECT RATIOS] Testing different aspect ratios...\n")
    aspect_ratios = ["square", "portrait", "landscape", "wide", "cinematic"]
    
    for aspect_ratio in aspect_ratios:
        print(f"[ASPECT] {aspect_ratio}")
        path = generate_image(
            prompt=f"Test image with {aspect_ratio} aspect ratio",
            style="realistic",
            aspect_ratio=aspect_ratio,
            complexity="simple"
        )
        print(f"  [SAVED] {path}\n")
        time.sleep(0.1)
    
    print("[SUCCESS] All images generated successfully!")
    print(f"[INFO] Check the gallery in ~/Pictures/Jarvis/ to see all images")
    print(f"[INFO] Or open the JARVIS Dashboard and click the GALLERY button")
    print(f"[INFO] Total images generated: {len(styles) + len(color_schemes) + len(aspect_ratios)}")


if __name__ == "__main__":
    main()
