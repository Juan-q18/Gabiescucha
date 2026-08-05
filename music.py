import asyncio
import logging
import random
from dataclasses import dataclass, field
from typing import Optional

import discord
import yt_dlp

import config

log = logging.getLogger(__name__)

YDL_OPTS = {
    "format": "bestaudio[ext=m4a]/bestaudio/best",
    "noplaylist": True,
    "quiet": True,
    "no_warnings": True,
    "skip_download": True,
    "socket_timeout": 15,
    "http_headers": {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        )
    },
}

FFMPEG_BEFORE_OPTS = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"


@dataclass
class Track:
    title: str
    url: str
    duration: Optional[float] = None
    webpage_url: str = ""
    requester: Optional[str] = None


def _search(query: str) -> Optional[Track]:
    """Busca un tema (o usa una URL) y devuelve el Track resuelto."""
    q = query if query.startswith(("http://", "https://")) else f"ytsearch1:{query}"
    with yt_dlp.YoutubeDL(YDL_OPTS) as ydl:
        info = ydl.extract_info(q, download=False)
    if info is None:
        return None
    if "entries" in info and info["entries"]:
        info = info["entries"][0]
    if not info or "url" not in info:
        return None
    return Track(
        title=info.get("title") or "desconocido",
        url=info["url"],
        duration=info.get("duration"),
        webpage_url=info.get("webpage_url") or info.get("original_url") or "",
    )


def _format_duration(seconds: Optional[float]) -> str:
    if not seconds:
        return "??:??"
    seconds = int(seconds)
    return f"{seconds // 60}:{seconds % 60:02d}"


class MusicPlayer:
    def __init__(self, vc: discord.VoiceClient, loop: asyncio.AbstractEventLoop):
        self.vc = vc
        self.loop = loop
        self.queue: list[Track] = []
        self.current: Optional[Track] = None
        self.volume = config.MUSIC_VOLUME
        self.loop_current = False
        self.paused = False

    @property
    def is_playing(self) -> bool:
        return self.vc.is_playing()

    async def add(self, query: str, requester: Optional[str] = None) -> Track:
        track = await asyncio.to_thread(_search, query)
        if track is None:
            raise ValueError("No encontré ese tema.")
        track.requester = requester
        self.queue.append(track)
        return track

    def _source(self) -> discord.AudioSource:
        source = discord.FFmpegPCMAudio(
            self.current.url,
            before_options=FFMPEG_BEFORE_OPTS,
            options="-vn",
        )
        return discord.PCMVolumeTransformer(source, volume=self.volume)

    def play_next(self) -> None:
        if self.vc is None or not self.vc.is_connected():
            return
        if self.vc.is_playing() or self.vc.is_paused():
            return

        if self.loop_current and self.current is not None:
            self.queue.insert(0, self.current)
        if not self.queue:
            self.current = None
            return

        self.current = self.queue.pop(0)
        self.paused = False
        source = self._source()
        log.info("Reproduciendo: %s", self.current.title)
        self.vc.play(source, after=self._on_track_end)

    def _on_track_end(self, error: Optional[Exception]) -> None:
        if error:
            log.warning("Error en la reproducción: %s", error)
        self.loop.call_soon_threadsafe(self.play_next)

    def skip(self) -> Optional[Track]:
        skipped = self.current
        if self.vc.is_playing() or self.vc.is_paused():
            self.vc.stop()
        self.current = None
        return skipped

    def pause(self) -> None:
        if self.vc.is_playing():
            self.vc.pause()
            self.paused = True

    def resume(self) -> None:
        if self.vc.is_paused():
            self.vc.resume()
            self.paused = False

    def stop(self) -> None:
        self.queue.clear()
        self.current = None
        if self.vc.is_playing() or self.vc.is_paused():
            self.vc.stop()

    def pause_for_tts(self) -> bool:
        """Detiene la reproducción actual para que hable el TTS. Devuelve si había algo sonando."""
        self._interrupted = False
        if self.vc.is_playing() or self.vc.is_paused():
            self._interrupted = True
            self.vc.stop()
        return self._interrupted

    def resume_after_tts(self) -> None:
        """Re-encola el tema que se interrumpió y sigue la reproducción."""
        if self._interrupted and self.current is not None:
            self.queue.insert(0, self.current)
            self.current = None
            self._interrupted = False
        self.play_next()

    def set_volume(self, volume: float) -> None:
        self.volume = max(0.0, min(2.0, volume))
        if self.vc.source is not None:
            try:
                self.vc.source.volume = self.volume
            except Exception:
                pass

    def shuffle(self) -> None:
        random.shuffle(self.queue)

    def toggle_loop(self) -> bool:
        self.loop_current = not self.loop_current
        return self.loop_current

    def queue_status(self) -> str:
        lines = []
        if self.current:
            lines.append(
                f"▶ **{self.current.title}** ({_format_duration(self.current.duration)})"
            )
        for i, track in enumerate(self.queue[:10], 1):
            lines.append(f"{i}. {track.title} ({_format_duration(track.duration)})")
        if len(self.queue) > 10:
            lines.append(f"... y {len(self.queue) - 10} más")
        return "\n".join(lines) if lines else "Cola vacía."
