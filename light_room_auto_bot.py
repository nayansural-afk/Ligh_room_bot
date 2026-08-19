"""
Light Room bot — Ready AI Image Prompts + AI News

Content mix (3 posts/day via 3 scheduled runs):
  ~70%  Ready-to-copy photorealistic image prompts (AiFreeRoPrompt style)
  ~30%  Short interesting AI news (prompt channel style)

Secrets: TELEGRAM_TOKEN, GROQ_API_KEY
"""

import os
import json
import re
import random
import asyncio
from datetime import datetime, timezone
from pathlib import Path

import requests
from telegram import Bot

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
GROQ_API_KEY = os.environ["GROQ_API_KEY"]
CHANNEL_ID = os.environ.get("CHANNEL_ID", "@LightRooms")

POSTS_PER_RUN = 1
HISTORY_FILE = Path("content_history.json")

# Detailed themes for face-preserving photo prompts (AiFreeRoPrompt style)
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

NEWS_TOPICS = [
    "new open-weight LLM release",
    "image generation model upgrade",
    "AI agent or tool breakthrough",
    "interesting research paper or demo",
    "hardware / inference speed news",
    "funny or surprising AI story",
    "open-source model comparison",
]


def load_history():
    if HISTORY_FILE.exists():
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"posts": [], "theme_counts": {}, "analytics_snapshots": []}


def save_history(history):
    history["posts"] = history.get("posts", [])[-300:]
    history["analytics_snapshots"] = history.get("analytics_snapshots", [])[-90:]
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def record_post(history, entry):
    history.setdefault("posts", []).append(entry)
    theme = entry.get("theme", entry.get("content_type", ""))
    history.setdefault("theme_counts", {})[theme] = (
        history["theme_counts"].get(theme, 0) + 1
    )
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
    counts = history.get("theme_counts", {})
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


def generate_prompt_post(theme, recent_titles):
    avoid = ", ".join([t for t in recent_titles[-10:] if t]) or "none"
    system = f"""You write ready-to-copy AI image prompts for Telegram channels like AiFreeRoPrompt.

Theme / concept: {theme}
Do NOT reuse these recent titles: {avoid}

Rules for the prompt itself:
- English only
- Photorealistic or highly detailed
- Always include strong face-preservation instruction: "Do not change the face. Keep exact facial features, identity, skin tone and proportions from the reference photo."
- Include lighting, camera/lens feel, composition, aspect ratio (prefer 9:16 or 3:4 or 4:5)
- 80–160 words
- Ready to paste into Midjourney, Flux, ChatGPT image, or similar with a selfie/reference photo
- No hashtags inside the prompt text

Reply with EXACTLY this XML format and nothing else:

<title>short catchy title with 1 emoji</title>
<ai_prompt>
the full ready-to-copy prompt here
</ai_prompt>
<footer>one short Persian or English line telling user to copy the prompt and use it with their photo in Midjourney / Flux / ChatGPT</footer>
"""
    raw = groq_generate(system, max_tokens=850)
    title = _extract("title", raw) or theme.title()[:60]
    ai_prompt = _extract("ai_prompt", raw)
    footer = _extract("footer", raw) or "Copy the prompt → upload your selfie in Midjourney / Flux / ChatGPT"

    # Fallback if extraction failed: take the longest meaningful block
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


def generate_news_post(recent_hooks):
    avoid = ", ".join([h for h in recent_hooks[-6:] if h]) or "none"
    topic = random.choice(NEWS_TOPICS)
    system = f"""Write a short, punchy AI news post for a Telegram channel (style of @prompt).

Topic direction: {topic}
Avoid repeating these recent hooks: {avoid}

Style:
- 1 bold emoji + short attention-grabbing headline
- 2–4 short paragraphs max
- Conversational, slightly witty, not corporate
- English
- Focus on something that feels fresh / interesting / useful
- No fake numbers or made-up sources
- End with nothing extra

Reply with EXACTLY this format:

<title>emoji + short headline</title>
<body>
the full post body here
</body>
"""
    raw = groq_generate(system, max_tokens=450, temperature=0.9)
    title = _extract("title", raw) or "🤖 AI update"
    body = _extract("body", raw) or raw.strip()
    if not body or len(body) < 30:
        body = raw.strip()

    caption = f"{title}\n\n{body}\n\n#AI #AInews #Tech"
    return {
        "title": title,
        "body": body,
        "caption": caption,
        "theme": topic,
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
        "theme_counts": dict(history.get("theme_counts", {})),
    }
    history.setdefault("analytics_snapshots", []).append(snap)
    save_history(history)
    print(f"Analytics | members={count}")
    return snap


async def publish_one(bot, history, index):
    content_type = choose_content_type(history)
    recent_titles = [p.get("title", "") for p in history.get("posts", [])]

    if content_type == "news":
        data = generate_news_post(recent_titles)
        entry_type = "ai_news"
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
        disable_web_page_preview=True,
    )

    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "content_type": entry_type,
        "theme": data.get("theme", ""),
        "title": data.get("title"),
        "ai_prompt": data.get("ai_prompt", ""),
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

    try:
        await snapshot_channel_stats(bot, history)
    except Exception as e:
        print(f"analytics: {e}")


if __name__ == "__main__":
    asyncio.run(main())
