# Gabiescucha

Bot de Discord que escucha la voz del canal, la transcribe **en local** (faster-whisper + GPU CUDA) y ejecuta comandos por voz: música, TTS, mensajes al chat y moderación del servidor.

Sin depender de APIs de transcripción: todo el audio se procesa en tu propia PC.

---

## Características

| Función | Descripción |
| --- | --- |
| 🎧 Escucha y transcripción | Detecta actividad de voz por energía (VAD), separa segmentos por usuario y transcribe con faster-whisper (`small`, español). |
| 🎵 Música | Búsqueda en YouTube por texto o URLs, cola de reproducción, skip, pausa, volumen, shuffle y loop. |
| 🗣️ TTS | El bot habla en el canal de voz con edge-tts (voz `es-AR-ElenaNeural`). Si hay música, la pausa y la reanuda. |
| 💬 Mensajes al chat | Por voz, el bot escribe lo que le pedís en el canal de texto. |
| 🛡️ Moderación | Mutear, desmutear, ensordecer, mover a otro canal, expulsar y banear, por texto o por voz. |
| 🔔 Wake word | Activás los comandos por voz diciendo "señor gabriel, ..." (configurable). |

---

## Requisitos

- Python 3.12+
- [ffmpeg](https://ffmpeg.org/) en el PATH (instalable en Windows con `winget install Gyan.FFmpeg`)
- GPU NVIDIA con driver CUDA (opcional; si no tenés, usá `WHISPER_DEVICE=cpu`)
- Token de un bot de Discord

### Intents requeridos en el Developer Portal

En la configuración del bot (https://discord.com/developers/applications), dentro de **Bot**, activar:

- `PRESENCE INTENT`
- `SERVER MEMBERS INTENT`
- `MESSAGE CONTENT INTENT`

Y otorgarle al rol del bot los permisos: `MOVE_MEMBERS`, `MUTE_MEMBERS`, `DEAFEN_MEMBERS`, `KICK_MEMBERS`, `BAN_MEMBERS`, `CONNECT`, `SPEAK`, `USE_VOICE_ACTIVITY`, `SEND_MESSAGES`.

---

## Instalación

```powershell
# 1. Clonar
git clone https://github.com/Juan-q18/Gabiescucha.git
cd Gabiescucha

# 2. Dependencias
pip install -r requirements.txt

# 3. Configuración
Copy-Item .env.example .env
# Editar .env y pegar el DISCORD_BOT_TOKEN
```

Variables de `.env`:

| Variable | Default | Descripción |
| --- | --- | --- |
| `DISCORD_BOT_TOKEN` | — | Token del bot (obligatorio). |
| `WAKE_WORD` | `señor gabriel` | Frase que activa los comandos por voz. |
| `WHISPER_MODEL` | `small` | Modelo de faster-whisper (tiny/base/small/medium/large-v3). |
| `MUSIC_MODEL` | `medium` | Modelo para re-transcribir **solo** las consultas de música (mejor precisión en títulos). Vacío = deshabilitado (se usa `WHISPER_MODEL` para todo). |
| `WHISPER_DEVICE` | `cuda` | `cuda` si hay GPU NVIDIA, si no `cpu`. |
| `WHISPER_COMPUTE_TYPE` | `int8` | Precisión de cómputo (int8/int8_float16/float16). |
| `WHISPER_LANGUAGE` | `es` | Idioma de la transcripción. |
| `WHISPER_INITIAL_PROMPT` | *(frase de comandos)* | Frase de ejemplo que inclina a whisper hacia los comandos del bot (mejora el parseo). |
| `NO_SPEECH_THRESHOLD` | `-1.0` | Confianza mínima del segmento (avg_logprob); solo se descarta si además `no_speech_prob` supera el umbral siguiente. |
| `NO_SPEECH_PROB_THRESHOLD` | `0.6` | Probabilidad de "no voz" de whisper para descartar un segmento. |
| `BEAM_SIZE` | `5` | Beam de whisper (`1` = más rápido, menos preciso). |
| `SILENCE_DURATION` | `2.0` | Segundos de silencio para considerar terminado un segmento de voz (más bajo = respuesta más rápida, riesgo de partir frases). |
| `DRAIN_INTERVAL` | `1.0` | Cada cuántos segundos se transcriben los segmentos pendientes. |
| `SAVE_SEGMENTS` | `0` | Guarda cada segmento capturado como WAV en `SEGMENTS_DIR` (diagnóstico). |
| `SEGMENTS_DIR` | `segments` | Carpeta donde se guardan los segmentos con `SAVE_SEGMENTS=1`. |
| `TTS_VOICE` | `es-AR-ElenaNeural` | Voz de edge-tts. |
| `TTS_RATE` | `+0%` | Velocidad del TTS (ej. `+10%`, `-10%`). |
| `IGNORE_BOT_AUDIO` | `1` | Ignora el audio de otros bots (ej. otro bot de música). |
| `MUSIC_VOLUME` | `0.5` | Volumen inicial de la música (0 a 2). |
| `YDL_SLEEP_REQUESTS` | `0.5` | Pausa entre requests de yt-dlp (mitiga throttling). |

> El primer arranque descarga el modelo de whisper (≈485 MB para `small`) y el VAD de Silero.

---

## Uso

```powershell
python main.py
```

En Discord: `!listen` para que entre al canal de voz donde estás, y `!ayuda` para ver los comandos.

### Comandos de texto

**Música**
- `!play <tema o URL>` — busca en YouTube y reproduce (ej. `!play despacito luis fonsi`)
- `!skip` — siguiente tema
- `!pause` / `!resume` — pausar / reanudar
- `!queue` — ver la cola
- `!volume <0-2>` — cambiar volumen (sin argumento muestra el actual)
- `!shuffle` — mezclar la cola
- `!loop` — repetir el tema actual

**TTS**
- `!decir <texto>` — el bot dice el texto en el canal de voz

**Moderación**
- `!mutear @usuario`
- `!desmutear @usuario`
- `!sordear @usuario` — lo ensordece (deafen)
- `!desordear @usuario`
- `!mover @usuario #canal`
- `!expulsar @usuario [razón]`
- `!banear @usuario [razón]`

**Escucha**
- `!listen` — conectarse al canal de voz del autor y empezar a escuchar
- `!salir` — desconectarse

### Comandos por voz

Decí el wake word (por defecto "señor gabriel") seguido de la orden:

- `señor gabriel, poné <tema>` / `reproducí <tema>` / `música <tema>` — reproduce música
- `señor gabriel, pausá la música` / `seguí` — pausar / reanudar
- `señor gabriel, siguiente canción` — saltea el tema
- `señor gabriel, subí/bajá el volumen` — volumen ±10%
- `señor gabriel, decí <texto>` — TTS
- `señor gabriel, escribí en el chat <texto>` — postea el texto en el canal
- `señor gabriel, muteá a <nombre>` / `desmutear a <nombre>`
- `señor gabriel, sordeá a <nombre>` / `desordea a <nombre>`
- `señor gabriel, expulsá a <nombre>` / `banéa a <nombre>`
- `señor gabriel, mové a <nombre> a <canal>` / `moveme a <canal>`
- `señor gabriel, qué podés hacer` / `ayuda` — lista de comandos

Los nombres se resuelven con coincidencia aproximada sobre el nick/nombre (ej. "muteá a juan" funciona si hay "Juan Pérez").

---

## Estructura del proyecto

```
main.py          # bot: comandos, dispatch de voz, ciclo de vida
dave_patch.py    # monkey-patch: descifra DAVE (E2EE) en la voz recibida
listener.py      # SpeechSink: VAD por energía + segmentación por usuario
transcriber.py   # WhisperTranscriber local (faster-whisper, CPU/CUDA)
_cuda.py         # expone los DLL de nvidia al loader en Windows
intents.py       # parser de intenciones por reglas (comandos de voz)
music.py         # reproductor con cola (yt-dlp + ffmpeg)
tts.py           # síntesis de voz con edge-tts + reproducción
moderation.py    # acciones de moderación + resolución de miembros/canales
config.py        # configuración desde .env
```

---

## Notas

- La transcripción es 100% local: el audio nunca sale de tu PC.
- La música de otros bots (ej. m!p) se ignora automáticamente para no contaminar la transcripción (`IGNORE_BOT_AUDIO=1`).
- El TTS usa edge-tts: necesita internet para generar el audio; la reproducción es local.
- El comando por voz para música conecta automáticamente al bot al canal si no está conectado.
