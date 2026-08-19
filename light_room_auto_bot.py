"""
Light Room bot — Full Content Manager

Content types (3 posts/day via scheduled runs):
  - Lightroom presets (Before|After collage + free XMP)
  - Ready-to-copy AI image prompts (AiFreeRoPrompt style)
  - Real AI news from RSS feeds (rewritten short)

Secrets: TELEGRAM_TOKEN, GROQ_API_KEY
"""

import os
import json
import re
import random
import uuid as uuid_lib
import asyncio
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from pathlib import Path
from email.utils import parsedate_to_datetime
from html import unescape

import requests
from telegram import Bot
from PIL import Image, ImageEnhance, ImageDraw, ImageFont

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
GROQ_API_KEY = os.environ["GROQ_API_KEY"]
CHANNEL_ID = os.environ.get("CHANNEL_ID", "@LightRooms")

POSTS_PER_RUN = 1
SAMPLE_PHOTO = "IMG_1042.jpeg"
HISTORY_FILE = Path("content_history.json")

CONTENT_WEIGHTS = {
    "lightroom": 40,
    "ai_image_prompt": 40,
    "ai_news": 20,
}

LIGHTROOM_PILLARS = {
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

PROMPT_THEMES = [
    "cinematic low-angle outdoor portrait with wildflowers and sky",
    "cozy winter close-up with oversized white faux-fur hood",
    "playful bubblegum bubble shot with crimson backdrop and flash",
    "half-face extreme close-up with hard flash and film grain",
    "luxury fashion editorial on sport motorcycle studio shot",
    "golden hour beach portrait soft rim light",
    "noir detective rainy street black and white",
    "cyberpunk neon city night portrait",
    "Studio Ghibli anime version of the person",
    "medieval knight armor photorealistic portrait",
    "samurai warrior traditional armor",
    "astronaut space suit helmet open",
    "1920s Hollywood glamour studio portrait",
    "modern high-fashion magazine cover",
    "tropical vacation candid smartphone photo",
    "rockstar stage performance dramatic lights",
    "ancient Egyptian pharaoh / queen gold and linen",
    "steampunk inventor workshop",
    "vampire gothic elegance candlelight",
    "sports athlete action sweat and intensity",
    "royal Victorian era formal portrait",
    "underwater mermaid / merman fantasy",
    "wild west cowboy desert sunset",
    "futuristic android soft neon",
    "oil painting classical museum portrait style",
    "80s retro film look candid",
    "black and white film noir detective",
    "Disney Pixar 3D stylized character",
    "fantasy mage glowing magic portrait",
    "street style urban fashion editorial",
]

RSS_FEEDS = [
    ("OpenAI", "https://openai.com/news/rss.xml"),
    ("Hugging Face", "https://huggingface.co/blog/feed.xml"),
    ("Google AI", "https://blog.google/technology/ai/rss/"),
    ("TechCrunch AI", "https://techcrunch.com/category/artificial-intelligence/feed/"),
    ("The Verge AI", "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml"),
    ("MIT Tech Review AI", "https://www.technologyreview.com/topic/artificial-intelligence/feed/"),
    ("NVIDIA Blog", "https://blogs.nvidia.com/blog/category/ai/feed/"),
]


def load_history():
    if HISTORY_FILE.exists():
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            data.setdefault("posts", [])
            data.setdefault("style_counts", {})
            data.setdefault("pillar_counts", {})
            data.setdefault("theme_counts", {})
            data.setdefault("analytics_snapshots", [])
            data.setdefault("used_news_links", [])
            data.setdefault("content_type_counts", {})
            return data
        except Exception:
            pass
    return {
        "posts": [],
        "style_counts": {},
        "pillar_counts": {},
        "theme_counts": {},
        "analytics_snapshots": [],
        "used_news_links": [],
        "content_type_counts": {},
    }


def save_history(history):
    history["posts"] = history.get("posts", [])[-300:]
    history["analytics_snapshots"] = history.get("analytics_snapshots", [])[-90:]
    history["used_news_links"] = history.get("used_news_links", [])[-200:]
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def record_post(history, entry):
    history.setdefault("posts", []).append(entry)
    ct = entry.get("content_type") or entry.get("pillar") or "unknown"
    history.setdefault("content_type_counts", {})
    history["content_type_counts"][ct] = history["content_type_counts"].get(ct, 0) + 1
    if entry.get("style"):
        history.setdefault("style_counts", {})
        history["style_counts"][entry["style"]] = history["style_counts"].get(entry["style"], 0) + 1
    if entry.get("pillar"):
        history.setdefault("pillar_counts", {})
        history["pillar_counts"][entry["pillar"]] = history["pillar_counts"].get(entry["pillar"], 0) + 1
    if entry.get("theme"):
        history.setdefault("theme_counts", {})
        history["theme_counts"][entry["theme"]] = history["theme_counts"].get(entry["theme"], 0) + 1
    if entry.get("link"):
        history.setdefault("used_news_links", []).append(entry["link"])
    save_history(history)


def choose_content_type(history):
    recent = history.get("posts", [])[-9:]
    recent_counts = {}
    for p in recent:
        ct = p.get("content_type") or ("lightroom" if p.get("pillar") else "unknown")
        recent_counts[ct] = recent_counts.get(ct, 0) + 1
    types = list(CONTENT_WEIGHTS.keys())
    weights = []
    for t in types:
        base = CONTENT_WEIGHTS[t]
        penalty = recent_counts.get(t, 0) * 8
        weights.append(max(base - penalty, 5))
    return random.choices(types, weights=weights, k=1)[0]


def groq_generate(prompt, json_mode=False, max_tokens=900, temperature=0.85):
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
    }
    data = {
        "model": "openai/gpt-oss-20b",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if json_mode:
        data["response_format"] = {"type": "json_object"}
    response = requests.post(url, headers=headers, json=data, timeout=90)
    result = response.json()
    if "choices" not in result:
        raise Exception(f"Invalid Groq response: {result}")
    return result["choices"][0]["message"]["content"].strip()


def _extract(tag, text):
    m = re.search(rf"<{tag}>\s*(.*?)\s*</{tag}>", text, re.DOTALL | re.IGNORECASE)
    return m.group(1).strip() if m else ""


def choose_pillar(history):
    recent = history.get("posts", [])[-20:]
    recent_counts = {}
    for p in recent:
        if p.get("pillar"):
            recent_counts[p["pillar"]] = recent_counts.get(p["pillar"], 0) + 1
    pillars = list(LIGHTROOM_PILLARS.keys())
    weights = []
    for pillar in pillars:
        base = LIGHTROOM_PILLARS[pillar]
        penalty = recent_counts.get(pillar, 0) * 3
        weights.append(max(base - penalty, 5))
    return random.choices(pillars, weights=weights, k=1)[0]


def choose_style(history, exploration_rate=0.25):
    if random.random() < exploration_rate:
        return random.choice(STYLE_SEEDS)
    counts = history.get("style_counts", {})
    max_c = max(counts.values()) if counts else 0
    weights = [(max_c - counts.get(s, 0)) + 2 for s in STYLE_SEEDS]
    return random.choices(STYLE_SEEDS, weights=weights, k=1)[0]


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
    raw = groq_generate(prompt, json_mode=True, max_tokens=400)
    return json.loads(raw)


def generate_hook_and_caption(pillar, style, preset_name, tip_text=None):
    pillar_instructions = {
        "before_after": "This is a Before & After post. Hook should create curiosity about the transformation. Caption must clearly say Before to After and invite people to try the free XMP.",
        "preset_of_the_day": "This is Preset of the Day. Hook should feel like a daily gift. Highlight the mood and best use cases.",
        "quick_tip": "This is a Quick Editing Tip post. Hook should promise a useful shortcut. Caption focuses on the tip; the preset is a bonus example.",
        "experimental_trend": "This is an Experimental / Trend post. Hook should feel bold and current. Mention trying something fresh or slightly unconventional.",
    }
    tip_block = f"\nEditing tip to weave in: {tip_text}" if tip_text else ""
    prompt = f"""
You write for the Telegram channel Light Rooms (photo editing / Lightroom presets).

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
   - 4-6 relevant hashtags

Return ONLY valid JSON:
{{
  "hook": "...",
  "caption": "..."
}}
"""
    raw = groq_generate(prompt, json_mode=True, max_tokens=500)
    return json.loads(raw)


def generate_quick_tip(style):
    prompt = f"""
Give one practical, specific Adobe Lightroom editing tip related to this style: {style}.
One or two short sentences only. No intro. Return plain text.
"""
    return groq_generate(prompt, max_tokens=120).strip()


def apply_preset_preview(params, output_path):
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
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 42)
    except Exception:
        font = ImageFont.load_default()
    bbox = draw.textbbox((0, 0), label, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    pad = 14
    draw.rectangle([0, 0, tw + pad * 2, th + pad * 2], fill=(0, 0, 0))
    draw.text((pad, pad), label, fill=(255, 255, 255), font=font)
    return img


def make_before_after_collage(before_path, after_path, output_path):
    before = Image.open(before_path).convert("RGB")
    after = Image.open(after_path).convert("RGB")
    h = min(before.height, after.height)
    before = before.resize((int(before.width * h / before.height), h), Image.Resampling.LANCZOS)
    after = after.resize((int(after.width * h / after.height), h), Image.Resampling.LANCZOS)
    before = _add_label(before, "BEFORE")
    after = _add_label(after, "AFTER")
    gap = 8
    collage = Image.new("RGB", (before.width + after.width + gap, h), (20, 20, 20))
    collage.paste(before, (0, 0))
    collage.paste(after, (before.width + gap, 0))
    collage.save(output_path, quality=92)
    return output_path


def build_xmp(params):
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="XMP Core 5.4.0">\n'
        ' <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">\n'
        '  <rdf:Description rdf:about=""\n'
        '    xmlns:crs="http://ns.adobe.com/camera-raw-settings/1.0/"\n'
        '   crs:Version="15.0"\n'
        '   crs:ProcessVersion="11.0"\n'
        f'   crs:Temperature="{params["temperature"]}"\n'
        f'   crs:Tint="{params["tint"]}"\n'
        f'   crs:Exposure2012="{params["exposure"]}"\n'
        f'   crs:Contrast2012="{params["contrast"]}"\n'
        f'   crs:Highlights2012="{params["highlights"]}"\n'
        f'   crs:Shadows2012="{params["shadows"]}"\n'
        f'   crs:Whites2012="{params["whites"]}"\n'
        f'   crs:Blacks2012="{params["blacks"]}"\n'
        f'   crs:Clarity2012="{params["clarity"]}"\n'
        f'   crs:Vibrance="{params["vibrance"]}"\n'
        f'   crs:Saturation="{params["saturation"]}"\n'
        '   crs:PresetType="Normal"\n'
        '   crs:HasSettings="True"\n'
        f'   crs:UUID="{str(uuid_lib.uuid4()).upper()}"\n'
        '  >\n'
        '   <crs:Name>\n'
        '    <rdf:Alt>\n'
        f'     <rdf:li xml:lang="x-default">{params["name"]}</rdf:li>\n'
        '    </rdf:Alt>\n'
        '   </crs:Name>\n'
        '  </rdf:Description>\n'
        ' </rdf:RDF>\n'
        '</x:xmpmeta>\n'
    )


