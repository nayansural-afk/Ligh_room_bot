"""
Light Room bot — Ready AI Image Prompts + Real AI News (RSS)

Content mix (3 posts/day via 3 scheduled runs):
  ~70%  Ready-to-copy photorealistic image prompts (AiFreeRoPrompt style)
  ~30%  Real AI news from RSS feeds, rewritten in @prompt style

Secrets: TELEGRAM_TOKEN, GROQ_API_KEY
"""

import os
import json
import re
import random
import asyncio
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from pathlib import Path
from email.utils import parsedate_to_datetime
from html import unescape

import requests
from telegram import Bot

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
GROQ_API_KEY = os.environ["GROQ_API_KEY"]
CHANNEL_ID = os.environ.get("CHANNEL_ID", "@LightRooms")

POSTS_PER_RUN = 1
HISTORY_FILE = Path("content_history.json")

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

# Real AI news RSS sources
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
            data.setdefault("theme_counts", {})
            data.setdefault("posts", [])
            data.setdefault("analytics_snapshots", [])
            data.setdefault("used_news_links", [])
            return data
        except Exception:
            pass
    return {
        "posts": [],
        "theme_counts": {},
        "analytics_snapshots": [],
        "used_news_links": [],
    }


def save_history(history):
    history["posts"] = history.get("posts", [])[-300:]
    history["analytics_snapshots"] = history.get("analytics_snapshots", [])[-90:]
    history["used_news_links"] = history.get("used_news_links", [])[-200:]
    history.setdefault("theme_counts", {})
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def record_post(history, entry):
    history.setdefault("posts", []).append(entry)
    theme = entry.get("theme") or entry.get("content_type") or "unknown"
    history.setdefault("theme_counts", {})
    history["theme_counts"][theme] = history["theme_counts"].get(theme, 0) + 1
    if entry.get("link"):
        history.setdefault("used_news_links", []).append(entry["link"])
    save_history(history)


def choose_content_type(history):
    """~70% prompt, ~30% news"""
    recent = history.get("posts", [])[-6:]
    news_count = sum(1 for p in recent if p.get("content_type") == "ai_news")
    if news_count >= 2:
        return "prompt"
    if random.random() < 0.32:
        return "news"
    return "prompt"


def choose_theme(history):
    counts = history.get("theme_counts") or {}
    max_c = max((counts.get(t, 0) for t in PROMPT_THEMES), default=0)
    weights = [(max_c - counts.get(t, 0)) + 2 for t in PROMPT_THEMES]
    return random.choices(PROMPT_THEMES, weights=weights, k=1)[0]


def groq_generate(prompt, max_tokens=900, temperature=0.85):
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
    response = requests.post(url, headers=headers, json=data, timeout=90)
    result = response.json()
    if "choices" not in result:
        raise Exception(f"Invalid Groq response: {result}")
    return result["choices"][0]["message"]["content"].strip()


def _extract(tag, text):
    m = re.search(rf"<{tag}>\s*(.*?)\s*</{tag}>", text, re.DOTALL | re.IGNORECASE)
    return m.group(1).strip() if m else ""


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
        title = ""
        link = ""
        summary = ""
        pub = None

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
            upd = entry.find("{http://www.w3.org/2005/Atom}updated") or entry.find(
                "{http://www.w3.org/2005/Atom}published"
            )
            if upd is not None and upd.text:
                try:
                    pub = datetime.fromisoformat(upd.text.replace("Z", "+00:00"))
                except Exception:
                    pass

        if title and link:
            items.append({
                "source": source_name,
                "title": title,
                "link": link,
                "summary": summary,
                "published": pub,
            })
    return items


def fetch_fresh_news(used_links, max_age_days=5):
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    used = set(used_links or [])
    all_items = []

    for source, url in RSS_FEEDS:
        try:
            r = requests.get(
                url,
                timeout=20,
                headers={"User-Agent": "LightRoomBot/1.0 (AI news aggregator)"},
            )
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
    source = item["source"]
    title = item["title"]
    summary = item.get("summary") or ""
    link = item["link"]

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
        body = (
            f"{summary[:350]}\n\nSource: {source}\n{link}"
            if summary
            else f"{title}\n\nSource: {source}\n{link}"
        )
    elif link not in body:
        body = body.rstrip() + f"\n\n{link}"

    caption = f"{post_title}\n\n{body}\n\n#AI #AInews #Tech"
    return {
        "title": post_title,
        "body": body,
        "caption": caption,
        "theme": source,
        "link": link,
    }


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
    footer = _extract("footer", raw) or (
        "Copy this prompt and use it with your photo in Midjourney / Flux / ChatGPT"
    )

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

    caption = (
        f"{title}\n\n"
        f"{ai_prompt}\n\n"
        f"{footer}\n\n"
        f"#AIPrompt #Midjourney #Flux #AIArt #PhotoPrompt"
    )
    return {
        "title": title,
        "ai_prompt": ai_prompt,
        "footer": footer,
        "caption": caption,
        "theme": theme,
    }


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
        "theme_counts": dict(history.get("theme_counts") or {}),
    }
    history.setdefault("analytics_snapshots", []).append(snap)
    save_history(history)
    print(f"Analytics | members={count}")
    return snap


async def publish_one(bot, history, index):
    content_type = choose_content_type(history)
    recent_titles = [p.get("title", "") for p in history.get("posts", [])]

    if content_type == "news":
        items = fetch_fresh_news(history.get("used_news_links", []))
        if items:
            item = items[0]
            data = generate_news_from_rss(item)
            entry_type = "ai_news"
        else:
            print("No fresh RSS news — falling back to prompt")
            theme = choose_theme(history)
            data = generate_prompt_post(theme, recent_titles)
            entry_type = "ai_image_prompt"
    else:
        theme = choose_theme(history)
        data = generate_prompt_post(theme, recent_titles)
        entry_type = "ai_image_prompt"

    caption = data["caption"]
    if len(caption) > 4090:
        caption = caption[:4080] + "…"

    msg = await bot.send_message(
        chat_id=CHANNEL_ID,
        text=caption,
        disable_web_page_preview=False if entry_type == "ai_news" else True,
    )

    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "content_type": entry_type,
        "theme": data.get("theme", ""),
        "title": data.get("title"),
        "ai_prompt": data.get("ai_prompt", ""),
        "link": data.get("link", ""),
        "status": "published",
        "message_id": msg.message_id,
    }
    record_post(history, entry)
    print(f"Post {index + 1} | {entry_type} | {data.get('title')} | msg={msg.message_id}")


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
