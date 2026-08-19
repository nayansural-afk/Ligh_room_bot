"""
Light Room bot — automatic version
Each run: generates a few original Lightroom presets (XMP) with AI, builds a real
XMP file, writes an English caption, and posts them to the Telegram channel.

Runs via GitHub Actions on a daily schedule. Reads secrets from environment
variables instead of hardcoding them:
  TELEGRAM_TOKEN, GROQ_API_KEY, CHANNEL_ID (optional, defaults to @LightRooms)
"""

import os
import requests
import random
import uuid as uuid_lib
import asyncio
from telegram import Bot
from PIL import Image, ImageEnhance

# ---------- settings ----------
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
GROQ_API_KEY = os.environ["GROQ_API_KEY"]
CHANNEL_ID = os.environ.get("CHANNEL_ID", "@LightRooms")

POSTS_PER_RUN = 2  # how many posts each run publishes
SAMPLE_PHOTO = "IMG_1042.jpeg"  # the real photo the preset preview gets applied to

# base style seeds — the model expands one of these each run so presets stay varied
STYLE_SEEDS = [
    "warm cinematic with high contrast",
    "bright minimal with neutral tones",
    "dark street style with subtle green tint",
    "soft pastel tones for portraits",
    "golden hour sunset with rich saturation",
    "dramatic black and white with heavy contrast",
    "analog film look with controlled grain and fade",
    "lush green nature tones for landscapes",
]

XMP_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="XMP Core 5.4.0">
 <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
  <rdf:Description rdf:about=""
    xmlns:crs="http://ns.adobe.com/camera-raw-settings/1.0/"
   crs:Version="15.0"
   crs:ProcessVersion="11.0"
   crs:Temperature="{temperature}"
   crs:Tint="{tint}"
   crs:Exposure2012="{exposure}"
   crs:Contrast2012="{contrast}"
   crs:Highlights2012="{highlights}"
   crs:Shadows2012="{shadows}"
   crs:Whites2012="{whites}"
   crs:Blacks2012="{blacks}"
   crs:Clarity2012="{clarity}"
   crs:Vibrance="{vibrance}"
   crs:Saturation="{saturation}"
   crs:PresetType="Normal"
   crs:HasSettings="True"
   crs:UUID="{preset_uuid}"
  >
   <crs:Name>
    <rdf:Alt>
     <rdf:li xml:lang="x-default">{name}</rdf:li>
    </rdf:Alt>
   </crs:Name>
  </rdf:Description>
 </rdf:RDF>
