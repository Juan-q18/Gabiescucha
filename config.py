import os

from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN: str = os.getenv("DISCORD_BOT_TOKEN", "")

WAKE_WORD: str = os.getenv("WAKE_WORD", "señor gabriel").strip().lower()

WHISPER_MODEL: str = os.getenv("WHISPER_MODEL", "small")
WHISPER_DEVICE: str = os.getenv("WHISPER_DEVICE", "cuda")
WHISPER_COMPUTE_TYPE: str = os.getenv("WHISPER_COMPUTE_TYPE", "int8")
WHISPER_LANGUAGE: str = os.getenv("WHISPER_LANGUAGE", "es")
WHISPER_INITIAL_PROMPT: str = os.getenv(
    "WHISPER_INITIAL_PROMPT",
    "Señor Gabriel, muteá a, desmutear, silenciar, sordear, desordear, mover a, reproducir música, tocar, poné, decir, hablá, escríbime, expulsar, banear, ayuda.",
)
NO_SPEECH_THRESHOLD: float = float(os.getenv("NO_SPEECH_THRESHOLD", "-1.0"))
NO_SPEECH_PROB_THRESHOLD: float = float(os.getenv("NO_SPEECH_PROB_THRESHOLD", "0.6"))

SAVE_SEGMENTS: bool = os.getenv("SAVE_SEGMENTS", "0") == "1"
SEGMENTS_DIR: str = os.getenv("SEGMENTS_DIR", "segments")

TTS_VOICE: str = os.getenv("TTS_VOICE", "es-AR-ElenaNeural")
TTS_RATE: str = os.getenv("TTS_RATE", "+0%")

IGNORE_BOT_AUDIO: bool = os.getenv("IGNORE_BOT_AUDIO", "1") == "1"

YDL_SLEEP_REQUESTS: float = float(os.getenv("YDL_SLEEP_REQUESTS", "0.5"))
MUSIC_VOLUME: float = float(os.getenv("MUSIC_VOLUME", "0.5"))

# Latencia de las acciones por voz (en segundos).
SILENCE_DURATION: float = float(os.getenv("SILENCE_DURATION", "2.0"))
DRAIN_INTERVAL: float = float(os.getenv("DRAIN_INTERVAL", "1.0"))
BEAM_SIZE: int = int(os.getenv("BEAM_SIZE", "5"))
