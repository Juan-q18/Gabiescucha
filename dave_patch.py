# -*- coding: utf-8 -*-
"""Monkey-patch para que voice_recv descifre paquetes DAVE (E2EE).

Discord obliga DAVE (cifrado extremo a extremo) en voz desde marzo 2026.
discord.py 2.7.1 cifra DAVE al enviar, pero discord-ext-voice-recv no
descifra los paquetes entrantes: opus recibia basura y el audio salia roto.

Basado en el PR #54 de imayhaveborkedit/discord-ext-voice-recv
(https://github.com/imayhaveborkedit/discord-ext-voice-recv/pull/54) con
endurecimiento: guard de member None, except Exception, y fallback si davey
no esta instalado. El patch es idempotente y sobrevive a pip install -U.
"""

import logging

from discord.ext.voice_recv.buffer import HeapJitterBuffer as JitterBuffer
from discord.ext.voice_recv.opus import PacketDecoder, VoiceData
from discord.ext.voice_recv.router import PacketRouter
from discord.ext.voice_recv.rtp import FakePacket
from discord.opus import Decoder

try:
    from davey import MediaType

    _HAS_DAVE = True
except ImportError:  # pragma: no cover
    MediaType = None
    _HAS_DAVE = False

log = logging.getLogger(__name__)

_PATCHED = False


def _setup_dave_session(decoder) -> None:
    """Habilita passthrough de la sesion DAVE para recibir mezcla del canal."""
    try:
        session = decoder.vc._connection.dave_session
        if session is not None:
            session.set_passthrough_mode(True, 10)
    except Exception:
        log.debug("No se pudo configurar passthrough DAVE", exc_info=True)


def _patched_init(self, router, ssrc):
    self.router = router
    self.ssrc = ssrc

    self._decoder = None if self.sink.wants_opus() else Decoder()
    self._buffer = JitterBuffer()
    self._cached_id = None

    self.vc = self.sink.voice_client
    _setup_dave_session(self)

    self._last_seq = -1
    self._last_ts = -1


def _patched_process_packet(self, packet) -> VoiceData:
    pcm = None

    member = self._get_cached_member()

    if member is None:
        self._cached_id = self.sink.voice_client._get_id_from_ssrc(self.ssrc)
        member = self._get_cached_member()

    if (
        _HAS_DAVE
        and member is not None
        and not packet.is_silence()
        and packet.decrypted_data is not None
        and self.vc._connection.dave_session is not None
        and self.vc._connection.dave_session.ready
    ):
        try:
            packet.decrypted_data = self.vc._connection.dave_session.decrypt(
                member.id, MediaType.audio, bytes(packet.decrypted_data)
            )
        except Exception:
            self._last_seq = packet.sequence
            self._last_ts = packet.timestamp
            return VoiceData(packet, None, pcm=b"")

    if not self.sink.wants_opus():
        packet, pcm = self._decode_packet(packet)

    data = VoiceData(packet, member, pcm=pcm)
    self._last_seq = packet.sequence
    self._last_ts = packet.timestamp

    return data


def _patched_decode_packet(self, packet):
    assert self._decoder is not None

    # Paquete real: decodificamos y, si opus falla, insertamos un frame de silencio
    if packet:
        try:
            pcm = self._decoder.decode(packet.decrypted_data, fec=False)
        except Exception:
            pcm = self._decoder.decode(None, fec=False)
        return packet, pcm

    # Fake packet: intentamos recuperar con FEC del siguiente paquete
    next_packet = self._buffer.peek_next()

    if next_packet is not None:
        nextdata = next_packet.decrypted_data

        log.debug(
            "Generating fec packet: fake=%s, fec=%s",
            packet.sequence,
            next_packet.sequence,
        )
        try:
            pcm = self._decoder.decode(nextdata, fec=True)
        except Exception:
            pcm = self._decoder.decode(None, fec=False)
    else:
        pcm = self._decoder.decode(None, fec=False)

    return packet, pcm


def _patched_do_run(self) -> None:
    while not self._end_thread.is_set():
        self.waiter.wait()
        with self._lock:
            for decoder in self.waiter.items:
                try:
                    data = decoder.pop_data()
                except Exception:
                    continue
                if data is not None and data.source is not None:
                    self.sink.write(data.source, data)


def apply_dave_patch() -> None:
    """Aplica el monkey-patch de DAVE a voice_recv (idempotente)."""
    global _PATCHED
    if _PATCHED:
        return

    PacketDecoder.__init__ = _patched_init
    PacketDecoder._process_packet = _patched_process_packet
    PacketDecoder._decode_packet = _patched_decode_packet
    PacketRouter._do_run = _patched_do_run

    _PATCHED = True
    log.info(
        "Patch DAVE aplicado a voice_recv (%s)",
        "descifrado activo" if _HAS_DAVE else "davey no disponible, sin descifrado",
    )
