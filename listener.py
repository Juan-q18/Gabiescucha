import audioop
import logging
import threading
import time

from discord.ext import voice_recv

log = logging.getLogger(__name__)

SAMPLE_RATE = 48000
CHANNELS = 2
SAMPLE_WIDTH = 2
BYTES_PER_SECOND = SAMPLE_RATE * CHANNELS * SAMPLE_WIDTH


class SpeechSink(voice_recv.AudioSink):
    """Sink que acumula PCM por usuario y detecta segmentos de voz por energia."""

    def __init__(
        self,
        *,
        rms_threshold: int = 900,
        min_loud_frames: int = 4,
        min_duration: float = 0.5,
        silence_duration: float = 3.0,
        max_duration: float = 15.0,
        ignore_bots: bool = True,
        on_segment=None,
    ):
        super().__init__()
        self.rms_threshold = rms_threshold
        self.min_loud_frames = min_loud_frames
        self.min_duration = min_duration
        self.silence_duration = silence_duration
        self.max_duration = max_duration
        self.ignore_bots = ignore_bots
        self.on_segment = on_segment

        self._lock = threading.Lock()
        self._data: dict = {}
        self._streaks: dict = {}
        self._closed = False

        self._monitor = threading.Thread(
            target=self._monitor_loop, daemon=True, name="speech-sink-monitor"
        )
        self._monitor.start()

    def wants_opus(self) -> bool:
        return False

    def write(self, user, data) -> None:
        pcm = data.pcm
        if not pcm or user is None:
            return
        if self.ignore_bots and getattr(user, "bot", False):
            return

        now = time.monotonic()
        loud = audioop.rms(pcm, SAMPLE_WIDTH) >= self.rms_threshold

        with self._lock:
            entry = self._data.get(user.id)
            if entry is None:
                # Solo comenzamos a grabar tras suficientes frames activos
                # consecutivos para descartar ráfagas de ruido aisladas.
                streak = self._streaks.get(user.id, 0)
                if loud:
                    streak += 1
                    self._streaks[user.id] = streak
                    if streak >= self.min_loud_frames:
                        del self._streaks[user.id]
                        entry = {"user": user, "buf": bytearray(), "last_voice": now}
                        self._data[user.id] = entry
                    else:
                        return
                else:
                    if streak > 0:
                        self._streaks[user.id] = 0
                    return

            entry["buf"] += pcm
            if loud:
                entry["last_voice"] = now
            else:
                self._flush_if_done(entry, now)

    def _flush_if_done(self, entry, now) -> None:
        duration = len(entry["buf"]) / BYTES_PER_SECOND
        silence = now - entry["last_voice"]

        if silence >= self.silence_duration or duration >= self.max_duration:
            if duration >= self.min_duration:
                self._emit(entry)
            self._data.pop(entry["user"].id, None)

    def _monitor_loop(self) -> None:
        while not self._closed:
            time.sleep(0.2)
            now = time.monotonic()
            with self._lock:
                for entry in list(self._data.values()):
                    if now - entry["last_voice"] >= self.silence_duration:
                        self._flush_if_done(entry, now)

    def _emit(self, entry) -> None:
        pcm = bytes(entry["buf"])
        user = entry["user"]

        def run():
            try:
                if self.on_segment:
                    self.on_segment(user, pcm)
            except Exception:
                log.exception("Error en el callback on_segment")

        threading.Thread(target=run, daemon=True, name=f"segment-{user.id}").start()

    def cleanup(self) -> None:
        self._closed = True
        with self._lock:
            for entry in list(self._data.values()):
                if entry["buf"]:
                    self._emit(entry)
            self._data.clear()
