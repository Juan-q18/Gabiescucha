import io
import logging
import threading
import time
import wave

import _cuda  # noqa: F401  (expone los DLL de nvidia antes de importar faster_whisper)

from faster_whisper import WhisperModel
from faster_whisper.audio import decode_audio

log = logging.getLogger(__name__)


class WhisperTranscriber:
    """Transcribe PCM (48kHz stereo) a texto usando faster-whisper local (CPU o CUDA)."""

    def __init__(
        self,
        model: str = "small",
        device: str = "cuda",
        compute_type: str = "int8",
        language: str = "es",
        no_speech_threshold: float = -1.0,
        no_speech_prob_threshold: float = 0.6,
        initial_prompt: str = "",
        beam_size: int = 5,
    ):
        self.model = model
        self.device = device
        self.compute_type = compute_type
        self.language = language
        self.no_speech_threshold = no_speech_threshold
        self.no_speech_prob_threshold = no_speech_prob_threshold
        self.initial_prompt = initial_prompt
        self.beam_size = beam_size
        self._model = None
        self._lock = threading.Lock()
        self._load_attempted = False

    def load(self) -> None:
        """Carga el modelo en memoria (se puede llamar al arrancar para evitar
        la demora del primer comando)."""
        with self._lock:
            self._load()

    def _load(self) -> None:
        if self._model is not None or self._load_attempted:
            return
        self._load_attempted = True
        try:
            log.info(
                "Cargando modelo faster-whisper '%s' en %s (compute=%s)...",
                self.model,
                self.device,
                self.compute_type,
            )
            t0 = time.time()
            self._model = WhisperModel(
                self.model, device=self.device, compute_type=self.compute_type
            )
            log.info("Modelo listo en %.1fs", time.time() - t0)
        except Exception as exc:
            if self.device != "cpu":
                log.warning(
                    "Fallo al cargar en %s (%s), usando CPU int8",
                    self.device,
                    exc,
                )
                self._model = WhisperModel(self.model, device="cpu", compute_type="int8")
                self.device = "cpu"
            else:
                raise

    @staticmethod
    def pcm_to_wav(pcm: bytes) -> bytes:
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(2)
            wf.setsampwidth(2)
            wf.setframerate(48000)
            wf.writeframes(pcm)
        return buf.getvalue()

    def transcribe(self, pcm: bytes) -> str:
        wav = self.pcm_to_wav(pcm)
        audio = decode_audio(io.BytesIO(wav), sampling_rate=16000)

        with self._lock:
            self._load()
            if self._model is None:
                return ""
            segments, info = self._model.transcribe(
                audio,
                language=self.language,
                initial_prompt=self.initial_prompt,
                condition_on_previous_text=False,
                vad_filter=False,
                beam_size=self.beam_size,
            )
            parts = []
            for seg in segments:
                text = seg.text.strip()
                no_speech = seg.no_speech_prob > self.no_speech_prob_threshold
                low_conf = seg.avg_logprob < self.no_speech_threshold
                if not text:
                    log.debug("Segmento vacío (no_speech=%.2f)", seg.no_speech_prob)
                    continue
                if low_conf or no_speech:
                    log.debug(
                        "Segmento descartado (logprob=%.2f no_speech=%.2f): %r",
                        seg.avg_logprob,
                        seg.no_speech_prob,
                        text,
                    )
                    continue
                parts.append(text)
        return " ".join(parts).strip()