</x:xmpmeta>
"""


def apply_preset_preview(params, output_path):
    """Apply the preset's real numeric values to SAMPLE_PHOTO so the preview
    actually reflects the preset, instead of being an AI-imagined picture."""
    img = Image.open(SAMPLE_PHOTO).convert("RGB")

    # exposure (-1.0 .. 1.0) -> stronger brightness shift
    brightness_factor = 1.0 + (params["exposure"] * 0.9)
    img = ImageEnhance.Brightness(img).enhance(max(brightness_factor, 0.1))

    # contrast (-40 .. 60) -> stronger swing
    contrast_factor = 1.0 + (params["contrast"] / 45)
    img = ImageEnhance.Contrast(img).enhance(max(contrast_factor, 0.1))

    # saturation + vibrance combined (-20 .. 30 and -20 .. 50) -> stronger color shift
    color_push = (params["saturation"] + params["vibrance"]) / 60
    saturation_factor = 1.0 + color_push
    img = ImageEnhance.Color(img).enhance(max(saturation_factor, 0.0))

    # clarity (-20 .. 40) -> extra local contrast/sharpness punch
    clarity_factor = 1.0 + (params["clarity"] / 60)
    img = ImageEnhance.Sharpness(img).enhance(max(clarity_factor, 0.0))

    # temperature (-50 .. 50) -> much stronger warm/cool color cast
    r, g, b = img.split()
    temp_shift = params["temperature"] / 50  # -1.0 .. 1.0
    tint_shift = params["tint"] / 50
    if temp_shift != 0 or tint_shift != 0:
        r = r.point(lambda p: min(255, max(0, int(p + 255 * temp_shift * 0.35))))
        b = b.point(lambda p: min(255, max(0, int(p - 255 * temp_shift * 0.35))))
        g = g.point(lambda p: min(255, max(0, int(p + 255 * tint_shift * 0.2))))
    img = Image.merge("RGB", (r, g, b))

    img.save(output_path, quality=90)
    return output_path


def groq_generate(prompt, json_mode=False):
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
    }
    data = {
        "model": "openai/gpt-oss-20b",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.9,
        "max_tokens": 800,
    }
    if json_mode:
        data["response_format"] = {"type": "json_object"}

    response = requests.post(url, headers=headers, json=data, timeout=60)
    result = response.json()
    if "choices" not in result:
        raise Exception(f"پاسخ نامعتبر Groq: {result}")
    return result["choices"][0]["message"]["content"]


def generate_preset_params(style):
    """Ask the AI for a realistic, coherent set of preset parameters (raw JSON)."""
    prompt = f"""
    Design an original Adobe Lightroom preset for this style:
    Style: {style}

    Return ONLY a JSON object with exactly these keys (no extra text):
    {{
      "name": "short creative English preset name (no long phrases)",
      "temperature": number between -50 and 50,
      "tint": number between -30 and 30,
      "exposure": decimal number between -1.0 and 1.0,
      "contrast": number between -40 and 60,
      "highlights": number between -80 and 40,
      "shadows": number between -40 and 80,
      "whites": number between -30 and 40,
      "blacks": number between -40 and 30,
      "clarity": number between -20 and 40,
      "vibrance": number between -20 and 50,
      "saturation": number between -20 and 30
    }}
    """
    import json
    raw = groq_generate(prompt, json_mode=True)
    return json.loads(raw)


def build_xmp(params):
    return XMP_TEMPLATE.format(
        temperature=params["temperature"],
        tint=params["tint"],
        exposure=params["exposure"],
        contrast=params["contrast"],
        highlights=params["highlights"],
        shadows=params["shadows"],
        whites=params["whites"],
        blacks=params["blacks"],
        clarity=params["clarity"],
        vibrance=params["vibrance"],
        saturation=params["saturation"],
        preset_uuid=str(uuid_lib.uuid4()).upper(),
        name=params["name"],
    )


def generate_caption(style, preset_name):
    prompt = f"""
    Write an engaging English caption for the Telegram channel "Light Room"
    introducing a new preset called "{preset_name}" in the style: {style}.

    Include:
    - An eye-catching title with an emoji
    - A short description of the mood and best use case for this preset
    - Relevant hashtags about photo editing and Lightroom
    - Friendly, concise tone (max 6-7 lines)
    """
    return groq_generate(prompt)


async def publish_one(bot: Bot, style: str, index: int):
    params = generate_preset_params(style)
    xmp_content = build_xmp(params)
    caption = generate_caption(style, params["name"])

    base_name = params["name"].replace(" ", "_")
    xmp_filename = f"{base_name}.xmp"
    preview_filename = f"{base_name}_preview.jpg"

    with open(xmp_filename, "w", encoding="utf-8") as f:
        f.write(xmp_content)

    apply_preset_preview(params, preview_filename)

    with open(preview_filename, "rb") as f:
        await bot.send_photo(chat_id=CHANNEL_ID, photo=f, caption=caption)

    with open(xmp_filename, "rb") as f:
        await bot.send_document(chat_id=CHANNEL_ID, document=f, filename=xmp_filename)

    print(f"✅ Post {index+1} published: {params['name']}")


async def main():
    bot = Bot(token=TELEGRAM_TOKEN)
    styles = random.sample(STYLE_SEEDS, k=min(POSTS_PER_RUN, len(STYLE_SEEDS)))
    for i, style in enumerate(styles):
        try:
            await publish_one(bot, style, i)
        except Exception as e:
            print(f"❌ خطا در پست {i+1}: {e}")


if __name__ == "__main__":
    asyncio.run(main())
