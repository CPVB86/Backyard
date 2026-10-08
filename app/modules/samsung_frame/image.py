"""Deterministic geometric test composition with a local timestamp."""
from datetime import datetime
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont


def generate(path: Path):
    image = Image.new("RGB", (3840, 2160), "#102c3a")
    draw = ImageDraw.Draw(image)
    draw.ellipse((2400, 160, 3300, 1060), fill="#f7c96b")
    draw.polygon([(0, 1800), (900, 600), (2100, 2160), (0, 2160)], fill="#418b82")
    draw.polygon([(1300, 2160), (2600, 950), (3840, 1750), (3840, 2160)], fill="#ba6955")
    draw.rectangle((0, 0, 3839, 2159), outline="#f7c96b", width=12)
    draw.line((0, 1080, 3840, 1080), fill="white", width=3)
    draw.line((1920, 0, 1920, 2160), fill="white", width=3)
    draw.text((160, 170), "BACKYARD / SAMSUNG FRAME", font=ImageFont.load_default(size=90), fill="white")
    draw.text((160, 310), "3840 x 2160 / matte = none", font=ImageFont.load_default(size=56), fill="white")
    draw.text((3760, 2080), datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %z"),
              anchor="rs", font=ImageFont.load_default(size=36), fill="white")
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, format="PNG")
    return path
