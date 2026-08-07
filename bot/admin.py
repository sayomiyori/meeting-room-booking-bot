from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from backend.core.access import ACCESS_DENIED, ADMIN_ONLY
from backend.core.database import async_session_factory
from backend.core.exceptions import AppError
from backend.models import UserRole
from backend.repositories import UserRepository
from backend.services.booking import RoomService
from bot.book_clarify import clear_clarification_if_any

router = Router(name="admin")


class AddRoomFSM(StatesGroup):
    name = State()
    capacity = State()
    description = State()


class EditRoomFSM(StatesGroup):
    value = State()


async def _require_admin(telegram_id: int) -> bool:
    async with async_session_factory() as session:
        user = await UserRepository(session).get_by_telegram_id(telegram_id)
        await session.commit()
    return user is not None and user.role == UserRole.admin


def _admin_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Список комнат", callback_data="admin:rooms")],
            [InlineKeyboardButton(text="Добавить комнату", callback_data="admin:add")],
            [InlineKeyboardButton(text="Закрыть", callback_data="admin:close")],
        ]
    )


def _rooms_keyboard(rooms: list) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for room in rooms:
        status = "" if room.active else " [выкл]"
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{room.name}{status} (до {room.capacity})",
                    callback_data=f"admin:room:{room.id}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="« Назад", callback_data="admin:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _room_actions_keyboard(room_id: int, *, active: bool) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text="Редактировать",
                callback_data=f"admin:edit:{room_id}",
            )
        ],
    ]
    if active:
        rows.append(
            [
                InlineKeyboardButton(
                    text="Деактивировать",
                    callback_data=f"admin:deactivate:{room_id}",
                )
            ]
        )
    else:
        rows.append(
            [
                InlineKeyboardButton(
                    text="Активировать",
                    callback_data=f"admin:activate:{room_id}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="« К списку", callback_data="admin:rooms")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _edit_fields_keyboard(room_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Название", callback_data=f"admin:editfield:{room_id}:name")],
            [
                InlineKeyboardButton(
                    text="Вместимость",
                    callback_data=f"admin:editfield:{room_id}:capacity",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Описание",
                    callback_data=f"admin:editfield:{room_id}:description",
                )
            ],
            [InlineKeyboardButton(text="« Назад", callback_data=f"admin:room:{room_id}")],
        ]
    )


@router.message(Command("admin"))
async def cmd_admin(message: Message, state: FSMContext) -> None:
    await clear_clarification_if_any(state)
    if message.from_user is None:
        return
    if not await _require_admin(message.from_user.id):
        await message.answer(ADMIN_ONLY)
        return
    await state.clear()
    await message.answer("Панель администратора", reply_markup=_admin_menu_keyboard())


