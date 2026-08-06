import asyncio
import logging
import os
import re
import threading
import time
import unicodedata
from typing import Optional

import discord
from discord.ext import commands, voice_recv

import config
import dave_patch
import listener as listener_mod
import moderation as mod
import tts as tts_mod
from intents import parse as parse_intent
from listener import SpeechSink
from music import MusicPlayer
from transcriber import WhisperTranscriber

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger(__name__)

logging.getLogger("discord.ext.voice_recv").setLevel(logging.WARNING)
logging.getLogger("discord.gateway").setLevel(logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("faster_whisper").setLevel(logging.INFO)

dave_patch.apply_dave_patch()



intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

transcriber = WhisperTranscriber(
    config.WHISPER_MODEL,
    config.WHISPER_DEVICE,
    config.WHISPER_COMPUTE_TYPE,
    config.WHISPER_LANGUAGE,
    config.NO_SPEECH_THRESHOLD,
    config.NO_SPEECH_PROB_THRESHOLD,
    config.WHISPER_INITIAL_PROMPT,
    config.BEAM_SIZE,
)
music_transcriber = None
if config.MUSIC_MODEL:
    music_transcriber = WhisperTranscriber(
        config.MUSIC_MODEL,
        config.WHISPER_DEVICE,
        config.WHISPER_COMPUTE_TYPE,
        config.WHISPER_LANGUAGE,
        config.NO_SPEECH_THRESHOLD,
        config.NO_SPEECH_PROB_THRESHOLD,
        config.WHISPER_INITIAL_PROMPT,
        config.BEAM_SIZE,
    )
wake_word = config.WAKE_WORD

home_channels: dict = {}
players: dict = {}

_pending_lock = threading.Lock()
pending: dict = {}
DRAIN_INTERVAL = config.DRAIN_INTERVAL
_worker_started = False

HELP_TEXT = (
    "**Gabiescucha**\n"
    "**Música:** `!play <tema>` `!skip` `!pause` `!resume` `!queue` `!volume` `!shuffle` `!loop`\n"
    "**Voz:** `!decir <texto>`\n"
    "**Moderación:** `!mutear @user` `!desmutear @user` `!sordear @user` `!desordear @user` "
    "`!mover @user #canal` `!expulsar @user` `!banear @user`\n"
    "**Voz directa:** decime *\"señor gabriel, poné <tema>\"*, *\"decí <texto>\"*, "
    "*\"escribí en el chat <texto>\"*, *\"muteá a <nombre>\"*, *\"mové a <nombre> a <canal>\"*, "
    "*\"pausá la música\"*, *\"seguí\"*, *\"siguiente canción\"*, *\"subí/bajá el volumen\"*.\n"
    "**Escucha:** `!listen` / `!salir`"
)


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFD", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return text.lower()


def _find_wake(text: str) -> Optional[int]:
    """Busca el wake word con limites de palabra y devuelve el indice crudo
    donde termina (para cortar la frase cruda). None si no aparece."""
    nwake = _normalize(wake_word)
    if not nwake or not text:
        return None

    # Normalizar char a char para poder mapear indices crudos (NFD puede
    # partir un caracter en varios, ej. 'ñ' -> 'n' + tilde combinante).
    norm_chars: list = []
    for i, c in enumerate(text):
        cc = unicodedata.normalize("NFD", c)
        cc = "".join(x for x in cc if unicodedata.category(x) != "Mn").lower()
        if cc:
            norm_chars.append((i, cc))
    if len(norm_chars) < len(nwake):
        return None

    flat = "".join(cc for _, cc in norm_chars)
    m = re.search(r"(?<!\w)" + re.escape(nwake) + r"(?!\w)", flat)
    if not m:
        return None
    return norm_chars[m.end() - 1][0] + 1


async def segment_worker():
    while True:
        await asyncio.sleep(DRAIN_INTERVAL)
        with _pending_lock:
            batch = list(pending.values())
            pending.clear()
        for user, pcm in batch:
            try:
                await process_segment(user, pcm)
            except Exception:
                log.exception("Error procesando segmento de voz")


CLEANUP_INTERVAL = 3600


def _cleanup_old_segments() -> int:
    """Borra los WAVs de segmentos más viejos que SEGMENTS_MAX_AGE_HOURS.
    Devuelve cuántos borró."""
    removed = 0
    if not os.path.isdir(config.SEGMENTS_DIR):
        return 0
    cutoff = time.time() - config.SEGMENTS_MAX_AGE_HOURS * 3600
    for name in os.listdir(config.SEGMENTS_DIR):
        if not name.lower().endswith(".wav"):
            continue
        path = os.path.join(config.SEGMENTS_DIR, name)
        try:
            if os.path.getmtime(path) < cutoff:
                os.remove(path)
                removed += 1
        except OSError:
            log.exception("Error borrando segmento %s", path)
    return removed


async def segment_cleanup_worker():
    """Borra los WAVs de segmentos más viejos que SEGMENTS_MAX_AGE_HOURS."""
    while True:
        try:
            removed = await asyncio.to_thread(_cleanup_old_segments)
            if removed:
                log.info(
                    "Limpieza de segmentos: %d WAV(s) borrados (> %dh)",
                    removed,
                    config.SEGMENTS_MAX_AGE_HOURS,
                )
        except Exception:
            log.exception("Error en limpieza de segmentos")
        await asyncio.sleep(CLEANUP_INTERVAL)


async def reply(guild, text: str) -> None:
    cid = home_channels.get(guild.id)
    channel = bot.get_channel(cid) if cid else None
    if channel:
        try:
            await channel.send(text)
        except Exception:
            log.exception("Error enviando respuesta al canal de casa")


def _check_voice_perms(channel, me) -> None:
    """Valida permisos y capacidad del canal antes de intentar conectar (evita timeouts de 30s)."""
    perms = channel.permissions_for(me)
    if not perms.connect:
        raise ValueError("El bot no tiene permiso **Conectar** en ese canal de voz.")
    if not perms.speak:
        log.warning("El bot no tiene permiso **Hablar** en %s (la música/TTS no se oirá)", channel.name)
    if getattr(channel, "user_limit", 0) and len(channel.members) >= channel.user_limit:
        if not perms.move_members:
            raise ValueError(
                "El canal de voz está **lleno** y el bot no tiene permiso **Mover Miembros**."
            )


async def connect_and_listen(channel, guild, home_channel_id, attempts: int = 2) -> discord.VoiceClient:
    _check_voice_perms(channel, guild.me)
    last_exc = None
    for attempt in range(attempts):
        try:
            vc = await channel.connect(cls=voice_recv.VoiceRecvClient, timeout=20)
            break
        except (asyncio.TimeoutError, Exception) as exc:
            last_exc = exc
            log.warning("Intento %d/%d de conexión de voz falló: %s", attempt + 1, attempts, exc)
            if attempt + 1 < attempts:
                await asyncio.sleep(4)
    else:
        raise last_exc
    sink = SpeechSink(
        on_segment=on_segment,
        ignore_bots=config.IGNORE_BOT_AUDIO,
        silence_duration=config.SILENCE_DURATION,
    )
    vc.listen(sink)
    home_channels[guild.id] = home_channel_id
    return vc


async def ensure_connected(guild, member) -> discord.VoiceClient:
    vc = discord.utils.get(bot.voice_clients, guild=guild)
    if vc is not None:
        return vc
    if not member.voice or not member.voice.channel:
        raise ValueError("No estás conectado a un canal de voz.")
    return await connect_and_listen(member.voice.channel, guild, home_channels.get(guild.id))


def get_player(vc) -> MusicPlayer:
    player = players.get(vc.guild.id)
    if player is None:
        player = MusicPlayer(vc, bot.loop)
        players[vc.guild.id] = player
    return player


def _save_segment(user, pcm) -> None:
    try:
        os.makedirs(config.SEGMENTS_DIR, exist_ok=True)
        ts = time.strftime("%Y%m%d-%H%M%S")
        name = getattr(user, "display_name", "desconocido")
        safe = "".join(c for c in name if c.isalnum() or c in " _-")[:24] or "desconocido"
        path = os.path.join(config.SEGMENTS_DIR, f"{ts}_{safe}.wav")
        with open(path, "wb") as f:
            f.write(transcriber.pcm_to_wav(pcm))
    except Exception:
        log.exception("Error guardando segmento de audio")


def on_segment(user, pcm):
    seconds = len(pcm) / listener_mod.BYTES_PER_SECOND
    log.info("Segmento de voz de %s (%.2fs)", getattr(user, "display_name", user), seconds)
    if config.SAVE_SEGMENTS:
        _save_segment(user, pcm)
    with _pending_lock:
        pending[user.id] = (user, pcm)


async def process_segment(user, pcm):
    if getattr(user, "bot", False):
        return

    seconds = len(pcm) / listener_mod.BYTES_PER_SECOND
    if seconds < 0.5:
        log.info("Segmento de %s (%.2fs) descartado por corto", user.display_name, seconds)
        return

    text = await asyncio.to_thread(transcriber.transcribe, pcm)
    if not text:
        log.info("Transcripción de %s: sin voz clara (descartado)", user.display_name)
        return

    log.info("Transcripción de %s: %s", user.display_name, text)

    if _find_wake(text) is not None:
        await dispatch_voice_command(user, text, pcm)


async def _refine_music_query(text: str, pcm) -> str:
    """Re-transcribe el audio con el modelo de música (mejor precisión en
    títulos) y devuelve el texto refinado. Fallback: el texto original."""
    if music_transcriber is None:
        return text
    try:
        refined = await asyncio.to_thread(music_transcriber.transcribe, pcm)
        if refined and _find_wake(refined) is not None:
            log.info(
                "Música (re-transcripción %s): %s",
                config.MUSIC_MODEL,
                refined,
            )
            return refined
    except Exception:
        log.exception("Error re-transcribiendo la consulta de música")
    return text


async def dispatch_voice_command(user, text: str, pcm=None) -> None:
    guild = user.guild
    raw_end = _find_wake(text)
    if raw_end is None:
        return
    phrase = text[raw_end:]

    cmd = parse_intent(phrase)
    if cmd.action == "none":
        return

    member = guild.get_member(user.id)
    if member is None:
        return

    if cmd.action == "music" and pcm is not None:
        refined_text = await _refine_music_query(text, pcm)
        if refined_text != text:
            r_end = _find_wake(refined_text)
            if r_end is not None:
                r_cmd = parse_intent(refined_text[r_end:])
                if r_cmd.action == "music" and r_cmd.text:
                    cmd = r_cmd

    log.info("Comando por voz de %s: %s %s", user.display_name, cmd.action, cmd.args)

    if cmd.action == "music":
        if config.MUSIC_PROXY:
            await music_proxy_play_command(guild, cmd.text)
        else:
            await music_command(guild, member, cmd.text, requester=user.display_name)
    elif cmd.action == "music_pause":
        if config.MUSIC_PROXY:
            await proxy_send_command(guild, config.PROXY_PAUSE_COMMAND)
        else:
            await music_pause_command(guild)
    elif cmd.action == "music_resume":
        if config.MUSIC_PROXY:
            await proxy_send_command(guild, config.PROXY_RESUME_COMMAND)
        else:
            await music_resume_command(guild)
    elif cmd.action == "music_skip":
        if config.MUSIC_PROXY:
            await proxy_send_command(guild, config.PROXY_SKIP_COMMAND)
        else:
            await music_skip_command(guild)
    elif cmd.action == "music_volume_up":
        await music_volume_command(guild, "up")
    elif cmd.action == "music_volume_down":
        await music_volume_command(guild, "down")
    elif cmd.action == "help":
        await reply(guild, HELP_TEXT)
    elif cmd.action == "chat":
        await reply(guild, cmd.text)
    elif cmd.action == "tts":
        await tts_command(guild, member, cmd.text)
    elif cmd.action.startswith("mod_"):
        await mod_voice_command(guild, member, cmd, pcm)


proxy_volume: dict[int, int] = {}


async def proxy_send_command(guild, command: str) -> None:
    """Escribe un comando del bot de música externo en el canal de casa."""
    await reply(guild, command)


async def music_proxy_play_command(guild, query):
    query = (query or "").strip()
    if not query or query.lower() in ("este tema", "ese tema", "el tema", "esta canción", "esa canción"):
        await reply(guild, "¿Qué tema querés que ponga? Decime el nombre.")
        return
    await proxy_send_command(guild, f"{config.PROXY_PLAY_COMMAND} {query}")


async def music_command(guild, member, query, requester=None):
    try:
        vc = await ensure_connected(guild, member)
    except Exception as exc:
        await reply(guild, str(exc))
        return

    player = get_player(vc)
    await reply(guild, f"⏳ Buscando **{query}**...")
    try:
        track = await player.add(query, requester=requester)
    except ValueError as exc:
        await reply(guild, str(exc))
        return

    if not player.is_playing and not player.is_paused:
        player.play_next()
        await reply(guild, f"▶ Reproduciendo: **{player.current.title}**")
    else:
        await reply(guild, f"Agregado a la cola: **{track.title}**")


async def music_pause_command(guild):
    player = players.get(guild.id)
    if player is None or not player.is_playing:
        await reply(guild, "No hay música reproduciéndose.")
        return
    player.pause()
    await reply(guild, "⏸ Música pausada.")


async def music_resume_command(guild):
    player = players.get(guild.id)
    if player is None or not player.is_paused:
        await reply(guild, "No hay música pausada.")
        return
    player.resume()
    await reply(guild, "▶ Música reanudada.")


async def music_skip_command(guild):
    player = players.get(guild.id)
    if player is None or not (player.is_playing or player.is_paused):
        await reply(guild, "No hay nada reproduciéndose.")
        return
    player.skip()
    player.play_next()
    await reply(guild, "⏭ Siguiente tema.")


async def music_volume_command(guild, direction: str):
    if config.MUSIC_PROXY:
        gid = guild.id
        vol = proxy_volume.get(gid, 100)
        vol += config.PROXY_VOLUME_STEP if direction == "up" else -config.PROXY_VOLUME_STEP
        vol = max(config.PROXY_VOLUME_MIN, min(config.PROXY_VOLUME_MAX, vol))
        proxy_volume[gid] = vol
        await proxy_send_command(guild, f"{config.PROXY_VOLUME_COMMAND} {vol}")
        return
    player = players.get(guild.id)
    if player is None or not (player.is_playing or player.is_paused):
        await reply(guild, "No hay música reproduciéndose.")
        return
    step = 0.1
    new_volume = player.volume + step if direction == "up" else player.volume - step
    player.set_volume(max(0.0, min(2.0, round(new_volume, 2))))
    await reply(guild, f"🔊 Volumen: **{int(player.volume * 100)}%**")


async def tts_command(guild, member, text):
    try:
        vc = await ensure_connected(guild, member)
    except Exception as exc:
        await reply(guild, str(exc))
        return
    player = players.get(guild.id)
    try:
        await tts_mod.speak(
            vc,
            text,
            pause_cb=player.pause_for_tts if player else None,
            resume_cb=player.resume_after_tts if player else None,
        )
    except Exception:
        log.exception("Error generando/reproduciendo TTS")
        await reply(guild, "No pude generar el audio (¿hay internet?).")
        return
    await reply(guild, f"🗣️ {text}")


async def mod_voice_command(guild, actor, cmd, pcm=None):
    err = await _run_mod_command(guild, actor, cmd)
    if err is None:
        return
    if pcm is not None and music_transcriber is not None:
        await reply(guild, "⏳ No entendí bien, dejame escuchar de nuevo...")
        try:
            refined = await asyncio.to_thread(music_transcriber.transcribe, pcm)
        except Exception:
            log.exception("Error re-transcribiendo comando de moderación")
            await reply(guild, err)
            return
        if refined and _find_wake(refined) is not None:
            r_cmd = parse_intent(refined[_find_wake(refined):])
            if r_cmd.action.startswith("mod_"):
                log.info(
                    "Moderación (re-transcripción %s): %s %s",
                    config.MUSIC_MODEL,
                    r_cmd.action,
                    r_cmd.args,
                )
                if await _run_mod_command(guild, actor, r_cmd) is None:
                    return
    await reply(guild, err)


async def _run_mod_command(guild, actor, cmd) -> Optional[str]:
    """Ejecuta un comando de moderación. Devuelve un mensaje de error si no se
    pudo resolver el target/canal (para reintentar con otro modelo); None si se
    ejecutó o si ya se respondió un error de permisos."""
    if cmd.action == "mod_move":
        channel = mod.resolve_voice_channel(guild, cmd.channel)
        if channel is None:
            return f"No encontré el canal **{cmd.channel}**."
        try:
            mod.require_author_perm(actor, "move_members")
            mod.require_bot_perm(guild, "move_members")
            await mod.move_to_channel(actor, channel)
            await reply(guild, f"🚶 **{actor.display_name}** movido a **{channel.name}**.")
        except mod.ModerationError as exc:
            await reply(guild, str(exc))
        return None

    target_name = cmd.target or ""
    member = mod.resolve_member(guild, target_name)
    if member is None:
        return f"No encontré a **{target_name}**."

    try:
        if cmd.action in ("mod_mute", "mod_unmute"):
            mod.require_author_perm(actor, "mute_members")
            mod.require_bot_perm(guild, "mute_members")
            mute = cmd.action == "mod_mute"
            await mod.set_mute(member, mute)
            verb = "muteado" if mute else "desmuteado"
            await reply(guild, f"{'🔇' if mute else '🔊'} **{member.display_name}** {verb}.")
        elif cmd.action in ("mod_deafen", "mod_undeafen"):
            mod.require_author_perm(actor, "deafen_members")
            mod.require_bot_perm(guild, "deafen_members")
            deafen = cmd.action == "mod_deafen"
            await mod.set_deafen(member, deafen)
            verb = "ensordecido" if deafen else "desensordecido"
            await reply(guild, f"{'🙉' if deafen else '🙂'} **{member.display_name}** {verb}.")
        elif cmd.action == "mod_move_target":
            channel = mod.resolve_voice_channel(guild, cmd.channel)
            if channel is None:
                return f"No encontré el canal **{cmd.channel}**."
            mod.require_author_perm(actor, "move_members")
            mod.require_bot_perm(guild, "move_members")
            await mod.move_to_channel(member, channel)
            await reply(guild, f"🚶 **{member.display_name}** movido a **{channel.name}**.")
        elif cmd.action == "mod_kick":
            mod.require_author_perm(actor, "kick_members")
            mod.require_bot_perm(guild, "kick_members")
            name = member.display_name
            await mod.kick(member)
            await reply(guild, f"👢 **{name}** expulsado del servidor.")
        elif cmd.action == "mod_ban":
            mod.require_author_perm(actor, "ban_members")
            mod.require_bot_perm(guild, "ban_members")
            name = member.display_name
            await mod.ban(member)
            await reply(guild, f"⛔ **{name}** baneado.")
    except mod.ModerationError as exc:
        await reply(guild, str(exc))
    except Exception as exc:
        log.exception("Error en comando de moderación por voz")
        await reply(guild, f"Error: {exc}")
    return None


@bot.event
async def on_ready():
    global _worker_started
    log.info("Bot conectado como %s (id=%s)", bot.user, bot.user.id)
    if not _worker_started:
        _worker_started = True
        bot.loop.create_task(segment_worker())
        bot.loop.create_task(segment_cleanup_worker())
    # Precargar los modelos para que el primer comando no sufra la demora de carga.
    asyncio.get_running_loop().run_in_executor(None, transcriber.load)
    if music_transcriber is not None:
        asyncio.get_running_loop().run_in_executor(None, music_transcriber.load)
    await bot.change_presence(
        activity=discord.Activity(type=discord.ActivityType.listening, name="la voz del canal")
    )


@bot.command(name="listen")
async def listen(ctx):
    """Entra al canal de voz del autor y empieza a escuchar."""
    if not ctx.author.voice or not ctx.author.voice.channel:
        await ctx.send("No estás conectado a un canal de voz.")
        return

    vc = discord.utils.get(bot.voice_clients, guild=ctx.guild)
    if vc is not None:
        await ctx.send("Ya estoy conectado a un canal de voz.")
        return

    channel = ctx.author.voice.channel
    try:
        vc = await connect_and_listen(channel, ctx.guild, ctx.channel.id)
    except Exception as exc:
        log.exception("Error al conectar al canal de voz")
        await ctx.send(f"Error al conectarme al canal de voz: {exc}")
        return

    await ctx.send(f"Escuchando en **{channel.name}**. Esperando actividad de voz...")


@bot.command(name="salir", aliases=["unlisten", "stop"])
async def salir(ctx):
    vc = discord.utils.get(bot.voice_clients, guild=ctx.guild)
    if vc is None:
        await ctx.send("No estoy conectado a ningún canal de voz.")
        return
    player = players.pop(ctx.guild.id, None)
    if player:
        player.stop()
    if vc.is_listening():
        vc.stop_listening()
    await vc.disconnect()
    await ctx.send("Me desconecté del canal de voz.")


@bot.command(name="play", aliases=["poner", "reproducir"])
async def play(ctx, *, query):
    """Reproduce un tema (búsqueda o URL) en el canal de voz del autor."""
    if not ctx.author.voice or not ctx.author.voice.channel:
        await ctx.send("No estás conectado a un canal de voz.")
        return
    vc = discord.utils.get(bot.voice_clients, guild=ctx.guild)
    if vc is None:
        try:
            vc = await connect_and_listen(ctx.author.voice.channel, ctx.guild, ctx.channel.id)
        except Exception as exc:
            await ctx.send(f"Error al conectar: {exc}")
            return

    player = get_player(vc)
    try:
        track = await player.add(query, requester=ctx.author.display_name)
    except ValueError as exc:
        await ctx.send(str(exc))
        return

    if not player.is_playing and not player.is_paused:
        player.play_next()
        await ctx.send(f"▶ Reproduciendo: **{player.current.title}**")
    else:
        await ctx.send(f"Agregado a la cola: **{track.title}**")


@bot.command(name="skip", aliases=["siguiente"])
async def skip(ctx):
    player = players.get(ctx.guild.id)
    if player is None or not (player.is_playing or player.is_paused):
        await ctx.send("No hay nada reproduciéndose.")
        return
    player.skip()
    player.play_next()
    await ctx.send("⏭ Siguiente tema.")


@bot.command(name="pause", aliases=["pausar"])
async def pause(ctx):
    player = players.get(ctx.guild.id)
    if player is None or not player.is_playing:
        await ctx.send("No hay nada reproduciéndose.")
        return
    player.pause()
    await ctx.send("⏸ Pausado.")


@bot.command(name="resume", aliases=["reanudar"])
async def resume(ctx):
    player = players.get(ctx.guild.id)
    if player is None or not player.is_paused:
        await ctx.send("No hay nada pausado.")
        return
    player.resume()
    await ctx.send("▶ Reanudado.")


@bot.command(name="queue", aliases=["cola"])
async def queue(ctx):
    player = players.get(ctx.guild.id)
    if player is None or not (player.current or player.queue):
        await ctx.send("Cola vacía.")
        return
    await ctx.send(player.queue_status())


@bot.command(name="volume", aliases=["volumen"])
async def volume(ctx, value: float = None):
    player = players.get(ctx.guild.id)
    if player is None:
        await ctx.send("No hay reproducción activa.")
        return
    if value is None:
        await ctx.send(f"Volumen actual: **{int(player.volume * 100)}%**")
        return
    player.set_volume(value)
    await ctx.send(f"🔊 Volumen: **{int(player.volume * 100)}%**")


@bot.command(name="shuffle", aliases=["mezclar"])
async def shuffle(ctx):
    player = players.get(ctx.guild.id)
    if player is None or not player.queue:
        await ctx.send("No hay cola para mezclar.")
        return
    player.shuffle()
    await ctx.send("🔀 Cola mezclada.")


@bot.command(name="loop", aliases=["repetir"])
async def loop(ctx):
    player = players.get(ctx.guild.id)
    if player is None:
        await ctx.send("No hay reproducción activa.")
        return
    enabled = player.toggle_loop()
    await ctx.send("🔁 Repetir tema: **activado**" if enabled else "🔁 Repetir tema: **desactivado**")


@bot.command(name="decir", aliases=["decime", "habla"])
async def decir(ctx, *, text):
    """El bot dice el texto en el canal de voz (TTS)."""
    if not ctx.author.voice or not ctx.author.voice.channel:
        await ctx.send("No estás conectado a un canal de voz.")
        return
    vc = discord.utils.get(bot.voice_clients, guild=ctx.guild)
    if vc is None:
        try:
            vc = await connect_and_listen(ctx.author.voice.channel, ctx.guild, ctx.channel.id)
        except Exception as exc:
            await ctx.send(f"Error al conectar: {exc}")
            return
    player = players.get(ctx.guild.id)
    await tts_mod.speak(
        vc,
        text,
        pause_cb=player.pause_for_tts if player else None,
        resume_cb=player.resume_after_tts if player else None,
    )


@bot.command(name="mutear")
async def mutear(ctx, member: discord.Member):
    try:
        mod.require_author_perm(ctx.author, "mute_members")
        mod.require_bot_perm(ctx.guild, "mute_members")
        await mod.set_mute(member, True)
        await ctx.send(f"🔇 **{member.display_name}** muteado.")
    except mod.ModerationError as exc:
        await ctx.send(str(exc))


@bot.command(name="desmutear")
async def desmutear(ctx, member: discord.Member):
    try:
        mod.require_author_perm(ctx.author, "mute_members")
        mod.require_bot_perm(ctx.guild, "mute_members")
        await mod.set_mute(member, False)
        await ctx.send(f"🔊 **{member.display_name}** desmuteado.")
    except mod.ModerationError as exc:
        await ctx.send(str(exc))


@bot.command(name="sordear")
async def sordear(ctx, member: discord.Member):
    try:
        mod.require_author_perm(ctx.author, "deafen_members")
        mod.require_bot_perm(ctx.guild, "deafen_members")
        await mod.set_deafen(member, True)
        await ctx.send(f"🙉 **{member.display_name}** ensordecido.")
    except mod.ModerationError as exc:
        await ctx.send(str(exc))


@bot.command(name="desordear")
async def desordear(ctx, member: discord.Member):
    try:
        mod.require_author_perm(ctx.author, "deafen_members")
        mod.require_bot_perm(ctx.guild, "deafen_members")
        await mod.set_deafen(member, False)
        await ctx.send(f"🙂 **{member.display_name}** desensordecido.")
    except mod.ModerationError as exc:
        await ctx.send(str(exc))


@bot.command(name="mover")
async def mover(ctx, member: discord.Member, channel: discord.VoiceChannel):
    try:
        mod.require_author_perm(ctx.author, "move_members")
        mod.require_bot_perm(ctx.guild, "move_members")
        await mod.move_to_channel(member, channel)
        await ctx.send(f"🚶 **{member.display_name}** movido a **{channel.name}**.")
    except mod.ModerationError as exc:
        await ctx.send(str(exc))


@bot.command(name="expulsar")
async def expulsar(ctx, member: discord.Member, *, reason: str = ""):
    try:
        mod.require_author_perm(ctx.author, "kick_members")
        mod.require_bot_perm(ctx.guild, "kick_members")
        await mod.kick(member, reason=reason)
        await ctx.send(f"👢 **{member.display_name}** expulsado del servidor.")
    except mod.ModerationError as exc:
        await ctx.send(str(exc))


@bot.command(name="banear")
async def banear(ctx, member: discord.Member, *, reason: str = ""):
    try:
        mod.require_author_perm(ctx.author, "ban_members")
        mod.require_bot_perm(ctx.guild, "ban_members")
        await mod.ban(member, reason=reason)
        await ctx.send(f"⛔ **{member.display_name}** baneado.")
    except mod.ModerationError as exc:
        await ctx.send(str(exc))


@bot.command(name="ayuda")
async def ayuda(ctx):
    await ctx.send(HELP_TEXT)


def main():
    if not config.BOT_TOKEN:
        log.error("Falta DISCORD_BOT_TOKEN en el archivo .env")
        return
    bot.run(config.BOT_TOKEN)


if __name__ == "__main__":
    main()
