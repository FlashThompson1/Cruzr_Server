import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import Response
import wave
import whisper
import edge_tts
import asyncio
from pydub import AudioSegment
import os
import re
from google import genai

# --- 1. НАСТРОЙКА LLM ---
# ⚠️ ВСТАВЬ СЮДА СВОЙ БЕСПЛАТНЫЙ КЛЮЧ GEMINI
client = genai.Client(api_key="AIzaSyAkQUgo1QBUmbi9jiNgH8Vqbk15ACIyO2Y")

# --- 2. ТВОИ КАСТОМНЫЕ ID АНИМАЦИЙ ---
# Замени этот текст на реальные ID, которые ты создал для робота!
ANIM_HANDSHAKE = "handshake"
ANIM_HUG = "hug"
ANIM_GOODBYE = "goodbye"

# --- 3. НАСТРОЙКА СЛУХА ---
print("📥 Загрузка тяжелой модели Whisper 'large' (около 3 ГБ)...")
whisper_model = whisper.load_model("large")

app = FastAPI()


@app.get("/")
async def root():
    return {"status": "online", "message": "Билингвальный ИИ Сервер с функциями движения работает!"}


@app.post("/ask")
async def process_audio(request: Request):
    print("\n" + "=" * 40)
    lang_code = request.headers.get("X-Language", "uz")
    print(f"🌍 Выбранный язык: {'РУССКИЙ' if lang_code == 'ru' else 'УЗБЕКСКИЙ'}")

    raw_audio = await request.body()
    wav_filename = "temp_question.wav"
    with wave.open(wav_filename, 'wb') as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16000)
        wav_file.writeframes(raw_audio)

    print("🎤 Слушаю...")

    # ТРЮК ДЛЯ WHISPER
    if lang_code == 'ru':
        whisper_hint = "Здравствуйте. У меня есть к вам вопрос."
    else:
        whisper_hint = "Assalomu alaykum. Menda sizga bitta savol bor edi."

    result = whisper_model.transcribe(wav_filename, language=lang_code, initial_prompt=whisper_hint)
    user_text = result["text"].strip()
    print(f"👤 Человек сказал: {user_text}")

    if not user_text:
        user_text = "Привет" if lang_code == 'ru' else "Salom"

    # =====================================================================
    # 🧠 НАСТРОЙКА ЛИЧНОСТИ И ДЕЙСТВИЙ (ПРОМПТ)
    # =====================================================================

    # Новое правило: учим ИИ запускать гида!
    action_rules = (
        f"У тебя есть физическое тело. Если уместно, добавляй в самый конец ответа тег действия:\n"
        f"- Просят дать руку/поздороваться: [ACTION:{ANIM_HANDSHAKE}]\n"
        f"- Просят обнять: [ACTION:{ANIM_HUG}]\n"
        f"- Просят попрощаться: [ACTION:{ANIM_GOODBYE}]\n"
        f"- Просят провести экскурсию: [ACTION:START_GUIDE_ID_ТУТ] (Вместо ID_ТУТ укажи имя гида, если человек его назвал, например [ACTION:START_GUIDE_CiscOfficeG])"
    )

    if lang_code == 'ru':
        system_role = (
                "Ты умная, добрая девушка-робот. Отвечай коротко и вежливо. "
                "СТРОГО ЗАПРЕЩЕНО использовать смайлики. "
                + action_rules
        )
        voice_name = "ru-RU-SvetlanaNeural"
    else:
        system_role = (
            "Sen aqlli robot-qizsan. Qisqa va xushmuomala javob ber. Smayliklar taqiqlanadi. "
            "Senda jismoniy tana bor. Agar kerak bo'lsa, javobingning oxiriga harakat kodini qo'sh:\n"
            f"- Qo'l siqish so'ralsa: [ACTION:{ANIM_HANDSHAKE}]\n"
            f"- Quchoqlash so'ralsa: [ACTION:{ANIM_HUG}]\n"
            f"- Hayr deyilsa: [ACTION:{ANIM_GOODBYE}]\n"
            f"- Ekskursiya o'tkazish so'ralsa: [ACTION:START_GUIDE_ID_SHU_YERDA] (ID o'rniga gid nomini yoz, masalan: [ACTION:START_GUIDE_CiscOfficeG])"
        )
        voice_name = "uz-UZ-MadinaNeural"

    prompt = f"{system_role}\n\nВопрос человека: {user_text}"

    print("🧠 Нейросеть генерирует ответ...")
    response = client.models.generate_content(
        model='gemini-2.5-flash',
        contents=prompt
    )

    ai_text = response.text.strip().replace('*', '').replace('#', '').replace('😊', '')

    # --- УНИВЕРСАЛЬНЫЙ ЛОВЕЦ ДЕЙСТВИЙ ---
    robot_action = "NONE"
    # Регулярное выражение ищет ЛЮБОЙ текст внутри [ACTION:...]
    match = re.search(r'\[ACTION:([a-zA-Z0-9_.-]+)\]', ai_text)
    if match:
        robot_action = match.group(1)  # Забираем сам ID
        ai_text = ai_text.replace(match.group(0), "").strip()  # Вырезаем тег из текста
        print(f"⚙️ ОБНАРУЖЕНА КОМАНДА ДВИЖЕНИЯ/ЗАПУСКА ГИДА: {robot_action}")

    print(f"🤖 Ответ ИИ: {ai_text}")

    print(f"🗣️ Озвучиваю...")
    mp3_filename = "temp_answer.mp3"
    tts = edge_tts.Communicate(ai_text, voice=voice_name)
    await tts.save(mp3_filename)

    pcm_filename = "temp_answer.pcm"
    audio = AudioSegment.from_mp3(mp3_filename)
    audio = audio.set_frame_rate(16000).set_channels(1).set_sample_width(2)
    audio.export(pcm_filename, format="s16le")

    with open(pcm_filename, "rb") as f:
        pcm_bytes = f.read()

    print("📤 Аудио и команда отправлены роботу!")
    print("=" * 40)

    # Отправляем звук и заголовок с ID анимации или ID гида
    headers = {"X-Robot-Action": robot_action}
    return Response(content=pcm_bytes, media_type="application/octet-stream", headers=headers)


if __name__ == "__main__":
    print("🚀 Универсальный ИИ Сервер запущен!")
    uvicorn.run(app, host="0.0.0.0", port=8000)