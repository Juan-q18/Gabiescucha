import re
from dataclasses import dataclass, field
from typing import Optional

_PREFIX = r"(?:(?:p[oó]d[eé]s|me|y|dale|che)\s+)*"


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


_RULES = [
    (
        "help",
        re.compile(
            _PREFIX + r"(?:ayuda|qu[eé]\s*(?:pod(?:e|é)s|sab(?:e|é)s)\s*hacer)\s*$",
            re.I,
        ),
    ),
    (
        "chat",
        re.compile(
            _PREFIX + r"escri(?:b[ií]\w*|bime)\s+(?P<text>.+)$",
            re.I,
        ),
    ),
    (
        "tts",
        re.compile(
            _PREFIX + r"(?:dec[ií]\w*\s+|decime\s+|deci\s+|di\s+)(?P<text>.+)$",
            re.I,
        ),
    ),
    (
        "music",
        re.compile(
            _PREFIX
            + r"(?:pon(?:e|é|eme|éme|er|erme)?\s+|reproduc[ií]\w*\s+|toc[aá]\w*\s+|pas[aá]\w*\s+|m[úu]sic[aá]\s+)(?P<text>.+)$",
            re.I,
        ),
    ),
    (
        "mod_unmute",
        re.compile(_PREFIX + r"desmute(?:a|á)\w*\s+(?:a\s+)?(?P<target>.+)$", re.I),
    ),
    (
        "mod_mute",
        re.compile(
            _PREFIX + r"(?:mut(?:ea|eá|ear)\w*\s+|silenci(?:a|á)\w*\s+)(?:a\s+)?(?P<target>.+)$",
            re.I,
        ),
    ),
    (
        "mod_undeafen",
        re.compile(_PREFIX + r"desordea\w*\s+(?:a\s+)?(?P<target>.+)$", re.I),
    ),
    (
        "mod_deafen",
        re.compile(_PREFIX + r"sord(?:ea|eá)\w*\s+(?:a\s+)?(?P<target>.+)$", re.I),
    ),
    (
        "mod_move_target",
        re.compile(
            _PREFIX + r"mov(?:e|é|er)\w*\s+(?:a\s+)?(?P<target>.+?)\s+(?:a\s+|al\s+)(?P<channel>.+)$",
            re.I,
        ),
    ),
    (
        "mod_move",
        re.compile(
            _PREFIX + r"mov(?:e|é|er)\w*\s+(?:a\s+|al\s+)?(?P<channel>.+)$",
            re.I,
        ),
    ),
    (
        "mod_kick",
        re.compile(
            _PREFIX + r"(?:expuls(?:a|e|á)\w*\s+|ech(?:a|á)\w*\s+)(?:a\s+)?(?P<target>.+)$",
            re.I,
        ),
    ),
    (
        "mod_ban",
        re.compile(_PREFIX + r"ban(?:ea|eá|éa|ear)\w*\s+(?:a\s+)?(?P<target>.+)$", re.I),
    ),
]


def parse(text: str) -> Command:
    """Interpreta una frase libre (post wake word) y devuelve un Command."""
    text = text.strip()
    if not text:
        return Command()

    for action, pattern in _RULES:
        m = pattern.match(text)
        if m:
            args = {k: v.strip() for k, v in m.groupdict().items() if v}
            return Command(action=action, args=args)

    return Command()
