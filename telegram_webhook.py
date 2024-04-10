#!/usr/bin/env python
# This program is dedicated to the public domain under the CC0 license.
# pylint: disable=import-error,unused-argument
"""
Simple example of a bot that uses a custom webhook setup and handles custom updates.
For the custom webhook setup, the libraries `starlette` and `uvicorn` are used. Please install
them as `pip install starlette~=0.20.0 uvicorn~=0.23.2`.
Note that any other `asyncio` based web server framework can be used for a custom webhook setup
just as well.

Usage:
Set bot Token, URL, admin CHAT_ID and PORT after the imports.
You may also need to change the `listen` value in the uvicorn configuration to match your setup.
Press Ctrl-C on the command line or send a signal to the process to stop the bot.
"""
import asyncio
import json
import os
import redis
from dataclasses import dataclass
from typing import Union, TypedDict
import requests
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response
from starlette.routing import Route
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram import __version__ as TG_VER
from telegram.ext import (
    Application,
    CallbackContext,
    CommandHandler,
    ContextTypes,
    ExtBot,
    CallbackQueryHandler, MessageHandler,
)
from typing import List, Dict
from language_util import language_init, get_languages, get_message
from telegram.ext import filters
from config_util import get_config_value
from logger import logger
from telemetry_logger import TelemetryLogger
from telegram.helpers import escape_markdown
telemetryLogger = TelemetryLogger()
# Define configuration constants
TELEGRAM_BASE_URL = os.environ["TELEGRAM_BASE_URL"]
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
botName = os.environ['TELEGRAM_BOT_NAME']
concurrent_updates = int(os.getenv('concurrent_updates', '256'))
pool_time_out = int(os.getenv('pool_timeout', '30'))
connection_pool_size = int(os.getenv('connection_pool_size', '1024'))
connect_time_out = int(os.getenv('connect_timeout', '300'))
read_time_out = int(os.getenv('read_timeout', '15'))
write_time_out = int(os.getenv('write_timeout', '10'))
workers = int(os.getenv("UVICORN_WORKERS", "4"))
redis_host = os.getenv("REDIS_HOST", "172.17.0.1")
redis_port = int(os.getenv("REDIS_PORT", "6379"))
redis_index = int(os.getenv("REDIS_INDEX", "1"))
DEFAULT_CONTEXT = get_config_value('default', 'context', None)
DEFAULT_LANGUAGE = get_config_value('default', 'language', None)
CONVERSE_ENABLED = get_config_value('default', 'converse_enabled').lower() == "true"
try:
    from telegram import __version_info__
except ImportError:
    __version_info__ = (0, 0, 0, 0, 0)  # type: ignore[assignment]

if __version_info__ < (20, 0, 0, "alpha", 1):
    raise RuntimeError(
        f"This example is not compatible with your current PTB version {TG_VER}. To view the "
        f"{TG_VER} version of this example, "
        f"visit https://docs.python-telegram-bot.org/en/v{TG_VER}/examples.html"
    )

# Connect to Redis
redis_client = redis.Redis(host=redis_host, port=redis_port, db=redis_index)  # Adjust host and port if needed


# Define a function to store and retrieve data in Redis
def store_data(key, value):
    redis_client.set(key, value)


def retrieve_data(key):
    data_from_redis = redis_client.get(key)
    return data_from_redis.decode('utf-8') if data_from_redis is not None else None


@dataclass
class WebhookUpdate:
    """Simple dataclass to wrap a custom update type"""
    user_id: int
    payload: str


class CustomContext(CallbackContext[ExtBot, dict, dict, dict]):
    """
    Custom CallbackContext class that makes `user_data` available for updates of type
    `WebhookUpdate`.
    """

    @classmethod
    def from_update(
            cls,
            update: object,
            application: "Application",
    ) -> "CustomContext":
        if isinstance(update, WebhookUpdate):
            return cls(application=application, user_id=update.user_id)
        return super().from_update(update, application)


class ApiResponse(TypedDict):
    output: any


class ApiError(TypedDict):
    error: Union[str, requests.exceptions.RequestException]


def get_user_langauge(update: Update, default_lang=DEFAULT_LANGUAGE) -> str:
    user_id_lan = str(update.effective_chat.id) + '_language'
    selected_lang = retrieve_data(user_id_lan)
    if selected_lang:
        return selected_lang
    else:
        return default_lang


