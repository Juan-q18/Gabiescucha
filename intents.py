import re
from dataclasses import dataclass, field
from typing import Optional

# Verbos tolerantes: aceptan manglings tipicos de whisper
# (mutia/mutea, sordia/sordea, movia/movi, decia/deci, etc.)
_VERBS = {
    "help": r"ayuda|qu[eé] (?:pod[eé]s|sab[eé]s) hacer",
    "chat": r"escrib[ií](?:me|bime|bi|bime|le|ile|ime)?",
    "tts": r"(?:dec[ií]me|dec[ií]|d[ií]me)",
    "music": r"(?:pon(?:e|é|eme|éme|er|erme)?|reproduc[ií](?:a|e|eme|ir)?|toc(?:a|á|ar|aba|ame|eme)|pas(?:a|á|ar|ame|eme)|m[úu]sic[oa])",
    "mod_unmute": r"desmut(?:ea|eá|ia|e|iar|ear|ar)",
    "mod_mute": r"(?:mut(?:ea|eá|ia|iar|ear|ar|a|ee|ees|eeis)|silenci(?:a|á|ar|aba|ame|eme))",
    "mod_undeafen": r"des(?:ord(?:ea|eá|ia|iar|ear|ar)|orde)",
    "mod_deafen": r"(?:sord(?:ea|eá|ia|iar|ear|ar)|ensordec(?:e|i|er)|sorda)",
    "mod_move": r"mov(?:e|é|i|í|er|erme|eme|ia|ió)",
    "mod_kick": r"(?:expuls(?:a|e|á|ia|iar|ar|ame|eme)|ech(?:a|á|ar|ame|eme)|echal(?:o|e))",
    "mod_ban": r"ban(?:ea|eá|ia|iar|ear|ar|ame|eme)",
}

# Rellenos que whisper puede intercalar; se descartan al armar el target.
# Se incluyen variantes con y sin acento (whisper puede escribir ambas).
_FILLERS = {
    "a", "al", "la", "el", "lo", "los", "las", "un", "una", "unas",
    "que", "me", "se", "te", "le", "lo", "yo", "tú", "tu", "vos", "usted",
    "podes", "puedes", "puedo", "y", "dale", "che", "eh", "ah", "bueno",
    "quiero", "necesito", "por", "favor", "para", "hacia", "a ver", "aver",
    "entonces", "después", "despues", "luego", "mira", "mirá", "o sea", "osea",
}

_RULES = [
    ("help", "help"),
    ("chat", "chat"),
    ("tts", "tts"),
    ("music", "music"),
    ("mod_unmute", "mod_unmute"),
    ("mod_mute", "mod_mute"),
    ("mod_undeafen", "mod_undeafen"),
    ("mod_deafen", "mod_deafen"),
    ("mod_move_target", "mod_move"),
    ("mod_kick", "mod_kick"),
    ("mod_ban", "mod_ban"),
]


@dataclass
class Command:
    action: str = "none"
    args: dict = field(default_factory=dict)

    @property
    def text(self) -> Optional[str]:
        return self.args.get("text")

    @property
    def target(self) -> Optional[str]:
        return self.args.get("target")

    @property
    def channel(self) -> Optional[str]:
        return self.args.get("channel")


def _clean(text: str) -> str:
    """Minusculas, quita puntuacion y colapsa espacios."""
    text = text.lower()
    text = re.sub(r"[.,;:!¡?¿()\"']", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _strip_fillers(text: str) -> str:
    """Descarta palabras de relleno (contiguas) del texto dado."""
    tokens = text.split()
    out = [t for t in tokens if t not in _FILLERS]
    return " ".join(out).strip()


def _after_verb(text: str, action: str) -> Optional[str]:
    """Texto despues del verbo; None si el verbo no aparece."""
    m = re.search(r"\b(" + _VERBS[action] + r")\b\s*(?P<tail>.*)$", text, re.I)
    if not m:
        return None
    return _strip_fillers(m.group("tail").strip())


def _parse_move(text: str) -> Optional[Command]:
    """'move [a] X a Y' -> mod_move_target ; 'move [a] Y' -> mod_move."""
    m = re.search(r"\b(" + _VERBS["mod_move"] + r")\b\s*(?P<rest>.*)$", text, re.I)
    if not m:
        return None
    rest = m.group("rest").strip()
    rest = re.sub(r"^(?:a\s+|al\s+|para\s+|hacia\s+|de\s+)+", "", rest)
    m2 = re.match(r"^(?P<target>.+?)\s+(?:a|al|hacia|para)\s+(?P<channel>.+)$", rest)
    if m2:
        target = _strip_fillers(m2.group("target").strip())
        channel = _strip_fillers(m2.group("channel").strip())
        if target and channel:
            return Command(action="mod_move_target", args={"target": target, "channel": channel})
    channel = _strip_fillers(rest)
    if channel:
        return Command(action="mod_move", args={"channel": channel})
    return None


def _strip_chat_prefix(text: str) -> str:
    """Quita 'en el chat / al chat / en el canal / chat / canal' del inicio."""
    text = re.sub(
        r"^(?:(?:en|al)\s+)?(?:el\s+|la\s+)?(?:chat|canal)\b\s*",
        "",
        text,
    )
    return text.strip()


def parse(text: str) -> Command:
    """Interpreta una frase libre (post wake word) y devuelve un Command.

    Matcheo tolerante: ignora rellenos, puntuacion y variantes tipicas de
    transcripcion (mutia/mutea, sordia/sordea, etc.).
    """
    text = _clean(text)
    if not text:
        return Command()

    for action, kind in _RULES:
        if action == "help":
            if re.search(r"\b(?:ayuda|qu[eé] (?:pod[eé]s|sab[eé]s) hacer)\b", text):
                return Command(action="help")
            continue

        if kind == "mod_move":
            cmd = _parse_move(text)
            if cmd:
                return cmd
            continue

        tail = _after_verb(text, kind)
        if not tail:
            continue

        if kind in ("mod_unmute", "mod_mute", "mod_undeafen", "mod_deafen", "mod_kick", "mod_ban"):
            return Command(action=kind, args={"target": tail})
        if kind in ("music", "tts"):
            return Command(action=kind, args={"text": tail})
        if kind == "chat":
            return Command(action=kind, args={"text": _strip_chat_prefix(tail)})

    return Command()
