from pyrogram.raw.base.input_peer import InputPeer
from pytgcalls.methods.utilities.stream_params import StreamParams
from pytgcalls.types.stream.media_stream import MediaStream
from pytgcalls.types import GroupCallConfig, GroupCallParticipant
from pytgcalls import exceptions as calls_exceptions
from ntgcalls import StreamMode, ConnectionNotFound  # pyright: ignore [reportUnknownVariableType]
from enum import Enum
from pathlib import Path

import asyncio
import typing

from src import utils
from .player import PlayerPy


class StateEnum(Enum):
    WAITING = 1
    JOINED = 2
    PLAYING_SONG = 3
    PAUSED_SONG = 4


class PlayerWorker:
    def __init__(
        self,
        parent: PlayerPy,
        join_chat_id: int,
        join_as_peer: InputPeer | None = None,
        participants_monitor_interval: float = 3.,
        none_participants_timeout: float = 60.
    ):
        self.join_chat_id = join_chat_id
        self._join_as_peer = join_as_peer
        self._participants_monitor_interval = participants_monitor_interval
        self._none_participants_timeout = none_participants_timeout

        self._logger = parent._logger
        self._app = parent._app
        self._call_py = parent._call_py
        self._call_py_binding = parent._call_py_binding
        self._app_user_id = parent._app_user_id
        self._quality = parent._quality

        self._is_running = False
        self._current_state = StateEnum.WAITING
        self._participants_monitor_task: asyncio.Task[None] | None = None
        self._songs_queue: asyncio.Queue[Path] = asyncio.Queue()
        self._songs_repeat_enabled = False
        self._last_played_song_file_path: Path | None = None
        self._none_participants_first_time = 0

    @property
    def is_running(self) -> bool:
        return self._is_running

    @property
    def current_state(self) -> StateEnum:
        return self._current_state

    @property
    def songs_repeat_enabled(self) -> bool:
        return self._songs_repeat_enabled

    @songs_repeat_enabled.setter
    def songs_repeat_enabled(self, value: bool) -> None:
        self._songs_repeat_enabled = value

    def _get_log_pre_str(self) -> str:
        return f"[{self.join_chat_id}]"

    def _log_debug(self, msg: typing.Any, **kwargs: typing.Any) -> None:
        self._logger.debug(f"{self._get_log_pre_str()} {msg}", **kwargs)

    def _log_info(self, msg: typing.Any, **kwargs: typing.Any) -> None:
        self._logger.info(f"{self._get_log_pre_str()} {msg}", **kwargs)

    def _log_exception(self, msg: typing.Any, ex: Exception, **kwargs: typing.Any) -> None:
        self._logger.exception(f"{self._get_log_pre_str()} {msg}", exc_info=ex, **kwargs)

    async def process_stream_end(self) -> None:
        self._log_info("Stream ended")

        if self._songs_repeat_enabled and self._last_played_song_file_path:
            self._log_info(f"Replaying song: {self._last_played_song_file_path.resolve().as_posix()}")

            await self._play_song(self._last_played_song_file_path)

            return

        if self._last_played_song_file_path:
            self._log_info(f"Removing song file: {self._last_played_song_file_path.resolve().as_posix()}")

            self._last_played_song_file_path.unlink()

        await self._play_next_song()

    async def _play_song(self, song_file_path: Path) -> None:
        self._last_played_song_file_path = song_file_path

        stream_params = await StreamParams.get_stream_params(  # type: ignore
            MediaStream(
                song_file_path,
                self._quality,
                audio_flags = MediaStream.Flags.REQUIRED,
                video_flags = MediaStream.Flags.IGNORE
            )
        )

        try:
            await self._call_py_binding.set_stream_sources(
                self.join_chat_id,
                StreamMode.CAPTURE,
                stream_params
            )

        except ConnectionNotFound:
            await self._call_py.play(
                chat_id = self.join_chat_id,
                config = GroupCallConfig(
                    join_as = self._join_as_peer
                )
            )

            await self._call_py_binding.set_stream_sources(
                self.join_chat_id,
                StreamMode.CAPTURE,
                stream_params
            )

        self._current_state = StateEnum.PLAYING_SONG

    async def _play_next_song(self) -> None:
        self._log_info("... Playing next song...")

        if self._songs_queue.empty():
            self._log_info("No songs in queue")

            self._current_state = StateEnum.WAITING

            return

        song_file_path = await self._songs_queue.get()

        self._log_debug(f"Got song from queue: {song_file_path.resolve().as_posix()}")

        await self._play_song(song_file_path)

    async def _participants_monitor(self) -> None:
        while self._is_running:
            await asyncio.sleep(self._participants_monitor_interval)

            try:
                participants = typing.cast(
                    list[GroupCallParticipant],
                    await self._call_py.get_participants(self.join_chat_id)
                )

            except Exception as ex:
                self._log_exception("Error while getting participants", ex)

                continue

            participants_count = len(participants)

            for participant in participants:
                user_id = participant.user_id

                if user_id == self._app_user_id:
                    participants_count -= 1

                    continue

            self._log_debug(f"Participants in chat: {len(participants)}")

            if participants_count != 0:
                self._none_participants_first_time = utils.get_timestamp_int()

            elif self._none_participants_first_time - utils.get_timestamp_int() > self._none_participants_timeout:
                self._log_debug(f"No participants in chat for a long time ({self._none_participants_timeout} seconds) - stopping worker")

                await self.stop()

                return

    async def start(self) -> None:
        """
        Start the worker session to record voice chat.
        """

        if self._is_running:
            raise ValueError("Worker is already running")

        self._is_running = True

        await self._call_py.play(
            chat_id = self.join_chat_id,
            config = GroupCallConfig(
                join_as = self._join_as_peer
            )
        )

        self._participants_monitor_task = asyncio.create_task(utils.async_wrapper_logger(self._logger, self._participants_monitor()))

        self._log_info("Worker session started")

    async def add_song(self, song_file_path: str | Path) -> None:
        self._log_info(f"Adding song to queue: {song_file_path}")

        await self._songs_queue.put(Path(song_file_path))

        if self._current_state == StateEnum.WAITING:
            await self._play_next_song()

    async def pause_song(self) -> None:
        self._log_info("Pausing song...")

        if self._current_state != StateEnum.PLAYING_SONG:
            self._log_info("No song to pause")

            return

        await self._call_py_binding.pause(self.join_chat_id)

        self._current_state = StateEnum.PAUSED_SONG

    async def resume_song(self) -> None:
        self._log_info("Resuming song...")

        if self._current_state != StateEnum.PAUSED_SONG:
            self._log_info("No song to resume")

            return

        await self._call_py_binding.resume(self.join_chat_id)

        self._current_state = StateEnum.PLAYING_SONG

    async def skip_song(self) -> None:
        self._log_info("Skipping song...")

        await self._call_py_binding.stop(self.join_chat_id)

        if self._last_played_song_file_path:
            self._last_played_song_file_path.unlink()

        if self._current_state != StateEnum.PLAYING_SONG:
            self._log_info("No song to skip")

            return

        elif self._songs_queue.empty():
            self._current_state = StateEnum.WAITING

            self._log_info("No songs in queue")

            return

        await self._play_next_song()

    async def stop(self) -> None:
        """
        Stop the worker session.
        """

        if self._is_running is False:
            return

        self._is_running = False

        if self._participants_monitor_task:
            await self._participants_monitor_task

        try:
            await self._call_py.leave_call(self.join_chat_id)
        except calls_exceptions.NotInCallError:
            pass
