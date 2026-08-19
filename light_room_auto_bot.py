"""
Light Room bot — AI Content Manager

Multi-format content for @LightRooms:
  - Single-image Before | After collage (one post)
  - Free XMP preset file
  - Four content pillars + smart style selection
  - content_history.json with message_ids + member count

Secrets (env):
  TELEGRAM_TOKEN, GROQ_API_KEY
  CHANNEL_ID (optional, default @LightRooms)
"""

import os
import json
import random
import uuid as uuid_lib
import asyncio
from datetime import datetime, timezone
from pathlib import Path

import requests
from telegram import Bot
from PIL import Image, ImageEnhance, ImageDraw, ImageFont

# ---------- settings ----------
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
GROQ_API_KEY = os.environ["GROQ_API_KEY"]
CHANNEL_ID = os.environ.get("CHANNEL_ID", "@LightRooms")

POSTS_PER_RUN = 1  # one strong post per run (daily schedule)
SAMPLE_PHOTO = "IMG_1042.jpeg"
HISTORY_FILE = Path("content_history.json")

# Content pillars (weights sum to 100)
CONTENT_PILLARS = {
    "before_after": 40,
    "preset_of_the_day": 25,
    "quick_tip": 20,
    "experimental_trend": 15,
}

STYLE_SEEDS = [
    "warm cinematic with high contrast",
    "bright minimal with neutral tones",
    "dark street style with subtle green tint",
    "soft pastel tones for portraits",
    "golden hour sunset with rich saturation",
    "dramatic black and white with heavy contrast",
    "analog film look with controlled grain and fade",
    "lush green nature tones for landscapes",
    "cool blue hour cityscape",
    "high-key fashion with soft contrast",
    "moody forest with teal shadows",
    "vintage polaroid faded look",
]

XMP_TEMPLATE = """<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<x:xmpmeta xmlns:x=\"adobe:ns:meta/\" x:xmptk=\"XMP Core 5.4.0\">
 <rdf:RDF xmlns:rdf=\"http://www.w3.org/1999/02/22-rdf-syntax-ns#\">
  <rdf:Description rdf:about=\"\"
    xmlns:crs=\"http://ns.adobe.com/camera-raw-settings/1.0/\"
   crs:Version=\"15.0\"
   crs:ProcessVersion=\"11.0\"
   crs:Temperature=\"{temperature}\"
   crs:Tint=\"{tint}\"
   crs:Exposure2012=\"{exposure}\"
   crs:Contrast2012=\"{contrast}\"
   crs:Highlights2012=\"{highlights}\"
   crs:Shadows2012=\"{shadows}\"
   crs:Whites2012=\"{whites}\"
   crs:Blacks2012=\"{blacks}\"
   crs:Clarity2012=\"{clarity}\"
   crs:Vibrance=\"{vibrance}\"
   crs:Saturation=\"{saturation}\"
   crs:PresetType=\"Normal\"
   crs:HasSettings=\"True\"
   crs:UUID=\"{preset_uuid}\"
  >
   <crs:Name>
    <rdf:Alt>
     <rdf:li xml:lang=\"x-default\">{name}</rdf:li>
    </rdf:Alt>
   </crs:Name>
  </rdf:Description>
 </rdf:RDF>
</x:xmpmeta>
"""


# ---------- history ----------
def load_history():
    if HISTORY_FILE.exists():
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "posts": [],
        "style_counts": {},
        "pillar_counts": {},
        "analytics_snapshots": [],
    }


def save_history(history):
    history["posts"] = history.get("posts", [])[-200:]
    history["analytics_snapshots"] = history.get("analytics_snapshots", [])[-60:]
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def record_post(history, entry):
    history.setdefault("posts", []).append(entry)
    style = entry.get("style", "")
    pillar = entry.get("pillar", "")
    history.setdefault("style_counts", {})[style] = (
        history["style_counts"].get(style, 0) + 1
    )
    history.setdefault("pillar_counts", {})[pillar] = (
        history["pillar_counts"].get(pillar, 0) + 1
    )
    save_history(history)


# ---------- selection ----------
def choose_pillar(history):
    recent = history.get("posts", [])[-20:]
    recent_counts = {}
    for p in recent:
        recent_counts[p.get("pillar")] = recent_counts.get(p.get("pillar"), 0) + 1

    weights = []
    pillars = list(CONTENT_PILLARS.keys())
    for pillar in pillars:
        base = CONTENT_PILLARS[pillar]
        penalty = recent_counts.get(pillar, 0) * 3
        weights.append(max(base - penalty, 5))
    return random.choices(pillars, weights=weights, k=1)[0]


