import asyncio
import html
import io
import logging

import qrcode
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BotCommand,
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from amnezia_manager import AmneziaManager
from config import AmneziaServer, load_settings
from vpn_common import NAME_RE, BaseManager, ClientConfig, VpnError
from wg_manager import WireGuardManager

admin_router = Router(name="admin")
guest_router = Router(name="guest")

HELP_TEXT = (
    "Бот для создания клиентов VPN (WireGuard / Amnezia).\n\n"
    "/new — создать нового клиента (можно сразу: <code>/new Test001-name</code>)\n"
    "/list — список клиентов\n"
    "/get &lt;имя&gt; — повторно получить конфиг\n"
    "/cancel — отменить ввод\n\n"
    "Сервер и приложение (для Amnezia) выбираются кнопками."
)
NAME_RULES = "Только латиница, цифры, «_» и «-», не длиннее 15 символов."

# Форматы конфига для сервера Amnezia
FORMATS = {
    "awg": "AmneziaWG",
    "vpn": "AmneziaVPN",
}

Servers = dict[str, BaseManager]


class Flow(StatesGroup):
    server = State()
    fmt = State()
    name = State()


class ServerCb(CallbackData, prefix="srv"):
    key: str


class FormatCb(CallbackData, prefix="fmt"):
    fmt: str


def servers_kb(servers: Servers) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=m.title, callback_data=ServerCb(key=key).pack())]
        for key, m in servers.items()
    ])


def formats_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=title, callback_data=FormatCb(fmt=fmt).pack())
        for fmt, title in FORMATS.items()
    ]])


def make_qr(text: str) -> bytes:
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=10, border=4)
    qr.add_data(text)
    qr.make(fit=True)
    buf = io.BytesIO()
    qr.make_image(fill_color="black", back_color="white").save(buf, format="PNG")
    return buf.getvalue()


async def send_client(
    message: Message, mgr: BaseManager, client: ClientConfig, fmt: str | None, title: str
) -> None:
    caption = (
        f"{title}\n\n"
        f"Сервер: <b>{html.escape(mgr.title)}</b>\n"
        f"Имя: <code>{html.escape(client.name)}</code>\n"
        f"IPv4: <code>{client.ipv4 or '—'}</code>\n"
        f"IPv6: <code>{client.ipv6 or '—'}</code>\n"
        f"Файл на сервере: <code>{html.escape(client.server_path)}</code>"
    )

    if isinstance(mgr, AmneziaManager) and fmt == "vpn":
        try:
            key = mgr.vpn_key(client)
        except VpnError as e:
            await message.answer(f"❌ {html.escape(str(e))}")
            return
        await message.answer_document(
            BufferedInputFile(key.encode(), filename=f"{client.name}.vpn"),
            caption=caption + "\n\nAmneziaVPN: «Добавить сервер» → вставьте ключ "
                              "из следующего сообщения или откройте этот файл.",
        )
        if len(key) <= 4096:
            await message.answer(f"<code>{key}</code>")
        return

    app = "AmneziaWG" if isinstance(mgr, AmneziaManager) else "WireGuard"
    await message.answer_document(
        BufferedInputFile(client.config.encode(), filename=f"{client.name}.conf"),
        caption=caption,
    )
    await message.answer_photo(
        BufferedInputFile(make_qr(client.config), filename=f"{client.name}.png"),
        caption=f"QR-код для приложения {app}",
    )


async def create_client(message: Message, mgr: BaseManager, name: str, fmt: str | None) -> None:
    status = await message.answer(
        f"⏳ Создаю клиента <code>{html.escape(name)}</code> на «{html.escape(mgr.title)}»…"
    )
    try:
        client = await mgr.add_client(name)
    except VpnError as e:
        await status.edit_text(f"❌ {html.escape(str(e))}")
        return
    await status.delete()
    await send_client(message, mgr, client, fmt, "✅ Клиент создан")


async def fetch_client(message: Message, mgr: BaseManager, name: str, fmt: str | None) -> None:
    try:
        client = await mgr.get_client(name)
    except VpnError as e:
        await message.answer(f"❌ {html.escape(str(e))}")
        return
    await send_client(message, mgr, client, fmt, "📄 Конфиг клиента")


async def show_list(message: Message, mgr: BaseManager) -> None:
    try:
        names = await mgr.list_clients()
    except VpnError as e:
        await message.answer(f"❌ {html.escape(str(e))}")
        return
    if not names:
        await message.answer(f"На «{html.escape(mgr.title)}» клиентов пока нет.")
        return

    lines = [f"Клиенты «{html.escape(mgr.title)}» ({len(names)}):"] + [
        f"{i}. <code>{html.escape(n)}</code>" for i, n in enumerate(names, 1)
    ]
    chunk: list[str] = []
    for line in lines:
        if sum(len(x) + 1 for x in chunk) + len(line) > 4000:
            await message.answer("\n".join(chunk))
            chunk = []
        chunk.append(line)
    await message.answer("\n".join(chunk))


