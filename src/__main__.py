from pyrogram.client import Client
from pyrogram import filters
from pyrogram.types import Message
from pyrogram.raw.base.input_peer import InputPeer
from pytgcalls import PyTgCalls, filters as calls_filters, idle
# from pytgcalls.types import UpdatedGroupCallParticipant
from pytgcalls.types import AudioQuality, StreamEnded
# from pytgcalls.types import MediaStream

import re
import asyncio
import typing

from src import constants, utils
from src.player import PlayerPy
from src.config import config


COMMANDS_PREFIXES = "!"
JOIN_COMAMND = "join"
JOIN_COMMAND_PATTERN = re.compile(r"^(?:\s+(?P<join_chat_id>-|@?[a-zA-Z0-9_]{4,})(?:\s+(?P<join_as_id>@?[a-zA-Z0-9_]{4,}))?)?$")


logger = utils.get_logger(
    name = "tests",
    filepath = constants.LOG_FILEPATH,
    console_log_level = config.console_log_level,
    file_log_level = config.file_log_level
)


app = Client(
    name = config.session.name,
    api_id = config.session.api_id,
    api_hash = config.session.api_hash,
    phone_number = config.session.phone_number,
    workdir = constants.WORK_DIRPATH.resolve().as_posix()
)

call_py = PyTgCalls(app)

player_py = PlayerPy(
    logger = logger,
    app = app,
    call_py = call_py,
    quality = AudioQuality.HIGH
)


add_to_queue_lock = asyncio.Lock()


async def _chat_id_filter(_: typing.Any, __: typing.Any, message: Message) -> bool:
    if isinstance(config.control_chat_id, int):
        return message.chat.id == config.control_chat_id

    return message.chat.username.lower() == config.control_chat_id

chat_id_filter = filters.create(_chat_id_filter)


def _extract_id_from_peer(peer: InputPeer) -> int | None:
    return getattr(peer, "chat_id", None) or getattr(peer, "channel_id", None) or getattr(peer, "user_id", None)

@typing.overload
async def _resolve_chat_id(value: int | str, as_peer: typing.Literal[False]=...) -> int: ...

@typing.overload
async def _resolve_chat_id(value: int | str, as_peer: typing.Literal[True]) -> InputPeer: ...

async def _resolve_chat_id(value: int | str, as_peer: bool=False) -> int | InputPeer:
    if isinstance(value, str) and utils.is_int(value):
        value = int(value)

    try:
        chat_peer: InputPeer = await app.resolve_peer(value)  # type: ignore

    except Exception:
        raise ValueError("A valid peer must be specified")

    if as_peer:
        return chat_peer

    chat_id = _extract_id_from_peer(chat_peer)

    if not chat_id:
        raise ValueError("A valid ID must be specified")

    return chat_id

def _fix_chat_id(chat_id: int) -> int:
    if chat_id > 0:
        return -1_000_000_000_000 - chat_id

    return chat_id


@app.on_message(chat_id_filter & filters.command(JOIN_COMAMND, COMMANDS_PREFIXES))
async def join_handler(_, message: Message):
    """
    Join a voice chat.

    Args:
        `join_chat_id`: Chat ID to listen to. Can be a username or an ID. If not specified, the default chat ID from config or current chat will be used.
        `join_as_id`: Chat ID to join as. Can be a username or an ID. If not specified, the client will join as itself.

    Examples:
    `!join <join_chat_id> <join_as_id>`

    `!join - <join_as_id>`

    `!join`
    """
    command_match = JOIN_COMMAND_PATTERN.match(typing.cast(str, message.text)[1 + len(JOIN_COMAMND):])  # skip prefix and command

    if not command_match:
        await message.reply_text("Invalid command format")

        return

    async with add_to_queue_lock:
        command_match_data: dict[str, str] = command_match.groupdict()

        join_chat_id_str = command_match_data.get("join_chat_id")
        join_as_id_str = command_match_data.get("join_as_id")

        processing_message = await message.reply_text("Processing...")

        if not join_chat_id_str or join_chat_id_str == "-":
            join_chat_id = config.default_join_chat_id or message.chat.id

            if isinstance(join_chat_id, int):
                join_chat_id = _fix_chat_id(join_chat_id)

            else:
                try:
                    join_chat_id = _fix_chat_id(await _resolve_chat_id(join_chat_id))

                except ValueError as ex:
                    await processing_message.delete()
                    await message.reply_text(f"Listen chat ID (config) = {join_chat_id!r}" + "\n" + ex.args[0])

            join_chat_id = typing.cast(int, join_chat_id)

        else:
            try:
                join_chat_id = _fix_chat_id(await _resolve_chat_id(join_chat_id_str))

            except ValueError as ex:
                await processing_message.delete()
                await message.reply_text(f"Chat ID = {join_chat_id_str!r}" + "\n" + ex.args[0])

                return

        if player_py.is_running:
            if player_py.join_chat_id != join_chat_id:
                await processing_message.delete()
                await message.reply_text(f"Already joined to chat {join_chat_id}")

                return

            await player_py.stop()

        join_as_peer: InputPeer | None = None

        if join_as_id_str:
            try:
                join_as_peer = await _resolve_chat_id(join_as_id_str, as_peer=True)

            except ValueError as ex:
                await processing_message.delete()
                await message.reply_text(f"Join as ID = {join_as_id_str!r}" + "\n" + ex.args[0])

                return

        await player_py.join(
            join_chat_id = join_chat_id,
            join_as_peer = join_as_peer
        )

        await processing_message.delete()

        await message.reply_text((
            f"Joined to voice chat of <code>{join_chat_id}</code>\n"
            f"""Joined as: {f"<code>{_fix_chat_id(typing.cast(int, _extract_id_from_peer(join_as_peer)))}</code>" if join_as_peer else "<b>self</b>"}"""
        ))


