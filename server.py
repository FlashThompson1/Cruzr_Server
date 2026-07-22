import uvicorn
from fastapi import FastAPI, Request, UploadFile, File as UploadBody, HTTPException
from fastapi.responses import Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import edge_tts
import re
import os
from pathlib import Path
import uuid
from google import genai
from google.genai import types



import imageio_ffmpeg
from pydub import AudioSegment
AudioSegment.converter = imageio_ffmpeg.get_ffmpeg_exe()


from livekit import api

# === ВАШИ КЛЮЧИ LIVEKIT ===
# LiveKit credentials must match the keys configured on the self-hosted server.
# Keep secrets in Render environment variables instead of committing them here.
LIVEKIT_URL = "wss://cruzr-neovex.duckdns.org"
LIVEKIT_API_KEY = "APICWoWc2ckBgqh"
LIVEKIT_API_SECRET = "7aeJDn5fWe7XQRTdVNemXsFTz7YxEVovW3pByNuAKV3A"

# === НАСТРОЙКА LLM (GEMINI) ===
client = genai.Client(api_key="AIzaSyAxDIQ3bmpX2g1rmAX5fbYKKCiVy4Pt4gw")

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

GUIDE_MEDIA_DIR = Path(os.getenv("GUIDE_MEDIA_DIR", "guide_media")).resolve()
GUIDE_MEDIA_DIR.mkdir(parents=True, exist_ok=True)
GUIDE_MEDIA_MAX_BYTES = 500 * 1024 * 1024
GUIDE_MEDIA_EXTENSIONS = {
    ".mp4", ".webm", ".mov", ".m4v",
    ".mp3", ".wav", ".m4a", ".aac", ".ogg",
    ".jpg", ".jpeg", ".png", ".webp"
}
app.mount("/guide_media", StaticFiles(directory=str(GUIDE_MEDIA_DIR)), name="guide_media")

@app.get("/")
async def root():
    return {
        "status": "Neovex Cloud Server is Online",
        "livekit_configured": bool(LIVEKIT_API_KEY and LIVEKIT_API_SECRET),
    }

@app.post("/guide/upload")
async def upload_guide_media(request: Request, file: UploadFile = UploadBody(...)):
    original_name = Path(file.filename or "media.bin").name
    extension = Path(original_name).suffix.lower()
    if extension not in GUIDE_MEDIA_EXTENSIONS:
        raise HTTPException(status_code=415, detail="Unsupported guide media format")

    stored_name = f"{uuid.uuid4().hex}{extension}"
    output = GUIDE_MEDIA_DIR / stored_name
    written = 0
    try:
        with output.open("wb") as target:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > GUIDE_MEDIA_MAX_BYTES:
                    raise HTTPException(status_code=413, detail="Guide media is larger than 500 MB")
                target.write(chunk)
    except Exception:
        output.unlink(missing_ok=True)
        raise
    finally:
        await file.close()

    public_base = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
    if not public_base:
        public_base = str(request.base_url).rstrip("/")
    return {
        "url": f"{public_base}/guide_media/{stored_name}",
        "filename": original_name,
        "size": written,
    }

@app.get("/get_token")
async def get_token(request: Request):
    room = request.query_params.get("room")
    participant_name = request.query_params.get("participant_name")
    is_operator_str = request.query_params.get("is_operator", "false")
    is_operator = is_operator_str.lower() == "true"

    if not room or not participant_name:
        return Response(content="Missing room or participant_name", status_code=400)

    if not LIVEKIT_API_KEY or not LIVEKIT_API_SECRET:
        raise HTTPException(
            status_code=503,
            detail="LIVEKIT_API_KEY and LIVEKIT_API_SECRET are not configured on the token server",
        )

    grant = api.VideoGrants(room_join=True, room=room)
    if is_operator:
        grant.can_publish = True
        grant.can_publish_data = True
        grant.can_subscribe = True
    else:
        grant.can_publish = True
        grant.can_subscribe = True

    token = api.AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET) \
        .with_identity(participant_name) \
        .with_name(participant_name) \
        .with_grants(grant)

    return {"token": token.to_jwt(), "livekit_url": LIVEKIT_URL}

@app.post("/ask")
async def process_audio(request: Request):
    print("\n" + "=" * 40)
    lang_code = request.headers.get("X-Language", "ru")
    raw_audio = await request.body()

    if len(raw_audio) < 1000:
        print("⚠️ Получено пустое аудио. Игнорирую.")
        return Response(content=b"", media_type="application/octet-stream", headers={"X-Robot-Action": "NONE"})

    print("🧠 Отправка аудио напрямую в Gemini (без локального Whisper)...")

    action_rules = (
        f"Добавляй тег действия в конец:\n"
        f"- Поздороваться: [ACTION:handshake]\n"
        f"- Обнять: [ACTION:hug]\n"
        f"- Пока: [ACTION:goodbye]\n"
        f"- Да: [ACTION:nod]\n"
        f"- Нет: [ACTION:shake_head]"
    )

    if lang_code == 'ru':
        system_role = "Ты умная робот-девушка. Выслушай аудиосообщение пользователя и отвечай коротко и дружелюбно. " + action_rules
        voice_name = "ru-RU-SvetlanaNeural"
    else:
        system_role = "Sen aqlli robot-qizsan. Ovozli xabarni tingla va qisqa, do'stona javob ber. " + action_rules
        voice_name = "uz-UZ-MadinaNeural"

    # Превращаем аудио в формат, понятный Gemini
    audio_part = types.Part.from_bytes(data=raw_audio, mime_type='audio/aac')

    try:
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=[system_role, audio_part]
        )
        ai_text = response.text.strip().replace('*', '')
    except Exception as e:
        print(f"⚠️ Ошибка Gemini API: {e}")
        return Response(content=b"", media_type="application/octet-stream", headers={"X-Robot-Action": "NONE"})

    robot_action = "NONE"
    match = re.search(r'\[ACTION:([a-zA-Z0-9_.-]+)\]', ai_text)
    if match:
        robot_action = match.group(1)
        ai_text = ai_text.replace(match.group(0), "").strip()

    print(f"🤖 Ответ ИИ: {ai_text} (Действие: {robot_action})")

    print("🎙 Генерация голоса Edge TTS...")
    mp3_filename = "temp_answer.mp3"
    tts = edge_tts.Communicate(ai_text, voice=voice_name)
    await tts.save(mp3_filename)

    pcm_filename = "temp_answer.pcm"
    audio = AudioSegment.from_mp3(mp3_filename).set_frame_rate(16000).set_channels(1).set_sample_width(2)
    audio.export(pcm_filename, format="s16le")

    with open(pcm_filename, "rb") as f:
        pcm_bytes = f.read()

    print("🚀 Отправка ответа на робота!")
    return Response(content=pcm_bytes, media_type="application/octet-stream", headers={"X-Robot-Action": robot_action})

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
