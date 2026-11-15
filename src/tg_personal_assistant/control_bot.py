from __future__ import annotations

import asyncio

from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

from telegram import BotCommand, ReplyKeyboardMarkup, Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)


Handler = Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable[None]]

AUTONOMOUS_SETTINGS_COMMANDS = {
    "duration": "Длительность сессии в минутах",
    "schedule_on": "Разрешить запуск по расписанию",
    "schedule_off": "Отключить расписание",
    "schedule": "Показать расписание",
    "set_hours": "Задать часы работы, например 18:00 23:00",
    "set_days": "Задать дни работы через запятую",
    "set_timezone": "Задать часовой пояс IANA",
    "limit": "Задать лимит сообщений",
    "unlimited": "Убрать лимит сообщений",
}


BUTTON_STATUS = "Статус"
BUTTON_AUTO_ON = "Включить автоответ"
BUTTON_AUTO_OFF = "Выключить автоответ"
BUTTON_PAUSE_30M = "Пауза 30 минут"
BUTTON_PAUSE_2H = "Пауза 2 часа"
BUTTON_RESUME = "Снять паузу"
BUTTON_SHOW_PROMPT = "Показать промпт"
BUTTON_EDIT_PROMPT = "Изменить промпт"
BUTTON_SHOW_SESSION = "Показать сессию"
BUTTON_RESET_SESSION = "Сбросить сессию"
BUTTON_DRAFT_FIRST_MESSAGE = "Черновик первого сообщения"
BUTTON_CANCEL_INPUT = "Отменить ввод"
BUTTON_LAST_EVENT = "Последнее событие"
BUTTON_HELP = "Помощь"

BUTTON_TO_ACTION = {
    BUTTON_STATUS: "status",
    BUTTON_AUTO_ON: "auto_on",
    BUTTON_AUTO_OFF: "auto_off",
    BUTTON_PAUSE_30M: "pause_30m",
    BUTTON_PAUSE_2H: "pause_2h",
    BUTTON_RESUME: "resume",
    BUTTON_SHOW_PROMPT: "show_prompt",
    BUTTON_EDIT_PROMPT: "edit_prompt",
    BUTTON_SHOW_SESSION: "show_session",
    BUTTON_RESET_SESSION: "reset_session",
    BUTTON_DRAFT_FIRST_MESSAGE: "draft_first_message",
    BUTTON_CANCEL_INPUT: "cancel_input",
    BUTTON_LAST_EVENT: "last_event",
    BUTTON_HELP: "help",
}


def build_control_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [BUTTON_STATUS, BUTTON_HELP],
            [BUTTON_AUTO_ON, BUTTON_AUTO_OFF],
            [BUTTON_PAUSE_30M, BUTTON_PAUSE_2H],
            [BUTTON_RESUME, BUTTON_LAST_EVENT],
            [BUTTON_SHOW_PROMPT, BUTTON_EDIT_PROMPT],
            [BUTTON_SHOW_SESSION, BUTTON_RESET_SESSION],
            [BUTTON_DRAFT_FIRST_MESSAGE, BUTTON_CANCEL_INPUT],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


