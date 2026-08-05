import asyncio
import logging
import os
import tempfile
from typing import Callable, Optional

import discord
import edge_tts

import config

log = logging.getLogger(__name__)


async def generate(text: str, voice: str = config.TTS_VOICE, rate: str = config.TTS_RATE) -> bytes:
    communicate = edge_tts.Communicate(text, voice, rate=rate)
    buf = bytearray()
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            buf.extend(chunk["data"])
    return bytes(buf)


async def speak(
    vc: discord.VoiceClient,
    text: str,
    *,
    pause_cb: Optional[Callable[[], bool]] = None,
    resume_cb: Optional[Callable[[], None]] = None,
) -> None:
    """Genera el audio con edge-tts y lo reproduce, pausando la música si hace falta."""
    audio = await generate(text)
    if not audio:
        log.warning("TTS no generó audio")
        return

    fd, path = tempfile.mkstemp(suffix=".mp3")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(audio)
        if pause_cb:
            pause_cb()
        vc.play(discord.FFmpegPCMAudio(path, options="-vn"))
        while vc.is_playing() or vc.is_paused():
            await asyncio.sleep(0.5)
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
        if resume_cb:
            resume_cb()