async def publish_lightroom(bot, history, index):
    pillar = choose_pillar(history)
    style = choose_style(history)
    params = generate_preset_params(style)
    preset_name = params["name"]
    tip_text = None
    if pillar == "quick_tip":
        tip_text = generate_quick_tip(style)
    text_data = generate_hook_and_caption(pillar, style, preset_name, tip_text)
    caption = text_data.get("caption") or text_data.get("hook", preset_name)
    base_name = re.sub(r"[^\w\-]+", "_", preset_name).strip("_") or "preset"
    xmp_filename = f"{base_name}.xmp"
    after_raw = f"{base_name}_after_raw.jpg"
    collage_filename = f"{base_name}_ba.jpg"
    with open(xmp_filename, "w", encoding="utf-8") as f:
        f.write(build_xmp(params))
    apply_preset_preview(params, after_raw)
    make_before_after_collage(SAMPLE_PHOTO, after_raw, collage_filename)
    with open(collage_filename, "rb") as f:
        photo_msg = await bot.send_photo(chat_id=CHANNEL_ID, photo=f, caption=caption)
    with open(xmp_filename, "rb") as f:
        doc_msg = await bot.send_document(chat_id=CHANNEL_ID, document=f, filename=xmp_filename)
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "content_type": "lightroom",
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
    print(f"Post {index + 1} | lightroom/{pillar} | {preset_name} | msg={photo_msg.message_id}")