def get_user_context(update: Update, default_context=DEFAULT_CONTEXT) -> str:
    user_context_id = str(update.effective_chat.id) + '_context'
    selected_context = retrieve_data(user_context_id)
    if selected_context:
        return selected_context
    else:
        return default_context


async def send_message_to_bot(chat_id, text, context: CustomContext, parse_mode="Markdown") -> None:
    """Send a message  to bot"""
    await context.bot.send_message(chat_id=chat_id, text=text, parse_mode=parse_mode)


async def start(update: Update, context: CustomContext) -> None:
    """Send a message when the command /start is issued."""
    user_name = update.message.chat.first_name
    logger.info({"id": update.effective_chat.id, "username": user_name, "category": "logged_in", "label": "logged_in"})
    await send_message_to_bot(update.effective_chat.id, get_config_value('default', 'welcome_msg', None), context)
    await language_handler(update, context)


def create_language_keyboard():
    """Creates an inline keyboard markup with buttons for supported languages."""
    inline_keyboard_buttons = []
    languages = get_languages()
    for language in languages:
        button = InlineKeyboardButton(
            text=language["text"], callback_data=f"lang_{language['code']}"
        )
        inline_keyboard_buttons.append([button])
            
    return inline_keyboard_buttons


async def language_handler(update: Update, context: CustomContext):
    inline_keyboard_buttons = create_language_keyboard()
    if inline_keyboard_buttons:
        reply_markup = InlineKeyboardMarkup(inline_keyboard_buttons)
        await context.bot.send_message(chat_id=update.effective_chat.id, text="\nPlease select a Language to proceed", reply_markup=reply_markup)
    else:
        return query_handler


async def preferred_language_callback(update: Update, context: CustomContext):
    callback_query = update.callback_query
    preferred_language = callback_query.data[len("lang_"):]
    context.user_data['language'] = preferred_language
    user_id_lan = str(update.effective_chat.id) + '_language'
    store_data(user_id_lan, preferred_language)
    logger.info(
        {"id": update.effective_chat.id, "username": update.effective_chat.first_name, "category": "language_selection",
         "label": "engine_selection", "value": preferred_language})
    await callback_query.answer()
    await context_handler(update, context)
    # return query_handler

def create_context_keyboard_buttons(contexts: List[dict]):
    inline_keyboard_buttons = []
    for context in contexts:
        inline_keyboard_buttons.append(
        [InlineKeyboardButton(context["label"], callback_data=f'contextname_{context["value"]}')])
    return InlineKeyboardMarkup(inline_keyboard_buttons)

async def context_handler(update: Update, context: CustomContext):
    selected_language = get_user_langauge(update)
    context_options = get_message(language=selected_language, key="context")
    text_message = get_message(language=selected_language, key="default_context_selection")
    reply_markup = None
    if context_options:
        reply_markup = create_context_keyboard_buttons(context_options)
        text_message = get_message(language=selected_language, key="language_selection")
    
    await context.bot.send_message(chat_id=update.effective_chat.id, text=text_message, reply_markup=reply_markup, parse_mode="Markdown") 
        
async def preferred_context_callback(update: Update, context: CustomContext):
    callback_query = update.callback_query
    preferred_context = callback_query.data[len("contextname_"):]
    context.user_data['contextname'] = preferred_context
    user_context_id = str(update.effective_chat.id) + '_context'
    store_data(user_context_id, preferred_context)
    selected_language = get_user_langauge(update)
    text_msg = get_message(selected_language,"context_selection", preferred_context)
    logger.info({"id": update.effective_chat.id, "username": update.effective_chat.first_name, "category": "context_selection", "label": "context_selection", "value": preferred_context})
    await callback_query.answer()
    await context.bot.sendMessage(chat_id=update.effective_chat.id, text=text_msg, parse_mode="Markdown")


async def help_command(update: Update, context: CustomContext) -> None:
    """Send a message when the command /help is issued."""
    await update.message.reply_text("Help!")

def get_bot_endpoint(contextName: str):
    if contextName == "story":
        return os.environ["STORY_API_BASE_URL"] + '/v1/query_rstory'
    else:
        activity_url = os.environ["ACTIVITY_API_BASE_URL"]
        url =  activity_url + '/v1/query'
        if CONVERSE_ENABLED:
            url = activity_url + '/v1/chat'
        return url