@router.callback_query(F.data == "admin:close")
async def cb_admin_close(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.from_user is None:
        return
    if not await _require_admin(callback.from_user.id):
        await callback.answer(ADMIN_ONLY, show_alert=True)
        return
    await state.clear()
    await callback.answer()
    if callback.message:
        await callback.message.edit_text("Админ-меню закрыто.")


@router.callback_query(F.data == "admin:menu")
async def cb_admin_menu(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.from_user is None:
        return
    if not await _require_admin(callback.from_user.id):
        await callback.answer(ADMIN_ONLY, show_alert=True)
        return
    await state.clear()
    await callback.answer()
    if callback.message:
        await callback.message.edit_text(
            "Панель администратора",
            reply_markup=_admin_menu_keyboard(),
        )


@router.callback_query(F.data == "admin:rooms")
async def cb_admin_rooms(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.from_user is None:
        return
    if not await _require_admin(callback.from_user.id):
        await callback.answer(ADMIN_ONLY, show_alert=True)
        return
    await state.clear()
    async with async_session_factory() as session:
        rooms = await RoomService(session).list_rooms(active_only=False)
        await session.commit()
    await callback.answer()
    if callback.message:
        if not rooms:
            await callback.message.edit_text(
                "Комнат пока нет.",
                reply_markup=_admin_menu_keyboard(),
            )
            return
        await callback.message.edit_text(
            "Комнаты:",
            reply_markup=_rooms_keyboard(rooms),
        )


@router.callback_query(F.data.startswith("admin:room:"))
async def cb_admin_room(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.from_user is None or callback.data is None:
        return
    if not await _require_admin(callback.from_user.id):
        await callback.answer(ADMIN_ONLY, show_alert=True)
        return
    await state.clear()
    room_id = int(callback.data.rsplit(":", 1)[1])
    async with async_session_factory() as session:
        rooms = await RoomService(session).list_rooms(active_only=False)
        await session.commit()
    room = next((r for r in rooms if r.id == room_id), None)
    await callback.answer()
    if room is None or callback.message is None:
        return
    status = "активна" if room.active else "деактивирована"
    text = (
        f"<b>{room.name}</b>\n"
        f"Вместимость: {room.capacity}\n"
        f"Описание: {room.description or '—'}\n"
        f"Статус: {status}\n"
        f"Фото: положите файл в <code>backend/media/rooms/</code> и задайте "
        f"<code>photo_url</code> вручную при необходимости."
    )
    await callback.message.edit_text(
        text,
        reply_markup=_room_actions_keyboard(room.id, active=room.active),
    )


@router.callback_query(F.data == "admin:add")
async def cb_admin_add(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.from_user is None:
        return
    if not await _require_admin(callback.from_user.id):
        await callback.answer(ADMIN_ONLY, show_alert=True)
        return
    await state.set_state(AddRoomFSM.name)
    await callback.answer()
    if callback.message:
        await callback.message.edit_text(
            "Добавление комнаты.\n"
            "Введите название.\n"
            "Фото на этом шаге пропускаем — положите файл в "
            "<code>backend/media/rooms/</code> вручную."
        )


@router.message(StateFilter(AddRoomFSM.name))
async def add_room_name(message: Message, state: FSMContext) -> None:
    if message.from_user is None or not await _require_admin(message.from_user.id):
        await message.answer(ACCESS_DENIED if message.from_user else ADMIN_ONLY)
        await state.clear()
        return
    name = (message.text or "").strip()
    if not name:
        await message.answer("Название не может быть пустым. Введите ещё раз:")
        return
    await state.update_data(name=name)
    await state.set_state(AddRoomFSM.capacity)
    await message.answer("Введите вместимость (целое число):")


@router.message(StateFilter(AddRoomFSM.capacity))
async def add_room_capacity(message: Message, state: FSMContext) -> None:
    if message.from_user is None or not await _require_admin(message.from_user.id):
        await state.clear()
        return
    raw = (message.text or "").strip()
    try:
        capacity = int(raw)
    except ValueError:
        await message.answer("Нужно целое число. Введите вместимость:")
        return
    if capacity < 1:
        await message.answer("Вместимость должна быть >= 1:")
        return
    await state.update_data(capacity=capacity)
    await state.set_state(AddRoomFSM.description)
    await message.answer("Введите описание (или «-» чтобы оставить пустым):")


@router.message(StateFilter(AddRoomFSM.description))
async def add_room_description(message: Message, state: FSMContext) -> None:
    if message.from_user is None or not await _require_admin(message.from_user.id):
        await state.clear()
        return
    raw = (message.text or "").strip()
    description = "" if raw in {"-", "—"} else raw
    data = await state.get_data()
    await state.clear()
    async with async_session_factory() as session:
        try:
            room = await RoomService(session).create_room(
                name=data["name"],
                capacity=int(data["capacity"]),
                description=description,
            )
            await session.commit()
        except AppError as exc:
            await session.rollback()
            await message.answer(exc.message)
            return
    await message.answer(
        f"Комната «{room.name}» добавлена (id={room.id}).\n"
        "Фото: файл в <code>backend/media/rooms/</code>.",
        reply_markup=_admin_menu_keyboard(),
    )


@router.callback_query(F.data.startswith("admin:deactivate:"))
async def cb_deactivate(callback: CallbackQuery) -> None:
    if callback.from_user is None or callback.data is None:
        return
    if not await _require_admin(callback.from_user.id):
        await callback.answer(ADMIN_ONLY, show_alert=True)
        return
    room_id = int(callback.data.rsplit(":", 1)[1])
    async with async_session_factory() as session:
        try:
            room = await RoomService(session).set_room_active(room_id, False)
            await session.commit()
        except AppError as exc:
            await session.rollback()
            await callback.answer(exc.message, show_alert=True)
            return
    await callback.answer("Деактивирована")
    if callback.message:
        await callback.message.edit_text(
            f"Комната «{room.name}» деактивирована. Исторические брони сохранены.",
            reply_markup=_admin_menu_keyboard(),
        )


@router.callback_query(F.data.startswith("admin:activate:"))
async def cb_activate(callback: CallbackQuery) -> None:
    if callback.from_user is None or callback.data is None:
        return
    if not await _require_admin(callback.from_user.id):
        await callback.answer(ADMIN_ONLY, show_alert=True)
        return
    room_id = int(callback.data.rsplit(":", 1)[1])
    async with async_session_factory() as session:
        try:
            room = await RoomService(session).set_room_active(room_id, True)
            await session.commit()
        except AppError as exc:
            await session.rollback()
            await callback.answer(exc.message, show_alert=True)
            return
    await callback.answer("Активирована")
    if callback.message:
        await callback.message.edit_text(
            f"Комната «{room.name}» снова активна.",
            reply_markup=_admin_menu_keyboard(),
        )


@router.callback_query(F.data.startswith("admin:edit:"))
async def cb_edit_menu(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.from_user is None or callback.data is None:
        return
    if not await _require_admin(callback.from_user.id):
        await callback.answer(ADMIN_ONLY, show_alert=True)
        return
    # avoid matching admin:editfield
    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer()
        return
    room_id = int(parts[2])
    await state.clear()
    await callback.answer()
    if callback.message:
        await callback.message.edit_text(
            "Что изменить?",
            reply_markup=_edit_fields_keyboard(room_id),
        )


@router.callback_query(F.data.startswith("admin:editfield:"))
async def cb_edit_field(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.from_user is None or callback.data is None:
        return
    if not await _require_admin(callback.from_user.id):
        await callback.answer(ADMIN_ONLY, show_alert=True)
        return
    _, _, room_id_s, field = callback.data.split(":", 3)
    await state.set_state(EditRoomFSM.value)
    await state.update_data(room_id=int(room_id_s), field=field)
    labels = {"name": "название", "capacity": "вместимость (число)", "description": "описание"}
    await callback.answer()
    if callback.message:
        await callback.message.edit_text(f"Введите новое значение ({labels.get(field, field)}):")


@router.message(StateFilter(EditRoomFSM.value))
async def edit_room_value(message: Message, state: FSMContext) -> None:
    if message.from_user is None or not await _require_admin(message.from_user.id):
        await state.clear()
        return
    data = await state.get_data()
    field = data.get("field")
    room_id = int(data.get("room_id", 0))
    raw = (message.text or "").strip()
    kwargs: dict = {}
    if field == "name":
        kwargs["name"] = raw
    elif field == "capacity":
        try:
            kwargs["capacity"] = int(raw)
        except ValueError:
            await message.answer("Нужно целое число. Попробуйте ещё раз:")
            return
    elif field == "description":
        kwargs["description"] = "" if raw in {"-", "—"} else raw
    else:
        await state.clear()
        await message.answer("Неизвестное поле.")
        return

    await state.clear()
    async with async_session_factory() as session:
        try:
            room = await RoomService(session).update_room(room_id, **kwargs)
            await session.commit()
        except AppError as exc:
            await session.rollback()
            await message.answer(exc.message)
            return
    await message.answer(
        f"Комната «{room.name}» обновлена.",
        reply_markup=_admin_menu_keyboard(),
    )
