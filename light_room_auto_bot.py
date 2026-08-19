"""
ربات Light Room — نسخه اتوماتیک
هر بار اجرا: چند پریست اورجینال (XMP) با AI می‌سازه، فایل XMP واقعی تولید می‌کنه،
کپشن فارسی می‌نویسه و به کانال تلگرام می‌فرسته.

نحوه‌ی اجرا روی PythonAnywhere:
  - این فایل رو آپلود کن
  - از تب "Tasks" یه Scheduled Task بساز که هر روز این فایل رو اجرا کنه:
      python3.x /home/USERNAME/light_room_auto_bot.py
"""

import requests
import random
import uuid as uuid_lib
import asyncio
from telegram import Bot

# ---------- تنظیمات ----------
TELEGRAM_TOKEN = "8832220907:AAE7EFce1Wet5P9fsDN0kb_tpqXUUsrFw58"
GROQ_API_KEY = "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"
CHANNEL_ID = "@LightRooms"

POSTS_PER_RUN = 2  # چند پست هر بار اجرا (چون PythonAnywhere رایگان معمولاً فقط ۱ بار در روز اجرا میشه)

# چند تم/سبک پایه که مدل هر بار یکی رو بسط می‌ده تا پریست‌ها تکراری نباشن
STYLE_SEEDS = [
    "سینمایی گرم با کنتراست بالا",
    "مینیمال و روشن با تون خنثی",
    "خیابانی تیره با گرین ملایم",
    "پاستلی و نرم برای پرتره",
    "غروب طلایی با اشباع بالا",
    "بلک اند وایت درام با کنتراست شدید",
    "فیلم آنالوگ با نویز و رنگ‌پریدگی کنترل‌شده",
    "طبیعت سبز پررنگ برای لندسکیپ",
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
    """از AI می‌خواد یه ست پارامتر واقعی و منطقی برای پریست بسازه (JSON خالص)."""
    prompt = f"""
    یک پریست اورجینال Adobe Lightroom برای سبک زیر طراحی کن:
    سبک: {style}

    فقط یک JSON با دقیقاً همین کلیدها برگردون (بدون هیچ توضیح اضافه):
    {{
      "name": "اسم خلاقانه انگلیسی برای پریست (کوتاه، بدون فاصله زیاد)",
      "temperature": عدد بین -50 تا 50,
      "tint": عدد بین -30 تا 30,
      "exposure": عدد اعشاری بین -1.0 تا 1.0,
      "contrast": عدد بین -40 تا 60,
      "highlights": عدد بین -80 تا 40,
      "shadows": عدد بین -40 تا 80,
      "whites": عدد بین -30 تا 40,
      "blacks": عدد بین -40 تا 30,
      "clarity": عدد بین -20 تا 40,
      "vibrance": عدد بین -20 تا 50,
      "saturation": عدد بین -20 تا 30
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
    یک کپشن فارسی جذاب برای کانال تلگرامی «Light Room» بنویس که یک پریست جدید
    به اسم "{preset_name}" با سبک «{style}» معرفی می‌کند.

    شامل:
    - تیتر جذاب با ایموجی
    - توضیح کوتاه درباره حس و حال این پریست و کجا خوب جواب می‌ده
    - هشتگ‌های مرتبط با ادیت عکس و لایت‌روم
    - لحن دوستانه و کوتاه (حداکثر ۶-۷ خط)
    """
    return groq_generate(prompt)


async def publish_one(bot: Bot, style: str, index: int):
    params = generate_preset_params(style)
    xmp_content = build_xmp(params)
    caption = generate_caption(style, params["name"])

    filename = f"{params['name'].replace(' ', '_')}.xmp"
    with open(filename, "w", encoding="utf-8") as f:
        f.write(xmp_content)

    with open(filename, "rb") as f:
        await bot.send_document(chat_id=CHANNEL_ID, document=f, filename=filename)

    await bot.send_message(chat_id=CHANNEL_ID, text=caption)
    print(f"✅ پست {index+1} منتشر شد: {params['name']}")


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
