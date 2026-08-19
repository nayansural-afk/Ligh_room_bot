"""
Light Room bot — Viral AI Image Prompt Publisher

Posts ready-to-copy image-generation prompts (Midjourney / Flux / SD style).
Style: "Send your photo → become Spider-Man" viral transformations.

3 posts per run. Schedule runs 3x/day via GitHub Actions.

Secrets: TELEGRAM_TOKEN, GROQ_API_KEY
Optional: CHANNEL_ID (default @LightRooms)
"""

import os
import json
import random
import asyncio
from datetime import datetime, timezone
from pathlib import Path

import requests
from telegram import Bot

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
GROQ_API_KEY = os.environ["GROQ_API_KEY"]
CHANNEL_ID = os.environ.get("CHANNEL_ID", "@LightRooms")

POSTS_PER_RUN = 3
HISTORY_FILE = Path("content_history.json")

# Prompt themes — viral "transform yourself" style
PROMPT_THEMES = [
    "superhero costume transformation",
    "anime character version of yourself",
    "movie character cosplay photorealistic",
    "fantasy warrior / mage portrait",
    "cyberpunk neon street style",
    "oil painting classical portrait",
    "magazine cover fashion shoot",
    "80s retro film look portrait",
    "astronaut / space explorer",
    "medieval knight in armor",
    "vampire gothic elegance",
    "samurai warrior portrait",
    "Disney / Pixar 3D character",
    "black and white noir detective",
    "tropical vacation magazine photo",
    "rockstar stage performance",
    "ancient Egyptian royalty",
    "steampunk inventor",
    "winter wonderland fantasy",
    "sports athlete action shot",
    "royal Victorian era portrait",
    "zombie apocalypse survivor",
    "underwater mermaid / merman",
    "wild west cowboy",
    "futuristic android",
]


def load_history():
    if HISTORY_FILE.exists():
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "posts": [],
        "theme_counts": {},
        "analytics_snapshots": [],
    }


def save_history(history):
    history["posts"] = history.get("posts", [])[-300:]
    history["analytics_snapshots"] = history.get("analytics_snapshots", [])[-90:]
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def record_post(history, entry):
    history.setdefault("posts", []).append(entry)
    theme = entry.get("theme", "")
    history.setdefault("theme_counts", {})[theme] = (
        history["theme_counts"].get(theme, 0) + 1
    )
    save_history(history)


def choose_theme(history):
    """Prefer less-used themes (exploration 20%)."""
    if random.random() < 0.2:
        return random.choice(PROMPT_THEMES)
    counts = history.get("theme_counts", {})
    max_c = max(counts.values()) if counts else 0
    weights = [(max_c - counts.get(t, 0)) + 2 for t in PROMPT_THEMES]
    return random.choices(PROMPT_THEMES, weights=weights, k=1)[0]


def groq_generate(prompt, json_mode=False, max_tokens=1000):
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
    }
    data = {
        "model": "openai/gpt-oss-20b",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.9,
        "max_tokens": max_tokens,
    }
    if json_mode:
        data["response_format"] = {"type": "json_object"}
    response = requests.post(url, headers=headers, json=data, timeout=60)
    result = response.json()
    if "choices" not in result:
        raise Exception(f"Invalid Groq response: {result}")
    return result["choices"][0]["message"]["content"]


def generate_prompt_post(theme, recent_titles):
    """Create one viral AI image prompt package."""
    avoid = ", ".join(recent_titles[-8:]) if recent_titles else "none"
    prompt = f"""
You write viral content for a Telegram channel about AI image prompts
(Midjourney, Flux, Stable Diffusion, ChatGPT images).

Theme for this post: {theme}

Avoid repeating these recent titles: {avoid}

Create a post in this exact style of viral hooks:
- "Send your photo and become Spider-Man"
- "Turn yourself into a Studio Ghibli character"
- "Your face as a 1920s Hollywood star"

Return ONLY valid JSON with these keys:
{{
  "title": "short punchy title with 1 emoji (max 10 words)",
  "hook": "one viral line like: Send your selfie → become ...",
  "ai_prompt": "a complete, ready-to-copy image generation prompt in English. Include: subject description, style, lighting, camera/lens feel, quality tags. 40-80 words. Written so someone pastes it into Midjourney or Flux with their photo / face reference.",
  "how_to": "2 short lines: how to use (e.g. upload selfie + paste prompt in Midjourney / ChatGPT / Flux)",
  "caption": "full Telegram post text in English:\n- start with the hook\n- blank line\n- the AI prompt inside triple backticks\n- blank line\n- how_to\n- 5 relevant hashtags\nKeep under 900 characters total."
}}
"""
    raw = groq_generate(prompt, json_mode=True, max_tokens=900)
    return json.loads(raw)


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
    theme = choose_theme(history)
    recent_titles = [p.get("title", "") for p in history.get("posts", [])]
    data = generate_prompt_post(theme, recent_titles)

    caption = data.get("caption") or data.get("hook", theme)
    # Telegram caption limit is 4096 for text messages
    if len(caption) > 4000:
        caption = caption[:3990] + "…"

    msg = await bot.send_message(
        chat_id=CHANNEL_ID,
        text=caption,
        disable_web_page_preview=True,
    )

    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "content_type": "ai_image_prompt",
        "theme": theme,
        "title": data.get("title"),
        "hook": data.get("hook"),
        "ai_prompt": data.get("ai_prompt"),
        "status": "published",
        "message_id": msg.message_id,
    }
    record_post(history, entry)
    print(f"Post {index + 1}/3 | {theme} | {data.get('title')} | msg={msg.message_id}")


async def main():
    bot = Bot(token=TELEGRAM_TOKEN)
    history = load_history()

    for i in range(POSTS_PER_RUN):
        try:
            await publish_one(bot, history, i)
            # small gap so posts don't flood as one block
            if i < POSTS_PER_RUN - 1:
                await asyncio.sleep(8)
        except Exception as e:
            print(f"Error on post {i + 1}: {e}")

    try:
        await snapshot_channel_stats(bot, history)
    except Exception as e:
        print(f"analytics: {e}")


if __name__ == "__main__":
    asyncio.run(main())