def choose_theme(history):
    counts = history.get("theme_counts") or {}
    max_c = max((counts.get(t, 0) for t in PROMPT_THEMES), default=0)
    weights = [(max_c - counts.get(t, 0)) + 2 for t in PROMPT_THEMES]
    return random.choices(PROMPT_THEMES, weights=weights, k=1)[0]


def generate_prompt_post(theme, recent_titles):
    avoid = ", ".join([t for t in recent_titles[-10:] if t]) or "none"
    system = f"""You write ready-to-copy AI image prompts for Telegram channels like AiFreeRoPrompt.

Theme / concept: {theme}
Do NOT reuse these recent titles: {avoid}

Rules for the prompt itself:
- English only
- Photorealistic or highly detailed
- Always start with or include: "Do not change the face. Keep exact facial features, identity, skin tone and proportions from the reference photo."
- Include lighting, camera/lens feel, composition, aspect ratio (prefer 9:16 or 3:4 or 4:5)
- 80–160 words
- Ready to paste into Midjourney, Flux, ChatGPT image, or similar with a selfie/reference photo
- No hashtags inside the prompt text

Reply with EXACTLY this XML format and nothing else:

<title>short catchy title with 1 emoji</title>
<ai_prompt>
the full ready-to-copy prompt here
</ai_prompt>
<footer>Copy this prompt and use it with your photo in Midjourney / Flux / ChatGPT</footer>
"""
    raw = groq_generate(system, max_tokens=850)
    title = _extract("title", raw) or theme.title()[:60]
    ai_prompt = _extract("ai_prompt", raw)
    footer = _extract("footer", raw) or "Copy this prompt and use it with your photo in Midjourney / Flux / ChatGPT"
    if not ai_prompt or len(ai_prompt) < 40:
        cleaned = re.sub(r"</?title>|</?ai_prompt>|</?footer>", "", raw, flags=re.I).strip()
        if len(cleaned) > 60:
            ai_prompt = cleaned
        else:
            ai_prompt = (
                f"Do not change the face. Keep exact facial features and identity from the reference photo. "
                f"Photorealistic portrait, {theme}, natural skin texture, detailed lighting, "
                f"shallow depth of field, 85mm lens, cinematic color grade, 9:16 vertical."
            )
    caption = f"{title}\n\n{ai_prompt}\n\n{footer}\n\n#AIPrompt #Midjourney #Flux #AIArt #PhotoPrompt"
    return {"title": title, "ai_prompt": ai_prompt, "footer": footer, "caption": caption, "theme": theme}