@app.on_message(chat_id_filter & filters.command(["add"], COMMANDS_PREFIXES) & filters.reply)  # add to queue
async def add_handler(_, message: Message):
    if not player_py.is_running:
        await message.reply_text("Player is not running")

        return

    reply_to_message = message.reply_to_message

    playable_media = None
    file_ext = None

    if reply_to_message.audio:
        playable_media = reply_to_message.audio
        file_ext = "mp3"
    
    elif reply_to_message.voice:
        playable_media = reply_to_message.voice
        file_ext = "ogg"

    if not playable_media:
        await message.reply_text("Reply to an audio file or voice message")

        return

    processing_message = await reply_to_message.reply_text("Processing...")

    file_path = constants.DOWNLOADS_DIRPATH.joinpath(f"{utils.get_timestamp_int()}.{file_ext}").as_posix()

    await app.download_media(
        message = playable_media.file_id,
        file_name = file_path
    )

    await player_py.add_song(file_path)

    await processing_message.delete()

    await reply_to_message.reply_text("Song added to queue")


@app.on_message(chat_id_filter & filters.command(["repeat"], COMMANDS_PREFIXES))
async def repeat_handler(_, message: Message):
    if not player_py.is_running:
        await message.reply_text("Player is not running")

        return

    player_py.songs_repeat_enabled = not player_py.songs_repeat_enabled

    await message.reply_text(f"Song repeated = {player_py.songs_repeat_enabled}")


@app.on_message(chat_id_filter & filters.command(["pause"], COMMANDS_PREFIXES))
async def pause_handler(_, message: Message):
    if not player_py.is_running:
        await message.reply_text("Player is not running")

        return

    await player_py.pause_song()

    await message.reply_text("Song paused")


@app.on_message(chat_id_filter & filters.command(["resume"], COMMANDS_PREFIXES))
async def resume_handler(_, message: Message):
    if not player_py.is_running:
        await message.reply_text("Player is not running")

        return

    await player_py.resume_song()

    await message.reply_text("Song resumed")


@app.on_message(chat_id_filter & filters.command(["skip"], COMMANDS_PREFIXES))
async def skip_handler(_, message: Message):
    if not player_py.is_running:
        await message.reply_text("Player is not running")

        return

    await player_py.skip_song()

    await message.reply_text("Song skipped")


@app.on_message(chat_id_filter & filters.command(["stop"], COMMANDS_PREFIXES))
async def stop_handler(_, message: Message):
    stopping_message = await message.reply_text("Stopping recording...")

    await player_py.stop()

    await stopping_message.delete()

    await message.reply_text("Recording stopped")


@call_py.on_update(calls_filters.stream_end())
async def stream_end_handler(_, update: StreamEnded):
    await player_py.process_stream_end()


# @call_py.on_update(calls_filters.call_participant())
# async def joined_handler(_, update: UpdatedGroupCallParticipant):
#     await recorder_py.process_participant_update(update)


call_py.start()  # type: ignore
idle()
