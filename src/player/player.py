from pyrogram.client import Client
from pyrogram.raw.base.input_peer import InputPeer
from pytgcalls import PyTgCalls, exceptions as calls_exceptions
# from pytgcalls.types import GroupCallParticipant, UpdatedGroupCallParticipant
from pytgcalls.types import AudioQuality
from pathlib import Path

from logging import Logger
from functools import wraps

import typing


T = typing.TypeVar("T")
P = typing.ParamSpec("P")


class PlayerPy:
    def __init__(
        self,
        logger: Logger,
        app: Client,
        call_py: PyTgCalls,
        quality: AudioQuality
    ):
        self._logger = logger
        self._app = app
        self._call_py = call_py
        self._call_py_binding = call_py._binding
        self._quality = quality

        from .worker import PlayerWorker

        self._is_running = False
        self.worker: PlayerWorker | None = None

        self._app_user_id: int | None = None
        self._quality = quality

    @property
    def is_running(self) -> bool:
        return self._is_running
        # return self._is_running and bool(self.worker) and self.worker.is_running

    @property
    def join_chat_id(self) -> int | None:
        if self.worker:
            return self.worker.join_chat_id

        return None

    @staticmethod
    def _verify_running_wrapper(
        func: typing.Callable[typing.Concatenate["PlayerPy", P], typing.Awaitable[T]]
    ) -> typing.Callable[typing.Concatenate["PlayerPy", P], typing.Awaitable[T]]:
        @wraps(func)
        async def wrapper(self: "PlayerPy", *args: P.args, **kwargs: P.kwargs) -> T:
            if not self._is_running or not self.worker or not self.worker.is_running:
                raise ValueError("Player is not running")

            return await func(self, *args, **kwargs)

        return wrapper

    @staticmethod
    def _verify_running_sync_wrapper(
        func: typing.Callable[typing.Concatenate["PlayerPy", P], T]
    ) -> typing.Callable[typing.Concatenate["PlayerPy", P], T]:
        @wraps(func)
        def wrapper(self: "PlayerPy", *args: P.args, **kwargs: P.kwargs) -> T:
            if not self._is_running or not self.worker or not self.worker.is_running:
                raise ValueError("Player is not running")

            return func(self, *args, **kwargs)

        return wrapper

    async def process_stream_end(self) -> None:
        if not self._is_running or not self.worker:
            return

        await self.worker.process_stream_end()

    async def join(
        self,
        join_chat_id: int,
        join_as_peer: InputPeer | None = None
    ) -> None:
        self._logger.info("Starting (join) player...")

        if self._is_running:
            if self.worker and self.worker.join_chat_id == join_chat_id:
                self._logger.info("Player is already running in this chat")

                return

            elif self.worker:
                self._logger.info("Stopping current player...")

                await self.worker.stop()

        self._is_running = True
        self._app_user_id = self._app.me.id  # type: ignore

        from .worker import PlayerWorker

        self.worker = PlayerWorker(
            parent = self,
            join_chat_id = join_chat_id,
            join_as_peer = join_as_peer
        )

        for chat_id in (await self._call_py_binding.calls()).keys():  # type: ignore
            chat_id = typing.cast(int, chat_id)

            if chat_id != join_chat_id:
                try:
                    await self._call_py.leave_call(chat_id)
                except Exception as ex:
                    self._logger.exception(f"Error while leaving call with chat ID {chat_id}", exc_info=ex)

        await self.worker.start()

        self._logger.info("Player started")

    @_verify_running_wrapper
    async def add_song(self, song_path: str | Path) -> None:
        await self.worker.add_song(song_path)  # type: ignore

    @_verify_running_wrapper
    async def pause_song(self) -> None:
        await self.worker.pause_song()  # type: ignore

    @_verify_running_wrapper
    async def resume_song(self) -> None:
        await self.worker.resume_song()  # type: ignore

    @_verify_running_wrapper
    async def skip_song(self) -> None:
        await self.worker.skip_song()  # type: ignore

    # worker._songs_repeat_enabled
    @property
    @_verify_running_sync_wrapper
    def songs_repeat_enabled(self) -> None:
        return self.worker.songs_repeat_enabled  # type: ignore

    @songs_repeat_enabled.setter
    @_verify_running_sync_wrapper
    def songs_repeat_enabled(self, value: bool) -> None:
        self.worker.songs_repeat_enabled = value  # type: ignore

    async def stop(self) -> None:
        if not self._is_running:
            return

        self._logger.info("Stopping player...")

        self._is_running = False

        if self.worker:
            try:
                await self._call_py.leave_call(self.worker.join_chat_id)
            except calls_exceptions.NoActiveGroupCall:
                pass

            if self.worker.is_running:
                await self.worker.stop()

            self.worker = None

        self._logger.info("Player stopped")