class ControlBotManager:
    def __init__(self, token: str, owner_id: int, assistant: Any) -> None:
        self._token = token
        self._owner_id = owner_id
        self._assistant = assistant
        self._application: Application | None = None
        self._free_text_task: asyncio.Task | None = None

    async def start(self) -> None:
        self._application = ApplicationBuilder().token(self._token).build()
        for command, handler in (
            ("autonomous_start", self._autonomous_start),
            ("autonomous_stop", self._autonomous_stop),
            ("autonomous_status", self._autonomous_status),
            ("delivery_reviewed", self._delivery_reviewed),
        ):
            self._application.add_handler(CommandHandler(command, self._owner_only(handler)))
        for action in AUTONOMOUS_SETTINGS_COMMANDS:
            self._application.add_handler(CommandHandler(
                f"autonomous_{action}", self._owner_only(self._autonomous_settings_handler(action))
            ))
        self._application.add_handler(CommandHandler("start", self._owner_only(self._start)))
        self._application.add_handler(CommandHandler("help", self._owner_only(self._help)))
        self._application.add_handler(CommandHandler("status", self._owner_only(self._status)))
        self._application.add_handler(CommandHandler("auto_on", self._owner_only(self._on)))
        self._application.add_handler(CommandHandler("on", self._owner_only(self._on)))
        self._application.add_handler(CommandHandler("auto_off", self._owner_only(self._off)))
        self._application.add_handler(CommandHandler("off", self._owner_only(self._off)))
        self._application.add_handler(CommandHandler("resume", self._owner_only(self._resume)))
        self._application.add_handler(CommandHandler("pause_30m", self._owner_only(self._pause_30m)))
        self._application.add_handler(CommandHandler("pause_2h", self._owner_only(self._pause_2h)))
        self._application.add_handler(CommandHandler("pause", self._owner_only(self._pause)))
        self._application.add_handler(CommandHandler("show_prompt", self._owner_only(self._prompt)))
        self._application.add_handler(CommandHandler("prompt", self._owner_only(self._prompt)))
        self._application.add_handler(CommandHandler("edit_prompt", self._owner_only(self._setprompt)))
        self._application.add_handler(CommandHandler("setprompt", self._owner_only(self._setprompt)))
        self._application.add_handler(CommandHandler("show_session", self._owner_only(self._show_session)))
        self._application.add_handler(CommandHandler("reset_session", self._owner_only(self._reset_session)))
        self._application.add_handler(CommandHandler("cancel_input", self._owner_only(self._cancel_input)))
        self._application.add_handler(CommandHandler("cancel", self._owner_only(self._cancel_input)))
        self._application.add_handler(
            CommandHandler("draft_first_message", self._owner_only(self._draft_first_message))
        )
        self._application.add_handler(CommandHandler("last_event", self._owner_only(self._last)))
        self._application.add_handler(CommandHandler("last", self._owner_only(self._last)))
        self._application.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self._owner_only(self._message))
        )
        await self._application.initialize()
        await self._application.bot.set_my_commands(
            [
                *(BotCommand(f"autonomous_{action}", description)
                  for action, description in AUTONOMOUS_SETTINGS_COMMANDS.items()),
                BotCommand("autonomous_start", "Запустить автономный диалог"),
                BotCommand("autonomous_stop", "Остановить автономный диалог"),
                BotCommand("autonomous_status", "Проверить автономную сессию"),
                BotCommand("delivery_reviewed", "Закрыть неопределённую отправку после проверки чата"),
                BotCommand("start", "Открыть панель управления"),
                BotCommand("help", "Показать список команд и что они делают"),
                BotCommand("status", "Показать текущее состояние бота"),
                BotCommand("auto_on", "Включить автоответ"),
                BotCommand("auto_off", "Полностью выключить автоответ"),
                BotCommand("pause_30m", "Поставить автоответ на паузу на 30 минут"),
                BotCommand("pause_2h", "Поставить автоответ на паузу на 2 часа"),
                BotCommand("resume", "Снять паузу и вернуть автоответ"),
                BotCommand("show_prompt", "Показать текущий системный промпт"),
                BotCommand("edit_prompt", "Изменить системный промпт"),
                BotCommand("show_session", "Показать состояние внутренней Codex-сессии"),
                BotCommand("reset_session", "Сбросить внутреннюю Codex-сессию"),
                BotCommand("draft_first_message", "Сделать черновик первого сообщения"),
                BotCommand("cancel_input", "Отменить ожидание нового промпта или черновика"),
                BotCommand("last_event", "Показать последнее событие или ошибку"),
            ]
        )
        await self._application.start()
        if self._application.updater is None:
            raise RuntimeError("Control bot updater is unavailable")
        await self._process_offline_stops()
        await self._application.updater.start_polling(drop_pending_updates=False)

    async def _process_offline_stops(self) -> None:
        # Apply safety commands queued while the service was offline before restoring sends.
        offset = None
        while True:
            updates = await self._application.bot.get_updates(offset=offset, timeout=0)
            if not updates:
                return
            for update in updates:
                user = update.effective_user
                message = update.effective_message
                text = (message.text or "").strip() if message else ""
                command = text.split(maxsplit=1)[0].split("@", 1)[0] if text else ""
                if user is None or user.id != self._owner_id:
                    continue
                if command in ("/auto_off", "/off") or text == BUTTON_AUTO_OFF:
                    await self._assistant.set_mode("off")
                elif command == "/autonomous_stop":
                    await self._assistant.stop_autonomous()
                elif command == "/autonomous_schedule_off":
                    await self._assistant.configure_autonomous("schedule_off", [])
                elif command in ("/pause_30m", "/pause_2h", "/pause") or text in (BUTTON_PAUSE_30M, BUTTON_PAUSE_2H):
                    await self._assistant.pause_for(120 if command == "/pause_2h" or text == BUTTON_PAUSE_2H else 30)
            offset = updates[-1].update_id + 1

    def _autonomous_settings_handler(self, action: str) -> Handler:
        async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            try:
                if action == "schedule":
                    result = self._assistant.describe_autonomous_schedule()
                else:
                    result = await self._assistant.configure_autonomous(action, context.args or [])
            except (ValueError, KeyError):
                result = (
                    "Проверьте параметры: длительность 15–300 минут, часы HH:MM HH:MM, "
                    "дни mon,tue,wed,thu,fri,sat,sun, пояс IANA, лимит 1–1000."
                )
            await update.effective_message.reply_text(result)
        return handle

    async def _autonomous_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        result = await self._assistant.start_autonomous(" ".join(context.args or []))
        await update.effective_message.reply_text(result, reply_markup=build_control_keyboard())

    async def _autonomous_stop(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._assistant.stop_autonomous()
        await update.effective_message.reply_text("Автономная сессия остановлена.")

    async def _autonomous_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.effective_message.reply_text(self._assistant.describe_autonomous())

    async def _delivery_reviewed(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.effective_message.reply_text(await self._assistant.acknowledge_delivery_review())

    async def stop(self) -> None:
        if self._application is None:
            return
        if self._application.updater is not None and self._application.updater.running:
            await self._application.updater.stop()
        if self._free_text_task and not self._free_text_task.done():
            self._free_text_task.cancel()
        if self._application.running:
            await self._application.stop()
        await self._application.shutdown()
        self._application = None

    async def send_owner_message(self, text: str) -> None:
        if self._application is None:
            return
        await self._application.bot.send_message(chat_id=self._owner_id, text=text)

    def _owner_only(self, handler: Handler) -> Handler:
        async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            user = update.effective_user
            if user is None or user.id != self._owner_id:
                return
            await handler(update, context)

        return wrapped

    async def _start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        del context
        text = (
            "Панель управления готова.\n\n"
            "Самое полезное:\n"
            "/autonomous_start [задача] · /autonomous_stop · /autonomous_status\n"
            "/autonomous_schedule — расписание; остальные настройки доступны в меню команд\n"
            "/status — текущее состояние\n"
            "/auto_on — включить автоответ\n"
            "/auto_off — выключить автоответ\n"
            "/pause_30m или /pause_2h — временная пауза\n"
            "/resume — снять паузу\n"
            "/edit_prompt — изменить системный промпт\n"
            "/show_session — показать состояние внутренней Codex-сессии\n"
            "/reset_session — сбросить текущую внутреннюю Codex-сессию\n"
            "/draft_first_message — сделать черновик, если хочешь написать первой репликой сам\n"
            "/cancel_input — отменить режим ожидания ввода\n"
            "/last_event — последнее событие\n\n"
            "Можно пользоваться и кнопками под полем ввода."
        )
        await update.effective_message.reply_text(text, reply_markup=build_control_keyboard())

    async def _help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        del context
        text = (
            "Команды управления:\n\n"
            "/autonomous_start [задача] · /autonomous_stop · /autonomous_status\n"
            "/autonomous_schedule — расписание; остальные настройки доступны в меню команд\n"
            "/status — показывает, включён ли автоответ, есть ли пауза и какой чат подключён\n"
            "/auto_on — включает автоответ\n"
            "/auto_off — полностью выключает автоответ\n"
            "/pause_30m — ставит автоответ на паузу на 30 минут\n"
            "/pause_2h — ставит автоответ на паузу на 2 часа\n"
            "/resume — снимает паузу и возвращает автоответ\n"
            "/show_prompt — показывает текущий системный промпт\n"
            "/edit_prompt — следующий обычный текст станет новым системным промптом\n"
            "/show_session — показывает состояние живой Codex-сессии для этого чата\n"
            "/reset_session — сбрасывает живую Codex-сессию; следующее входящее создаст новую\n"
            "/draft_first_message — следующий обычный текст станет задачей для черновика первого сообщения\n"
            "/cancel_input — отменяет режим ожидания нового промпта или черновика\n"
            "/last_event — показывает последнее событие, алерт или ошибку\n\n"
            "Старые короткие команды тоже работают: /on /off /pause /prompt /setprompt /last"
        )
        await update.effective_message.reply_text(text, reply_markup=build_control_keyboard())

    async def _status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        del context
        status = self._assistant.describe_status()
        await update.effective_message.reply_text(status, reply_markup=build_control_keyboard())

    async def _on(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        del context
        await self._assistant.set_mode("auto")
        await update.effective_message.reply_text(
            "Автоответ включён. Новые сообщения снова будут обрабатываться автоматически.",
            reply_markup=build_control_keyboard(),
        )

    async def _off(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        del context
        await self._assistant.set_mode("off")
        await update.effective_message.reply_text(
            "Автоответ выключен. Бот больше не будет отвечать, пока ты не включишь его снова.",
            reply_markup=build_control_keyboard(),
        )

    async def _resume(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        del context
        await self._assistant.resume()
        await update.effective_message.reply_text(
            "Пауза снята. Автоответ снова активен.",
            reply_markup=build_control_keyboard(),
        )

    async def _pause_30m(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        del context
        await self._pause_with_minutes(update, 30)

    async def _pause_2h(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        del context
        await self._pause_with_minutes(update, 120)

    async def _pause(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        minutes = 30
        if context.args:
            raw = context.args[0].strip().lower()
            if raw.endswith("h"):
                minutes = int(raw[:-1]) * 60
            elif raw.endswith("m"):
                minutes = int(raw[:-1])
            else:
                minutes = int(raw)
        await self._pause_with_minutes(update, minutes)

    async def _pause_with_minutes(self, update: Update, minutes: int) -> None:
        await self._assistant.pause_for(minutes)
        until = datetime.now(timezone.utc) + timedelta(minutes=minutes)
        await update.effective_message.reply_text(
            f"Автоответ поставлен на паузу на {minutes} минут.\n"
            f"До: {until.strftime('%Y-%m-%d %H:%M UTC')}",
            reply_markup=build_control_keyboard(),
        )

    async def _prompt(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        del context
        await update.effective_message.reply_text(
            self._assistant.get_prompt(),
            reply_markup=build_control_keyboard(),
        )

    async def _setprompt(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        del context
        self._assistant.mark_prompt_update_pending()
        await update.effective_message.reply_text(
            "Режим редактирования промпта включён.\n"
            "Пришли следующим обычным сообщением новый системный промпт, и я его сохраню.",
            reply_markup=build_control_keyboard(),
        )

    async def _show_session(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        del context
        await update.effective_message.reply_text(
            self._assistant.describe_codex_session(),
            reply_markup=build_control_keyboard(),
        )

    async def _reset_session(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        del context
        await self._assistant.reset_codex_session()
        await update.effective_message.reply_text(
            "Внутренняя Codex-сессия сброшена. Следующее входящее сообщение создаст новую сессию с актуальным контекстом из Telegram.",
            reply_markup=build_control_keyboard(),
        )

    async def _draft_first_message(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        del context
        self._assistant.mark_first_message_draft_pending()
        await update.effective_message.reply_text(
            "Режим черновика первого сообщения включён.\n"
            "Пришли следующим обычным сообщением короткую задачу, например:\n"
            "\"мягко спроси, как прошёл день\" или \"позови на прогулку вечером\".\n"
            "Я верну готовый черновик сюда, без отправки собеседнику.",
            reply_markup=build_control_keyboard(),
        )

    async def _cancel_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        del context
        self._assistant.clear_pending_owner_input()
        await update.effective_message.reply_text(
            "Режим ожидания ввода отменён. Ничего не будет сохранено или сгенерировано, пока ты заново не выберешь действие.",
            reply_markup=build_control_keyboard(),
        )

    async def _last(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        del context
        await update.effective_message.reply_text(
            self._assistant.describe_last_event(),
            reply_markup=build_control_keyboard(),
        )

    async def _message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        text = (update.effective_message.text or "").strip()
        if not text:
            return
        action = BUTTON_TO_ACTION.get(text)
        if action:
            self._assistant.clear_pending_owner_input()
        if action == "status":
            await self._status(update, context)
            return
        elif action == "auto_on":
            await self._on(update, context)
            return
        elif action == "auto_off":
            await self._off(update, context)
            return
        elif action == "pause_30m":
            await self._pause_30m(update, context)
            return
        elif action == "pause_2h":
            await self._pause_2h(update, context)
            return
        elif action == "resume":
            await self._resume(update, context)
            return
        elif action == "show_prompt":
            await self._prompt(update, context)
            return
        elif action == "edit_prompt":
            await self._setprompt(update, context)
            return
        elif action == "show_session":
            await self._show_session(update, context)
            return
        elif action == "reset_session":
            await self._reset_session(update, context)
            return
        elif action == "draft_first_message":
            await self._draft_first_message(update, context)
            return
        elif action == "cancel_input":
            await self._cancel_input(update, context)
            return
        elif action == "last_event":
            await self._last(update, context)
            return
        elif action == "help":
            await self._help(update, context)
            return
        if self._application is not None:
            if self._free_text_task and not self._free_text_task.done():
                await update.effective_message.reply_text("Предыдущий запрос ещё обрабатывается.")
                return
            self._free_text_task = self._application.create_task(self._handle_free_text(update, text))
            return
        await self._handle_free_text(update, text)

    async def _handle_free_text(self, update: Update, text: str) -> None:
        handled, response = await self._assistant.handle_owner_free_text(text)
        if handled:
            await update.effective_message.reply_text(
                response,
                reply_markup=build_control_keyboard(),
            )