async def get_query_response(query: str, voice_message_url: str, update: Update, context: CustomContext) -> Union[
    ApiResponse, ApiError]:
    voice_message_language = get_user_langauge(update)
    selected_context = get_user_context(update)
    context.user_data['language'] = voice_message_language
    context.user_data['contextname'] = selected_context
    logger.info({"id": update.effective_chat.id, "username": update.effective_chat.first_name, "language_selected": voice_message_language, "bot_selected": selected_context})
    user_id = update.message.from_user.id
    message_id = update.message.message_id
    url = get_bot_endpoint(selected_context)
    try:
        reqBody: dict
        if voice_message_url is None:
            reqBody = {
                "input": {
                    "language": voice_message_language,
                    "text": query
                },
                "output": {
                    'format': 'text'
                }
            }
        else:
            reqBody = {
                "input": {
                    "language": voice_message_language,
                    "audio": voice_message_url
                },
                "output": {
                    'format': 'audio'
                }
            }

        if selected_context == "story" or not CONVERSE_ENABLED:
            reqBody["input"]["audienceType"] = selected_context
        else:
            reqBody["input"]["context"] = selected_context
        logger.info(f" API Request Body: {reqBody}")
        headers = {
            "x-source": "telegram",
            "x-request-id": str(message_id),
            "x-device-id": f"d{user_id}",
            "x-consumer-id": str(user_id)
        }
        response = requests.post(url, data=json.dumps(reqBody), headers=headers)
        response.raise_for_status()
        data = response.json()
        requests.session().close()
        response.close()
        return data
    except requests.exceptions.RequestException as e:
        return {'error': e}
    except (KeyError, ValueError):
        return {'error': 'Invalid response received from API'}


async def response_handler(update: Update, context: CustomContext) -> None:
    await query_handler(update, context)


async def query_handler(update: Update, context: CustomContext):
    voice_message = None
    query = None
    if update.message.text:
        query = update.message.text
        logger.info({"id": update.effective_chat.id, "username": update.effective_chat.first_name, "category": "query_handler", "label": "question", "value": query})
    elif update.message.voice:
        voice_message = update.message.voice

    voice_message_url = None
    if voice_message is not None:
        voice_file = await voice_message.get_file()
        voice_message_url = voice_file.file_path
        logger.info({"id": update.effective_chat.id, "username": update.effective_chat.first_name, "category": "query_handler", "label": "voice_question", "value": voice_message_url})
    selected_language = get_user_langauge(update)
    loading_msg = get_message(language=selected_language, key="context_loading_msg")
    await context.bot.send_message(chat_id=update.effective_chat.id, text=loading_msg)
    await handle_query_response(update, context, query, voice_message_url)
    return query_handler


