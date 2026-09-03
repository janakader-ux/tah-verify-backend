#!/usr/bin/env python3
"""Generates geo-tagged GBP visual assets for local SEO map ranking."""
import os
from PIL import Image, ImageDraw, ImageFont

HUBS = [
    {"city": "London", "lat": 51.5074, "lon": -0.1278, "text": "London Director IDV Hub\n100% Digital Personal Code"},
    {"city": "Birmingham", "lat": 52.4862, "lon": -1.8904, "text": "Birmingham Director IDV Hub\nFast GPG45 Verification"},
    {"city": "Manchester", "lat": 53.4808, "lon": -2.2426, "text": "Manchester Director IDV Hub\nSame-Day Identity Check"}
]

os.makedirs("gbp_assets", exist_ok=True)

for hub in HUBS:
    img = Image.new("RGB", (1200, 1200), color=(15, 23, 42)) # Dark blue slate background
    draw = ImageDraw.Draw(img)
    
    # Draw simple visual card structure
    draw.rectangle([60, 60, 1140, 1140], outline=(59, 130, 246), width=8)
    draw.text((100, 150), "DIRECTORPERSONALCODE.UK", fill=(148, 163, 184))
    draw.text((100, 450), hub["text"], fill=(255, 255, 255))
    draw.text((100, 950), f"Geo-Target: {hub['city']} ({hub['lat']}, {hub['lon']})", fill=(59, 130, 246))
    
    filename = f"gbp_assets/{hub['city'].lower()}_idv_hub.png"
    img.save(filename)
    print(f"Generated asset: {filename}")
