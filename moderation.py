import difflib
import logging
import unicodedata
from typing import Optional

import discord

log = logging.getLogger(__name__)


class ModerationError(Exception):
    pass


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFD", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return text.lower().strip()


def resolve_member(guild: discord.Guild, name: str) -> Optional[discord.Member]:
    """Resuelve un nombre (hablado) a un miembro con fuzzy matching."""
    name = name.strip().lstrip("@").strip()
    if not name:
        return None

    needle = normalize(name)
    candidates = list(guild.members)

    for m in candidates:
        if m.bot:
            continue
        labels = [m.display_name, m.name, m.nick or ""]
        for label in labels:
            if label and normalize(label) == needle:
                return m

    for m in candidates:
        if m.bot:
            continue
        labels = [m.display_name, m.name, m.nick or ""]
        for label in labels:
            if label and needle in normalize(label):
                return m

    pool = {normalize(m.display_name): m for m in candidates if not m.bot}
    if m := pool.get(needle):
        return m

    keys = list(pool.keys())
    matches = difflib.get_close_matches(needle, keys, n=1, cutoff=0.6)
    if matches:
        return pool[matches[0]]

    return None


def resolve_voice_channel(guild: discord.Guild, name: str) -> Optional[discord.VoiceChannel]:
    name = name.strip().lstrip("#").strip()
    if not name:
        return None

    needle = normalize(name)
    channels = [c for c in guild.voice_channels]

    for c in channels:
        if normalize(c.name) == needle:
            return c

    for c in channels:
        if needle in normalize(c.name):
            return c

    keys = {normalize(c.name): c for c in channels}
    matches = difflib.get_close_matches(needle, list(keys), n=1, cutoff=0.5)
    if matches:
        return keys[matches[0]]

    return None


def _has(member: discord.Member, flag: str) -> bool:
    return bool(getattr(member.guild_permissions, flag, False))


def require_author_perm(member: discord.Member, flag: str) -> None:
    if not (_has(member, flag) or _has(member, "administrator")):
        raise ModerationError("No tenés permiso para hacer eso.")


def require_bot_perm(guild: discord.Guild, flag: str) -> None:
    if not (_has(guild.me, flag) or _has(guild.me, "administrator")):
        raise ModerationError("Necesito permisos de **%s** para hacer eso." % flag)


async def set_mute(member: discord.Member, mute: bool) -> None:
    await member.edit(mute=mute)


async def set_deafen(member: discord.Member, deafen: bool) -> None:
    await member.edit(deafen=deafen)


async def move_to_channel(member: discord.Member, channel: discord.VoiceChannel) -> None:
    await member.move_to(channel)


async def kick(member: discord.Member, reason: str = "") -> None:
    await member.kick(reason=reason)


async def ban(member: discord.Member, reason: str = "") -> None:
    await member.ban(reason=reason, delete_message_days=0)