async def publish_ai_prompt(bot, history, index):
    recent_titles = [p.get("title", "") for p in history.get("posts", [])]
    theme = choose_theme(history)
    data = generate_prompt_post(theme, recent_titles)
    caption = data["caption"]
    if len(caption) > 4090:
        caption = caption[:4080] + "…"
    msg = await bot.send_message(chat_id=CHANNEL_ID, text=caption, disable_web_page_preview=True)
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "content_type": "ai_image_prompt",
        "theme": data.get("theme", ""),
        "title": data.get("title"),
        "ai_prompt": data.get("ai_prompt", ""),
        "status": "published",
        "message_id": msg.message_id,
    }
    record_post(history, entry)
    print(f"Post {index + 1} | ai_image_prompt | {data.get('title')} | msg={msg.message_id}")


def _strip_html(text):
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _parse_rss_items(xml_text, source_name, max_items=8):
    items = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return items
    channel = root.find("channel")
    entries = channel.findall("item") if channel is not None else []
    if not entries:
        entries = root.findall("{http://www.w3.org/2005/Atom}entry")
    for entry in entries[:max_items]:
        title, link, summary, pub = "", "", "", None
        t = entry.find("title")
        if t is not None and t.text:
            title = _strip_html(t.text)
        l = entry.find("link")
        if l is not None:
            link = (l.text or l.get("href") or "").strip()
        for tag in ("description", "summary", "content"):
            s = entry.find(tag)
            if s is None:
                s = entry.find("{http://purl.org/rss/1.0/modules/content/}encoded")
            if s is not None and (s.text or "").strip():
                summary = _strip_html(s.text)[:600]
                break
        pd = entry.find("pubDate")
        if pd is not None and pd.text:
            try:
                pub = parsedate_to_datetime(pd.text)
            except Exception:
                pass
        if not title:
            t = entry.find("{http://www.w3.org/2005/Atom}title")
            if t is not None and t.text:
                title = _strip_html(t.text)
        if not link:
            for ln in entry.findall("{http://www.w3.org/2005/Atom}link"):
                href = ln.get("href")
                if href:
                    link = href
                    break
        if not summary:
            s = entry.find("{http://www.w3.org/2005/Atom}summary")
            if s is not None and s.text:
                summary = _strip_html(s.text)[:600]
        if pub is None:
            upd = entry.find("{http://www.w3.org/2005/Atom}updated") or entry.find("{http://www.w3.org/2005/Atom}published")
            if upd is not None and upd.text:
                try:
                    pub = datetime.fromisoformat(upd.text.replace("Z", "+00:00"))
                except Exception:
                    pass
        if title and link:
            items.append({"source": source_name, "title": title, "link": link, "summary": summary, "published": pub})
    return items