async def proceed(message: Message, state: FSMContext, servers: Servers) -> None:
    """Запрашивает недостающее (сервер → приложение → имя) и выполняет действие."""
    data = await state.get_data()
    action = data["action"]

    key = data.get("server")
    if key is None:
        if len(servers) > 1:
            await state.set_state(Flow.server)
            await message.answer("Выберите сервер:", reply_markup=servers_kb(servers))
            return
        key = next(iter(servers))
    mgr = servers[key]

    fmt = data.get("fmt")
    if action != "list" and isinstance(mgr, AmneziaManager) and fmt is None:
        await state.set_state(Flow.fmt)
        await message.answer("Для какого приложения сделать конфиг?", reply_markup=formats_kb())
        return

    name = data.get("name")
    if action != "list" and name is None:
        await state.set_state(Flow.name)
        prompt = "Введите имя нового клиента" if action == "new" else "Введите имя клиента"
        await message.answer(f"{prompt}.\n{NAME_RULES}\n\n/cancel — отмена")
        return

    await state.clear()
    if action == "list":
        await show_list(message, mgr)
    elif action == "new":
        await create_client(message, mgr, name, fmt)
    else:
        await fetch_client(message, mgr, name, fmt)


async def start_flow(
    message: Message, state: FSMContext, servers: Servers, action: str, name: str | None = None
) -> None:
    await state.clear()
    if name is not None and not NAME_RE.fullmatch(name):
        await message.answer(f"❌ Недопустимое имя. {NAME_RULES}")
        return
    await state.update_data(action=action, name=name)
    await proceed(message, state, servers)


@admin_router.message(CommandStart())
@admin_router.message(Command("help"))
async def cmd_help(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(HELP_TEXT)


@admin_router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Отменено.")


@admin_router.message(Command("new"))
async def cmd_new(
    message: Message, command: CommandObject, state: FSMContext, servers: Servers
) -> None:
    await start_flow(message, state, servers, "new", (command.args or "").strip() or None)


@admin_router.message(Command("list"))
async def cmd_list(message: Message, state: FSMContext, servers: Servers) -> None:
    await start_flow(message, state, servers, "list")


@admin_router.message(Command("get"))
async def cmd_get(
    message: Message, command: CommandObject, state: FSMContext, servers: Servers
) -> None:
    await start_flow(message, state, servers, "get", (command.args or "").strip() or None)


# Регистрируются после команд, чтобы /list и др. работали и во время ввода
@admin_router.message(Flow.name, F.text)
async def got_name(message: Message, state: FSMContext, servers: Servers) -> None:
    name = message.text.strip()
    if not NAME_RE.fullmatch(name):
        await message.answer(f"❌ Недопустимое имя. {NAME_RULES}\nПопробуйте ещё раз или /cancel.")
        return
    await state.update_data(name=name)
    await proceed(message, state, servers)


@admin_router.message(Flow.server)
@admin_router.message(Flow.fmt)
async def waiting_button(message: Message) -> None:
    await message.answer("Выберите вариант кнопкой выше или /cancel.")


@admin_router.callback_query(Flow.server, ServerCb.filter())
async def on_server(
    callback: CallbackQuery, callback_data: ServerCb, state: FSMContext, servers: Servers
) -> None:
    mgr = servers.get(callback_data.key)
    if mgr is None or not isinstance(callback.message, Message):
        await callback.answer("Кнопка устарела, начните заново.", show_alert=True)
        return
    await callback.answer()
    await state.update_data(server=callback_data.key)
    await callback.message.edit_text(f"Сервер: <b>{html.escape(mgr.title)}</b>")
    await proceed(callback.message, state, servers)


@admin_router.callback_query(Flow.fmt, FormatCb.filter())
async def on_format(
    callback: CallbackQuery, callback_data: FormatCb, state: FSMContext, servers: Servers
) -> None:
    if callback_data.fmt not in FORMATS or not isinstance(callback.message, Message):
        await callback.answer("Кнопка устарела, начните заново.", show_alert=True)
        return
    await callback.answer()
    await state.update_data(fmt=callback_data.fmt)
    await callback.message.edit_text(f"Приложение: <b>{FORMATS[callback_data.fmt]}</b>")
    await proceed(callback.message, state, servers)


@admin_router.callback_query()
async def stale_callback(callback: CallbackQuery) -> None:
    await callback.answer("Кнопка устарела, начните заново.", show_alert=True)


@admin_router.message()
async def fallback(message: Message) -> None:
    await message.answer(HELP_TEXT)


@guest_router.message()
async def deny(message: Message) -> None:
    user_id = message.from_user.id if message.from_user else "?"
    await message.answer(f"⛔ Доступ запрещён.\nВаш Telegram ID: <code>{user_id}</code>")


@guest_router.callback_query()
async def deny_callback(callback: CallbackQuery) -> None:
    await callback.answer("⛔ Доступ запрещён.", show_alert=True)


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = load_settings()

    servers: Servers = {
        s.key: AmneziaManager(s) if isinstance(s, AmneziaServer) else WireGuardManager(s)
        for s in settings.servers
    }

    bot = Bot(settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()

    admin_filter = F.from_user.id.in_(settings.allowed_user_ids)
    admin_router.message.filter(admin_filter)
    admin_router.callback_query.filter(admin_filter)
    dp.include_routers(admin_router, guest_router)

    await bot.set_my_commands([
        BotCommand(command="new", description="Создать клиента"),
        BotCommand(command="list", description="Список клиентов"),
        BotCommand(command="get", description="Получить конфиг клиента"),
        BotCommand(command="cancel", description="Отмена"),
    ])
    await dp.start_polling(bot, servers=servers)


if __name__ == "__main__":
    asyncio.run(main())