def choose_style(history, exploration_rate=0.25):
    if random.random() < exploration_rate:
        return random.choice(STYLE_SEEDS)

    counts = history.get("style_counts", {})
    max_c = max(counts.values()) if counts else 0
    weights = []
    for s in STYLE_SEEDS:
        c = counts.get(s, 0)
        weights.append((max_c - c) + 2)
    return random.choices(STYLE_SEEDS, weights=weights, k=1)[0]


# ---------- Groq ----------
def groq_generate(prompt, json_mode=False, max_tokens=900):
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
    }
    data = {
        "model": "openai/gpt-oss-20b",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.85,
        "max_tokens": max_tokens,
    }
    if json_mode:
        data["response_format"] = {"type": "json_object"}

    response = requests.post(url, headers=headers, json=data, timeout=60)
    result = response.json()
    if "choices" not in result:
        raise Exception(f"Invalid Groq response: {result}")
    return result["choices"][0]["message"]["content"]


def generate_preset_params(style):
    prompt = f"""
Design an original Adobe Lightroom preset for this style:
Style: {style}

Return ONLY a JSON object with exactly these keys (no extra text):
{{
  "name": "short creative English preset name (2-4 words)",
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
    raw = groq_generate(prompt, json_mode=True)
    return json.loads(raw)


def generate_hook_and_caption(pillar, style, preset_name, tip_text=None):
    pillar_instructions = {
        "before_after": (
            "This is a Before & After post. Hook should create curiosity about the transformation. "
            "Caption must clearly say Before → After and invite people to try the free XMP."
        ),
        "preset_of_the_day": (
            "This is Preset of the Day. Hook should feel like a daily gift. "
            "Highlight the mood and best use cases."
        ),
        "quick_tip": (
            "This is a Quick Editing Tip post. Hook should promise a useful shortcut. "
            "Caption focuses on the tip; the preset is a bonus example."
        ),
        "experimental_trend": (
            "This is an Experimental / Trend post. Hook should feel bold and current. "
            "Mention trying something fresh or slightly unconventional."
        ),
    }

    tip_block = f"\nEditing tip to weave in: {tip_text}" if tip_text else ""

    prompt = f"""
You write for the Telegram channel "Light Rooms" (photo editing / Lightroom presets).

Content type: {pillar}
Style: {style}
Preset name: {preset_name}
{tip_block}

Instructions for this type:
{pillar_instructions.get(pillar, "")}

Write:
1) A single-line HOOK (max 12 words, punchy, with 1 emoji if natural)
2) A full CAPTION (English, friendly, max 8 lines) that includes:
   - The hook as the first line
   - Short mood / use-case description
   - Clear call to download the free .xmp (next message)
   - 4–6 relevant hashtags

Return ONLY valid JSON:
{{
  "hook": "...",
  "caption": "..."
}}
"""
    raw = groq_generate(prompt, json_mode=True)
    return json.loads(raw)


def generate_quick_tip(style):
    prompt = f"""