def fetch_fresh_news(used_links, max_age_days=5):
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    used = set(used_links or [])
    all_items = []
    for source, url in RSS_FEEDS:
        try:
            r = requests.get(url, timeout=20, headers={"User-Agent": "LightRoomBot/1.0 (AI news aggregator)"})
            if r.status_code != 200:
                print(f"RSS {source}: HTTP {r.status_code}")
                continue
            items = _parse_rss_items(r.text, source)
            for it in items:
                if it["link"] in used:
                    continue
                pub = it.get("published")
                if pub is not None:
                    if pub.tzinfo is None:
                        pub = pub.replace(tzinfo=timezone.utc)
                    if pub < cutoff:
                        continue
                all_items.append(it)
            print(f"RSS {source}: {len(items)} items")
        except Exception as e:
            print(f"RSS {source} failed: {e}")
    def sort_key(it):
        p = it.get("published")
        if p is None:
            return datetime.min.replace(tzinfo=timezone.utc)
        if p.tzinfo is None:
            p = p.replace(tzinfo=timezone.utc)
        return p
    all_items.sort(key=sort_key, reverse=True)
    return all_items


def generate_news_from_rss(item):
    source, title, summary, link = item["source"], item["title"], item.get("summary") or "", item["link"]
    system = f"""Rewrite this real AI news item into a short, punchy Telegram post (style of @prompt channel).

Source: {source}
Original title: {title}
Summary / excerpt: {summary[:500]}
Link: {link}

Rules:
- English only
- Start with 1 emoji + a short attention-grabbing headline (max 12 words) — do NOT just copy the original title
- Then 2–3 short paragraphs that explain what happened and why it matters
- Conversational, slightly witty, not corporate
- Stick to facts from the title/summary — do not invent numbers, quotes, or claims
- End with the source link on its own line
- No hashtags inside the body

Reply with EXACTLY this format:

<title>emoji + short headline</title>
<body>
post body here

Source link at the end
</body>
"""
    raw = groq_generate(system, max_tokens=450, temperature=0.75)
    post_title = _extract("title", raw)
    body = _extract("body", raw)
    if not post_title or len(post_title) < 5:
        post_title = f"🤖 {title[:80]}"
    if not body or len(body) < 40:
        body = f"{summary[:350]}\n\nSource: {source}\n{link}" if summary else f"{title}\n\nSource: {source}\n{link}"
    elif link not in body:
        body = body.rstrip() + f"\n\n{link}"
    caption = f"{post_title}\n\n{body}\n\n#AI #AInews #Tech"
    return {"title": post_title, "body": body, "caption": caption, "theme": source, "link": link}


async def publish_ai_news(bot, history, index):
    items = fetch_fresh_news(history.get("used_news_links", []))
    if not items:
        print("No fresh RSS news — falling back to AI prompt")
        await publish_ai_prompt(bot, history, index)
        return
    item = items[0]
    data = generate_news_from_rss(item)
    caption = data["caption"]
    if len(caption) > 4090:
        caption = caption[:4080] + "…"
    msg = await bot.send_message(chat_id=CHANNEL_ID, text=caption, disable_web_page_preview=False)
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "content_type": "ai_news",
        "theme": data.get("theme", ""),
        "title": data.get("title"),
        "link": data.get("link", ""),
        "status": "published",
        "message_id": msg.message_id,
    }
    record_post(history, entry)
    print(f"Post {index + 1} | ai_news | {data.get('title')} | msg={msg.message_id}")


async def snapshot_channel_stats(bot, history):
    try:
        count = await bot.get_chat_member_count(CHANNEL_ID)
    except Exception as e:
        print(f"member_count failed: {e}")
        count = None
    snap = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "member_count": count,
        "total_posts_recorded": len(history.get("posts", [])),
        "content_type_counts": dict(history.get("content_type_counts") or {}),
        "pillar_counts": dict(history.get("pillar_counts") or {}),
    }
    history.setdefault("analytics_snapshots", []).append(snap)
    save_history(history)
    print(f"Analytics | members={count}")
    return snap


async def publish_one(bot, history, index):
    content_type = choose_content_type(history)
    if content_type == "lightroom":
        await publish_lightroom(bot, history, index)
    elif content_type == "ai_news":
        await publish_ai_news(bot, history, index)
    else:
        await publish_ai_prompt(bot, history, index)


async def main():
    bot = Bot(token=TELEGRAM_TOKEN)
    history = load_history()
    for i in range(POSTS_PER_RUN):
        try:
            await publish_one(bot, history, i)
        except Exception as e:
            print(f"Error on post {i + 1}: {e}")
            import traceback
            traceback.print_exc()
    try:
        await snapshot_channel_stats(bot, history)
    except Exception as e:
        print(f"analytics: {e}")


if __name__ == "__main__":
    asyncio.run(main())
