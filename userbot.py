import asyncio
import io
import os
import re
import struct
import tempfile
import zlib

from pyrogram import Client, filters, idle
from pyrogram.errors import FloodWait

# ---------------- SETTINGS (sab Environment Variables se) ----------------
def env(name, default=None, required=False):
    value = os.environ.get(name) or default
    if required and not value:
        raise SystemExit(f"Environment variable {name} set nahi hai.")
    return value


API_ID = int(env("API_ID", required=True))
API_HASH = env("API_HASH", required=True)
SESSION_STRING = env("SESSION_STRING", required=True)
BOT_TOKEN = env("BOT_TOKEN", required=True)

TARGET = int(env("TARGET", "-1004435774367"))
DELAY = int(env("DELAY", "3"))                # har video ke baad wait (seconds)
START_NUMBER = int(env("START_NUMBER", "1"))  # pehle video par yeh number lagega

# Jis video me thumbnail nahi hai uska kya kare:
#   "black" -> pehle kaali image (number caption ke saath) bhejo, phir video
#   "skip"  -> aise video ko chhod do (number bhi nahi badhega)
NO_THUMB_MODE = env("NO_THUMB_MODE", "black").lower()
# -------------------------------------------------------------------------

# userbot (asli kaam) - login nahi, seedha session string se
userbot = Client(
    "userbot",
    api_id=API_ID,
    api_hash=API_HASH,
    session_string=SESSION_STRING,
    in_memory=True,
)
# control bot (sirf links lene ke liye)
bot = Client(
    "controlbot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    in_memory=True,
)

state = {
    "stop": False,
    "running": False,
    "count": START_NUMBER,
    "owner": None,
    "sticker": None,
}


def parse_links(text):
    """Private (t.me/c/ID/N) aur public (t.me/username/N) dono links."""
    out = []
    for m in re.finditer(r"t\.me/(?:c/(\d+)|([A-Za-z0-9_]{4,}))/(\d+)", text):
        private_id, username, mid = m.groups()
        if private_id:
            out.append((int("-100" + private_id), int(mid)))
        else:
            out.append((username, int(mid)))
    return out


def make_black_image(w=1280, h=720):
    """Bina kisi extra library ke kaali PNG image banata hai (BytesIO)."""
    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    row = b"\x00" + b"\x00" * (3 * w)
    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(row * h, 9))
        + chunk(b"IEND", b"")
    )
    buf = io.BytesIO(png)
    buf.name = "black.png"
    return buf


async def safe(fn, *args, **kwargs):
    """Flood wait aane par apne aap ruk kar dobara try karta hai."""
    while True:
        try:
            return await fn(*args, **kwargs)
        except FloodWait as e:
            await asyncio.sleep(e.value + 1)


async def load_sticker():
    """Userbot account ke Saved Messages me jo sabse naya sticker hai wahi use hoga."""
    async for m in userbot.get_chat_history("me", limit=100):
        if m.sticker:
            state["sticker"] = m.sticker.file_id
            return True
    return False


async def send_video_copy(msg):
    """Video bina sender name aur bina caption ke (seedha copy, download/upload nahi)."""
    try:
        await safe(userbot.copy_message, TARGET, msg.chat.id, msg.id, caption="")
    except Exception:
        # Agar channel me 'restrict saving content' ON hai to download karke upload
        with tempfile.TemporaryDirectory() as tmp:
            path = await safe(userbot.download_media, msg, file_name=tmp + os.sep)
            await safe(
                userbot.send_video,
                TARGET,
                path,
                duration=msg.video.duration,
                width=msg.video.width,
                height=msg.video.height,
                supports_streaming=True,
            )


async def process(msg):
    if not msg or msg.empty or not msg.video:
        return False

    # 1) Thumbnail download karke number caption ke saath bhejna
    if msg.video.thumbs:
        thumb = await safe(
            userbot.download_media, msg.video.thumbs[-1].file_id, in_memory=True
        )
        thumb.name = "thumb.jpg"
        await safe(userbot.send_photo, TARGET, thumb, caption=str(state["count"]))
    elif NO_THUMB_MODE == "skip":
        return False  # bina thumbnail wala video skip
    else:
        # bina thumbnail wale video se pehle kaali image
        await safe(
            userbot.send_photo, TARGET, make_black_image(), caption=str(state["count"])
        )

    # 2) Wahi video bina sender name aur bina caption
    await send_video_copy(msg)

    # 3) Sticker
    if state["sticker"]:
        await safe(userbot.send_sticker, TARGET, state["sticker"])

    state["count"] += 1
    return True


async def run_range(chat_ref, first, last, notify):
    try:
        src = await userbot.get_chat(chat_ref)
    except Exception as e:
        await notify(f"Channel nahi mila. Kya aap us channel ke member hain?\n{e}")
        return

    done = 0
    ids = list(range(first, last + 1))
    for i in range(0, len(ids), 100):
        batch = ids[i:i + 100]
        msgs = await safe(userbot.get_messages, src.id, batch)
        for msg in msgs:
            if state["stop"]:
                await notify(f"Roka gaya. Ab tak {done} video bheje.")
                return
            try:
                if await process(msg):
                    done += 1
                    await asyncio.sleep(DELAY)
            except Exception as e:
                print(f"Message {getattr(msg, 'id', '?')} failed: {e}")
    await notify(f"Khatam ✅ Total {done} video bheje gaye.")


@bot.on_message(filters.private & filters.text)
async def handler(_, message):
    # Sirf userbot wale account ka owner hi bot use kar sakta hai
    if not message.from_user or message.from_user.id != state["owner"]:
        return
    text = message.text or ""

    if text.strip() == "/start":
        await message.reply_text(
            "Do link bhejiye (pehla aur aakhri), ek hi message me:\n"
            "https://t.me/c/4487278039/2\nhttps://t.me/c/4487278039/200\n\n"
            "Rokne ke liye /stop"
        )
        return

    if text.strip() == "/stop":
        state["stop"] = True
        await message.reply_text("Ruk raha hoon...")
        return

    links = parse_links(text)
    if len(links) < 2:
        return
    if links[0][0] != links[1][0]:
        await message.reply_text("Dono link ek hi channel ke hone chahiye.")
        return
    if state["running"]:
        await message.reply_text("Pehle wala kaam chal raha hai. Rokne ke liye /stop likhein.")
        return

    if not state["sticker"] and not await load_sticker():
        await message.reply_text(
            "Userbot account ke Saved Messages me sticker nahi mila. "
            "Sticker wahan forward karke link dobara bhejein."
        )
        return

    chat_ref = links[0][0]
    first, last = sorted((links[0][1], links[1][1]))
    state["stop"] = False
    state["running"] = True
    state["count"] = START_NUMBER
    await message.reply_text(f"Shuru: {first} se {last} tak")
    try:
        await run_range(chat_ref, first, last, message.reply_text)
    finally:
        state["running"] = False


async def main():
    await userbot.start()  # session string se, OTP/login nahi
    # private channels pehchanne ke liye zaroori
    async for _ in userbot.get_dialogs():
        pass
    state["owner"] = (await userbot.get_me()).id
    await load_sticker()

    await bot.start()
    print("Userbot aur bot dono chalu hain. Bot ko /start karke links bhejiye.")
    await idle()
    await bot.stop()
    await userbot.stop()


if __name__ == "__main__":
    asyncio.get_event_loop().run_until_complete(main())