Give one practical, specific Adobe Lightroom editing tip related to this style: {style}.
One or two short sentences only. No intro. Return plain text.
"""
    return groq_generate(prompt, max_tokens=120).strip()


# ---------- image helpers ----------
def apply_preset_preview(params, output_path):
    """Apply numeric preset values to SAMPLE_PHOTO for a real preview."""
    img = Image.open(SAMPLE_PHOTO).convert("RGB")

    brightness_factor = 1.0 + (params["exposure"] * 0.9)
    img = ImageEnhance.Brightness(img).enhance(max(brightness_factor, 0.1))

    contrast_factor = 1.0 + (params["contrast"] / 45)
    img = ImageEnhance.Contrast(img).enhance(max(contrast_factor, 0.1))

    color_push = (params["saturation"] + params["vibrance"]) / 60
    saturation_factor = 1.0 + color_push
    img = ImageEnhance.Color(img).enhance(max(saturation_factor, 0.0))

    clarity_factor = 1.0 + (params["clarity"] / 60)
    img = ImageEnhance.Sharpness(img).enhance(max(clarity_factor, 0.0))

    r, g, b = img.split()
    temp_shift = params["temperature"] / 50
    tint_shift = params["tint"] / 50
    if temp_shift != 0 or tint_shift != 0:
        r = r.point(lambda p: min(255, max(0, int(p + 255 * temp_shift * 0.35))))
        b = b.point(lambda p: min(255, max(0, int(p - 255 * temp_shift * 0.35))))
        g = g.point(lambda p: min(255, max(0, int(p + 255 * tint_shift * 0.2))))
    img = Image.merge("RGB", (r, g, b))

    img.save(output_path, quality=90)
    return output_path


def _add_label(img, label):
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 42
        )
    except Exception:
        font = ImageFont.load_default()
    bbox = draw.textbbox((0, 0), label, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    pad = 14
    draw.rectangle([0, 0, tw + pad * 2, th + pad * 2], fill=(0, 0, 0))
    draw.text((pad, pad), label, fill=(255, 255, 255), font=font)
    return img


def make_before_after_collage(before_path, after_path, output_path):
    """One image: BEFORE | AFTER side by side — single Telegram post."""
    before = Image.open(before_path).convert("RGB")
    after = Image.open(after_path).convert("RGB")

    # match heights
    h = min(before.height, after.height)
    before = before.resize(
        (int(before.width * h / before.height), h), Image.Resampling.LANCZOS
    )
    after = after.resize(
        (int(after.width * h / after.height), h), Image.Resampling.LANCZOS
    )

    before = _add_label(before, "BEFORE")
    after = _add_label(after, "AFTER")

    gap = 8
    collage = Image.new("RGB", (before.width + after.width + gap, h), (20, 20, 20))
    collage.paste(before, (0, 0))
    collage.paste(after, (before.width + gap, 0))
    collage.save(output_path, quality=92)
    return output_path


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


# ---------- real analytics (Bot API limits) ----------
async def snapshot_channel_stats(bot: Bot, history: dict):
    """
    Real metrics available via Bot API (no fake views):
      - member_count (subscribers)
    Views per post are NOT available to bots; only the channel owner
    sees them in Telegram's built-in Analytics.
    """
    try:
        count = await bot.get_chat_member_count(CHANNEL_ID)
    except Exception as e:
        print(f"⚠️ member_count failed: {e}")
        count = None

    snap = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "member_count": count,
        "total_posts_recorded": len(history.get("posts", [])),
        "pillar_counts": dict(history.get("pillar_counts", {})),
    }
    history.setdefault("analytics_snapshots", []).append(snap)
    save_history(history)
    print(f"📊 Analytics snapshot | members={count}")
    return snap


# ---------- publish ----------
async def publish_one(bot: Bot, history: dict, index: int):
    pillar = choose_pillar(history)
    style = choose_style(history)
    params = generate_preset_params(style)
    preset_name = params["name"]

    tip_text = None
    if pillar == "quick_tip":
        tip_text = generate_quick_tip(style)

    text_data = generate_hook_and_caption(pillar, style, preset_name, tip_text)
    caption = text_data.get("caption") or text_data.get("hook", preset_name)

    base_name = preset_name.replace(" ", "_")
    xmp_filename = f"{base_name}.xmp"
    after_raw = f"{base_name}_after_raw.jpg"
    collage_filename = f"{base_name}_ba.jpg"

    with open(xmp_filename, "w", encoding="utf-8") as f:
        f.write(build_xmp(params))

    apply_preset_preview(params, after_raw)
    make_before_after_collage(SAMPLE_PHOTO, after_raw, collage_filename)

    # ONE photo post: Before | After collage + caption
    with open(collage_filename, "rb") as f:
        photo_msg = await bot.send_photo(
            chat_id=CHANNEL_ID, photo=f, caption=caption
        )

    # XMP as follow-up document
    with open(xmp_filename, "rb") as f:
        doc_msg = await bot.send_document(
            chat_id=CHANNEL_ID, document=f, filename=xmp_filename
        )

    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "pillar": pillar,
        "style": style,
        "preset_name": preset_name,
        "status": "published",
        "has_before_after": True,
        "format": "single_collage",
        "tip": tip_text,
        "photo_message_id": photo_msg.message_id,
        "document_message_id": doc_msg.message_id,
    }
    record_post(history, entry)
    print(f"✅ Post {index + 1} | {pillar} | {preset_name} | msg={photo_msg.message_id}")


async def main():
    bot = Bot(token=TELEGRAM_TOKEN)
    history = load_history()

    for i in range(POSTS_PER_RUN):
        try:
            await publish_one(bot, history, i)
        except Exception as e:
            print(f"❌ Error on post {i + 1}: {e}")

    # Real analytics snapshot after each run
    try:
        await snapshot_channel_stats(bot, history)
    except Exception as e:
        print(f"⚠️ analytics: {e}")


if __name__ == "__main__":
    asyncio.run(main())