async def handle_query_response(update: Update, context: CustomContext, query: str, voice_message_url: str):
    response = await get_query_response(query, voice_message_url, update, context)
    if "error" in response:
        selected_language = get_user_langauge(update)
        error_msg = get_message(language=selected_language, key="context_error_msg")
        await context.bot.send_message(chat_id=update.effective_chat.id, text=error_msg)
        info_msg = {"id": update.effective_chat.id, "username": update.effective_chat.first_name,
                    "category": "handle_query_response", "label": "question_sent", "value": query}
        logger.info(info_msg)
        merged = dict()
        merged.update(info_msg)
        merged.update(response)
        logger.error(merged)
    else:
        logger.info({"id": update.effective_chat.id, "username": update.effective_chat.first_name,
                     "category": "handle_query_response", "label": "answer_received", "value": query})
        answer = response['output']["text"]
        keyboard = [
            [InlineKeyboardButton("👍🏻", callback_data=f'message-liked__{update.message.id}'),
             InlineKeyboardButton("👎🏻", callback_data=f'message-disliked__{update.message.id}')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await context.bot.send_message(chat_id=update.effective_chat.id, text=escape_markdown(answer), parse_mode="Markdown")
        await context.bot.send_message(chat_id=update.effective_chat.id, text="Please provide your feedback", parse_mode="Markdown", reply_markup=reply_markup)
        if response['output']["audio"]:
            audio_output_url = response['output']["audio"]
            audio_request = requests.get(audio_output_url)
            audio_data = audio_request.content
            await context.bot.send_voice(chat_id=update.effective_chat.id, voice=audio_data)


async def preferred_feedback_callback(update: Update, context: CustomContext) -> None:
    """Parses the CallbackQuery and updates the message text."""
    query = update.callback_query
    queryData = query.data.split("__")
    selected_context = get_user_context(update)
    user_id = update.callback_query.from_user.id
    eventData = {
        "x-source": "telegram",
        "x-request-id": str(queryData[1]),
        "x-device-id": f"d{user_id}",
        "x-consumer-id": str(user_id),
        "subtype": queryData[0],
        "edataId": selected_context
    }
    interectEvent = telemetryLogger.prepare_interect_event(eventData)
    telemetryLogger.add_event(interectEvent)
    # # CallbackQueries need to be answered, even if no notification to the user is needed
    # # Some clients may have trouble otherwise. See https://core.telegram.org/bots/api#callbackquery
    await query.answer("Thanks for your feedback.")
    # await query.delete_message()
    thumpUpIcon = "👍" if queryData[0] == "message-liked" else "👍🏻"
    thumpDownIcon = "👎" if queryData[0] == "message-disliked" else "👎🏻"
    keyboard = [
        [InlineKeyboardButton(thumpUpIcon, callback_data='replymessage_liked'),
         InlineKeyboardButton(thumpDownIcon, callback_data='replymessage_disliked')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text("Please provide your feedback:", reply_markup=reply_markup)


async def preferred_feedback_reply_callback(update: Update, context: CustomContext) -> None:
    """Parses the CallbackQuery and updates the message text."""
    query = update.callback_query
    # # CallbackQueries need to be answered, even if no notification to the user is needed
    # # Some clients may have trouble otherwise. See https://core.telegram.org/bots/api#callbackquery
    await query.answer()


async def main() -> None:
    """Set up PTB application and a web application for handling the incoming requests."""
    logger.info('################################################')
    logger.info('# Telegram bot name %s', botName)
    logger.info('################################################')
    language_init()
    context_types = ContextTypes(context=CustomContext)
    # Here we set updater to None because we want our custom webhook server to handle the updates.persistence(persistence)
    # and hence we don't need an Updater instance
    application = (
        Application.builder().token(TELEGRAM_BOT_TOKEN).updater(None).context_types(context_types).pool_timeout(pool_time_out).connection_pool_size(connection_pool_size).concurrent_updates(True).concurrent_updates(concurrent_updates).connect_timeout(
            connect_time_out).read_timeout(read_time_out).write_timeout(write_time_out).build()
    )

    # register handlers
    application.add_handler(CommandHandler("start", start, block=False))
    application.add_handler(CommandHandler("help", help_command, block=False))
    application.add_handler(CommandHandler('select_language', language_handler, block=False))
    application.add_handler(CommandHandler('select_context', context_handler, block=False))
    application.add_handler(CallbackQueryHandler(preferred_language_callback, pattern=r'lang_\w*', block=False))
    application.add_handler(CallbackQueryHandler(preferred_context_callback, pattern=r'contextname_\w*', block=False))
    application.add_handler(CallbackQueryHandler(preferred_feedback_callback, pattern=r'message-\w*', block=False))
    application.add_handler(CallbackQueryHandler(preferred_feedback_reply_callback, pattern=r'replymessage_\w*', block=False))
    application.add_handler(MessageHandler(filters.TEXT | filters.VOICE, response_handler, block=False))

    # Pass webhook settings to telegram
    await application.bot.set_webhook(url=f"{TELEGRAM_BASE_URL}/telegram", allowed_updates=Update.ALL_TYPES)

    # Set up webserver
    async def telegram(request: Request) -> Response:
        """Handle incoming Telegram updates by putting them into the `update_queue`"""
        body = await request.json()
        await application.update_queue.put(
            Update.de_json(data=body, bot=application.bot)
        )
        return Response()

    async def health(_: Request) -> PlainTextResponse:
        """For the health endpoint, reply with a simple plain text message."""
        return PlainTextResponse(content="The bot is still running fine :)")

    starlette_app = Starlette(
        routes=[
            Route("/telegram", telegram, methods=["POST"]),
            Route("/healthcheck", health, methods=["GET"]),
        ]
    )
    webserver = uvicorn.Server(
        config=uvicorn.Config(
            app=starlette_app,
            port=8000,
            use_colors=False,
            host="0.0.0.0",
            workers=workers
        )
    )

    # Run application and webserver together
    async with application:
        await application.start()
        await webserver.serve()
        await application.stop()


if __name__ == "__main__":
    asyncio.run(main())
