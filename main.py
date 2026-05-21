import re
import os
import httpx
import yt_dlp
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    MessageHandler,
    CommandHandler,
    filters,
    ContextTypes,
)

load_dotenv()

TOKEN = os.getenv("TOKEN")

PATTERNS = {
    "tiktok": re.compile(
        r'(https?://)?(www\.)?(vm\.tiktok\.com|vt\.tiktok\.com|tiktok\.com|m\.tiktok\.com)(/[^\s]*)?'
    ),
    "youtube": re.compile(
        r'(https?://)?(www\.)?(youtube\.com/watch\?v=|youtu\.be/|youtube\.com/shorts/)([^\s&]+)'
    ),
    "spotify": re.compile(
        r'(https?://)?open\.spotify\.com/(track)/([^\s?/]+)'
    ),
}


def detect_url(text: str) -> tuple[str, str] | tuple[None, None]:
    for platform, pattern in PATTERNS.items():
        match = pattern.search(text)
        if match:
            url = match.group(0)
            if not url.startswith("http"):
                url = "https://" + url
            return platform, url
    return None, None


async def download_yt(url: str, path: str, audio_only: bool = False):
    ydl_opts = {
        "outtmpl": path,
        "quiet": True,
        "noplaylist": True,
    }
    if audio_only:
        ydl_opts["format"] = "bestaudio/best"
        ydl_opts["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }]
    else:
        ydl_opts["format"] = "mp4/bestvideo+bestaudio/best"
        ydl_opts["merge_output_format"] = "mp4"

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])


async def download_spotify(url: str, path: str) -> dict:
    track_id = url.split("/track/")[1].split("?")[0]

    async with httpx.AsyncClient(timeout=60) as client:
        res = await client.post(
            "https://lucida.to/api/load",
            json={"url": url, "country": "DE"},
            headers={"Content-Type": "application/json"}
        )
        data = res.json()

        if not data.get("url"):
            raise Exception("Не удалось получить ссылку на трек")

        mp3 = await client.get(data["url"], follow_redirects=True)
        with open(path, "wb") as f:
            f.write(mp3.content)

        return {
            "title": data.get("metadata", {}).get("title", "track"),
            "artist": data.get("metadata", {}).get("artist", "unknown"),
        }


async def process_url(
    context,
    text: str,
    chat_id: int,
    business_connection_id: str | None = None,
    reply_to_message_id: int | None = None,
):
    platform, url = detect_url(text)
    if not url:
        return

    uid = abs(hash(text + str(chat_id))) % 10**9
    send_kwargs = {"business_connection_id": business_connection_id} if business_connection_id else {}

    msg = await context.bot.send_message(
        chat_id=chat_id,
        text="⏳ Скачиваю...",
        reply_to_message_id=reply_to_message_id,
        **send_kwargs,
    )

    try:
        if platform in ("tiktok", "youtube"):
            path = f"video_{uid}.mp4"
            await download_yt(url, path)
            size_mb = os.path.getsize(path) / (1024 * 1024)

            if platform == "youtube" and size_mb > 2000:
                os.remove(path)
                await context.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=msg.message_id,
                    text="⚠️ Видео > 2 ГБ, отправляю только аудио...",
                )
                audio_path = f"audio_{uid}.mp3"
                await download_yt(url, audio_path, audio_only=True)
                with open(audio_path, "rb") as f:
                    await context.bot.send_audio(
                        chat_id=chat_id,
                        audio=f,
                        reply_to_message_id=reply_to_message_id,
                        **send_kwargs,
                    )
                os.remove(audio_path)

            elif size_mb > 2000:
                os.remove(path)
                await context.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=msg.message_id,
                    text=f"❌ Видео слишком большое ({size_mb:.1f} МБ), максимум 2 ГБ.",
                )
                return

            else:
                with open(path, "rb") as f:
                    await context.bot.send_video(
                        chat_id=chat_id,
                        video=f,
                        reply_to_message_id=reply_to_message_id,
                        **send_kwargs,
                    )
                os.remove(path)

        elif platform == "spotify":
            path = f"track_{uid}.mp3"
            meta = await download_spotify(url, path)
            with open(path, "rb") as f:
                await context.bot.send_audio(
                    chat_id=chat_id,
                    audio=f,
                    title=meta["title"],
                    performer=meta["artist"],
                    reply_to_message_id=reply_to_message_id,
                    **send_kwargs,
                )
            os.remove(path)

        await context.bot.delete_message(chat_id=chat_id, message_id=msg.message_id)

    except yt_dlp.utils.DownloadError:
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=msg.message_id,
            text="❌ Не удалось скачать. Видео приватное или удалено.",
        )
    except Exception as e:
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=msg.message_id,
            text=f"❌ Ошибка: {e}",
        )


async def handle_direct(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text or ""
    await process_url(
        context=context,
        text=text,
        chat_id=update.message.chat.id,
        reply_to_message_id=update.message.message_id,
    )


async def handle_business(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        update.business_message
        or update.edited_business_message
    )
    if not msg:
        return
    text = msg.text or ""
    await process_url(
        context=context,
        text=text,
        chat_id=msg.chat.id,
        business_connection_id=msg.business_connection_id,
        reply_to_message_id=msg.message_id,
    )



async def handle_business_sent(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.edited_business_message or update.business_message
    if not msg:
        return
    text = msg.text or ""
    await process_url(
        context=context,
        text=text,
        chat_id=msg.chat.id,
        business_connection_id=msg.business_connection_id,
        reply_to_message_id=msg.message_id,
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "⚡️ Привет! Я Zappy.\n\n"
        "Кидай ссылку — пришлю файл:\n"
        "🎬 TikTok → видео\n"
        "▶️ YouTube → видео или аудио\n"
        "🎵 Spotify → MP3"
    )


app = ApplicationBuilder().token(TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_direct))
app.add_handler(MessageHandler(filters.UpdateType.BUSINESS_MESSAGE, handle_business))
app.add_handler(MessageHandler(filters.UpdateType.EDITED_BUSINESS_MESSAGE, handle_business_sent))
app.add_handler(MessageHandler(filters.ALL, debug_update), group=1)

async def debug_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print(f"UPDATE: {update}")

app.run_polling(allowed_updates=["message", "business_message", "edited_business_message"])
