# type: ignore

import logging
from datetime import datetime
from functools import partial
from typing import List, Union, Optional

from pyrogram.client import Client
from pyrogram import raw, enums
from pyrogram import types
from pyrogram import utils
from pyrogram.parser import utils as parser_utils, Parser

log = logging.getLogger(__name__)


class Str(str):
    def __init__(self, *args):
        super().__init__()

        self.entities = None

    def init(self, entities):
        self.entities = entities

        return self

    @property
    def markdown(self):
        return Parser.unparse(self, self.entities, False)

    @property
    def html(self):
        return Parser.unparse(self, self.entities, True)

    def __getitem__(self, item):
        return parser_utils.remove_surrogates(parser_utils.add_surrogates(self)[item])


async def send_message(
    self: Client,
    chat_id: Union[int, str],
    text: str,
    parse_mode: Optional["enums.ParseMode"] = None,
    entities: List["types.MessageEntity"] = None,
    disable_web_page_preview: bool = None,
    disable_notification: bool = None,
    reply_to_message_id: int = None,
    schedule_date: datetime = None,
    protect_content: bool = None,
    reply_markup: Union[
        "types.InlineKeyboardMarkup",
        "types.ReplyKeyboardMarkup",
        "types.ReplyKeyboardRemove",
        "types.ForceReply"
    ] = None,
    send_as: Optional[raw.base.InputPeer] = None
) -> "types.Message":
    """Send text messages.

    .. include:: /_includes/usable-by/users-bots.rst

    Parameters:
        chat_id (``int`` | ``str``):
            Unique identifier (int) or username (str) of the target chat.
            For your personal cloud (Saved Messages) you can simply use "me" or "self".
            For a contact that exists in your Telegram address book you can use his phone number (str).

        text (``str``):
            Text of the message to be sent.

        parse_mode (:obj:`~pyrogram.enums.ParseMode`, *optional*):
            By default, texts are parsed using both Markdown and HTML styles.
            You can combine both syntaxes together.

        entities (List of :obj:`~pyrogram.types.MessageEntity`):
            List of special entities that appear in message text, which can be specified instead of *parse_mode*.

        disable_web_page_preview (``bool``, *optional*):
            Disables link previews for links in this message.

        disable_notification (``bool``, *optional*):
            Sends the message silently.
            Users will receive a notification with no sound.

        reply_to_message_id (``int``, *optional*):
            If the message is a reply, ID of the original message.

        schedule_date (:py:obj:`~datetime.datetime`, *optional*):
            Date when the message will be automatically sent.

        protect_content (``bool``, *optional*):
            Protects the contents of the sent message from forwarding and saving.

        reply_markup (:obj:`~pyrogram.types.InlineKeyboardMarkup` | :obj:`~pyrogram.types.ReplyKeyboardMarkup` | :obj:`~pyrogram.types.ReplyKeyboardRemove` | :obj:`~pyrogram.types.ForceReply`, *optional*):
            Additional interface options. An object for an inline keyboard, custom reply keyboard,
            instructions to remove reply keyboard or to force a reply from the user.

    Returns:
        :obj:`~pyrogram.types.Message`: On success, the sent text message is returned.

    Example:
        .. code-block:: python

            # Simple example
            await app.send_message("me", "Message sent with **Pyrogram**!")

            # Disable web page previews
            await app.send_message("me", "https://docs.pyrogram.org",
                disable_web_page_preview=True)

            # Reply to a message using its id
            await app.send_message("me", "this is a reply", reply_to_message_id=123)

        .. code-block:: python

            # For bots only, send messages with keyboards attached

            from pyrogram.types import (
                ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton)

            # Send a normal keyboard
            await app.send_message(
                chat_id, "Look at that button!",
                reply_markup=ReplyKeyboardMarkup([["Nice!"]]))

            # Send an inline keyboard
            await app.send_message(
                chat_id, "These are inline buttons",
                reply_markup=InlineKeyboardMarkup(
                    [
                        [InlineKeyboardButton("Data", callback_data="callback_data")],
                        [InlineKeyboardButton("Docs", url="https://docs.pyrogram.org")]
                    ]))
    """

    message, entities = (await utils.parse_text_entities(self, text, parse_mode, entities)).values()

    r = await self.invoke(
        raw.functions.messages.SendMessage(
            peer=await self.resolve_peer(chat_id),
            no_webpage=disable_web_page_preview or None,
            silent=disable_notification or None,
            reply_to_msg_id=reply_to_message_id,
            random_id=self.rnd_id(),
            schedule_date=utils.datetime_to_timestamp(schedule_date),
            reply_markup=await reply_markup.write(self) if reply_markup else None,
            message=message,
            entities=entities,
            noforwards=protect_content,
            send_as = send_as
        )
    )

    if isinstance(r, raw.types.UpdateShortSentMessage):
        peer = await self.resolve_peer(chat_id)

        peer_id = (
            peer.user_id
            if isinstance(peer, raw.types.InputPeerUser)
            else -peer.chat_id
        )

        return types.Message(
            id=r.id,
            chat=types.Chat(
                id=peer_id,
                type=enums.ChatType.PRIVATE,
                client=self
            ),
            text=message,
            date=utils.timestamp_to_datetime(r.date),
            outgoing=r.out,
            reply_markup=reply_markup,
            entities=[
                types.MessageEntity._parse(None, entity, {})
                for entity in entities
            ] if entities else None,
            client=self
        )

    for i in r.updates:
        if isinstance(i, (raw.types.UpdateNewMessage,
                            raw.types.UpdateNewChannelMessage,
                            raw.types.UpdateNewScheduledMessage)):
            return await types.Message._parse(
                self, i.message,
                {i.id: i for i in r.users},
                {i.id: i for i in r.chats},
                is_scheduled=isinstance(i, raw.types.UpdateNewScheduledMessage)
            )


def setup_client(client: Client, send_as: raw.base.InputPeer | None=None) -> None:
    client.send_message = partial(send_message, client, send_as=send_as or raw.types.InputPeerSelf())
