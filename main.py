import logging
import sqlite3
import os
import re
from telegram import Update, ParseMode
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackContext, ConversationHandler

# Импортируем настройки
from config import BOT_TOKEN, ADMIN_IDS, CHANNEL_SCAM, CHANNEL_TRUSTED, CHANNEL_ID

# Состояния
(
    WAITING_FOR_TARGET, WAITING_FOR_NOTE,
    WAITING_FOR_PROOF,
    WAITING_FOR_TRUSTED_TARGET, WAITING_FOR_TRUSTED_NOTE,
    WAITING_FOR_REMOVE_TARGET
) = range(6)

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# === ЭКРАНИРОВАНИЕ ДЛЯ MARKDOWN_V2 ===
def escape_markdown_v2(text: str) -> str:
    if not text:
        return ""
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    return re.sub(f'([{re.escape(escape_chars)}])', r'\\\1', str(text))

def init_db():
    conn = sqlite3.connect('scam_base.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS scammers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT,
            note TEXT,
            proof_url TEXT
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS trusted (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT,
            note TEXT
        )
    ''')
    conn.commit()
    conn.close()

def find_user_in_table(target: str, table: str):
    conn = sqlite3.connect('scam_base.db')
    cursor = conn.cursor()
    target_clean = target.lstrip('@').lower()
    is_digit = target_clean.isdigit()

    if table == 'scammers':
        cursor.execute('''
            SELECT user_id, username, note, proof_url FROM scammers
            WHERE (username IS NOT NULL AND LOWER(username) = ?)
               OR (user_id IS NOT NULL AND user_id = ?)
        ''', (target_clean, int(target_clean) if is_digit else None))
    else:
        cursor.execute('''
            SELECT user_id, username, note, NULL as proof_url FROM trusted
            WHERE (username IS NOT NULL AND LOWER(username) = ?)
               OR (user_id IS NOT NULL AND user_id = ?)
        ''', (target_clean, int(target_clean) if is_digit else None))

    result = cursor.fetchone()
    conn.close()
    return result

def add_user_to_table(user_id, username, note, table, proof_url=None):
    conn = sqlite3.connect('scam_base.db')
    cursor = conn.cursor()
    if table == 'scammers':
        cursor.execute(f'''
            INSERT INTO {table} (user_id, username, note, proof_url)
            VALUES (?, ?, ?, ?)
        ''', (user_id, username, note, proof_url))
    else:
        cursor.execute(f'''
            INSERT INTO {table} (user_id, username, note)
            VALUES (?, ?, ?)
        ''', (user_id, username, note))
    conn.commit()
    conn.close()

def remove_user_from_table(target: str, table: str):
    conn = sqlite3.connect('scam_base.db')
    cursor = conn.cursor()
    target_clean = target.lstrip('@').lower()
    is_digit = target_clean.isdigit()

    if is_digit:
        cursor.execute(f'DELETE FROM {table} WHERE user_id = ?', (int(target_clean),))
    else:
        cursor.execute(f'DELETE FROM {table} WHERE LOWER(username) = ?', (target_clean,))

    deleted = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return deleted

def move_user_between_tables(target: str, from_table: str, to_table: str):
    info = find_user_in_table(target, from_table)
    if not info:
        return False
    user_id, username, note, proof_url = info
    remove_user_from_table(target, from_table)
    add_user_to_table(user_id, username, note, to_table, proof_url)
    return True

def get_user_info_block(username: str, user_id: int, note: str = "") -> str:
    """
    Формирует блок с информацией о пользователе.
    username: может быть None
    user_id: может быть None
    """
    # Формируем строку отображения
    if username and user_id:
        display = f"@{escape_markdown_v2(username)} \\| ID: {user_id}"
    elif username:
        display = f"@{escape_markdown_v2(username)} \\| ID: неизвестен"
    elif user_id:
        display = f"ID: {user_id}"
    else:
        display = "неизвестен"

    msg = f"🟢 {display}"
    if note:
        msg += f"\n\n📝 Примечание:\n{escape_markdown_v2(note)}"
    return msg

def get_social_footer(proof_url: str = None) -> str:
    footer = (
        "💬 Наш чат: @loneasBASE"
    )
    if proof_url:
        footer += f"\n🔗 Пруфы: [ссылка]({proof_url})"
    return footer

# === ОСНОВНЫЕ КОМАНДЫ ===

def start(update: Update, context: CallbackContext):
    msg = (
        "🛡️ *Скам\\-база Лонеаса*\n\n"
        "🔍 Отправьте `@username` для проверки\\.\n"
        "✅ Бот покажет статус: _скамер_ или _проверенный гарант_\\.\n"
    )
    update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN_V2)

def handle_check_command(update: Update, context: CallbackContext):
    if not context.args:
        update.message.reply_text("❌ Используйте: /check @username или /check ID", parse_mode=ParseMode.MARKDOWN_V2)
        return

    query = context.args[0].strip()
    if not query:
        update.message.reply_text("❌ Пустой запрос\\.", parse_mode=ParseMode.MARKDOWN_V2)
        return

    _handle_user_check(update, context, query)

def handle_check_in_pm(update: Update, context: CallbackContext):
    # Обрабатываем @username в личке
    query = update.message.text.strip()
    if query.startswith('@'):
        _handle_user_check(update, context, query)

def _handle_user_check(update: Update, context: CallbackContext, query: str):
    # СНАЧАЛА ПРОВЕРЕННЫЕ
    trust_info = find_user_in_table(query, 'trusted')
    if trust_info:
        user_id, username, note, _ = trust_info
        display = f"@{escape_markdown_v2(username)} \\| ID: {user_id}" if username else f"ID: {user_id}"
        status_line = "💡`Статус`: *__ГАРАНТ__* ✅\n\n🟢*Данный пользователь является гарантом\\! Следующий вывод был основан на его репутации\\.*"

        # Добавляем примечание, если оно есть
        note_line = f"\n\n📝 Примечание:\n{escape_markdown_v2(note)}" if note else ""

        msg = (
            f"👤 Пользователь: {display}\n\n"
            f"{status_line}{note_line}\n\n"
            f"> Наш чат: @loneasBASE"
        )
        with open('guarantee.jpg', 'rb') as photo:
            update.message.reply_photo(photo=photo, caption=msg, parse_mode=ParseMode.MARKDOWN_V2)
        return

    # ПОТОМ СКАМЕРЫ
    scam_info = find_user_in_table(query, 'scammers')
    if scam_info:
        user_id, username, note, proof_url = scam_info
        display = f"@{escape_markdown_v2(username)} \\| ID: {user_id}" if username else f"ID: {user_id}"
        status_line = "⚠️ НАЙДЕН В СКАМ\\-БАЗЕ\\!⚠️\n\n💡`Статус`: *__МОШЕННИК__*❌\n \n🔴*Пользователь — скамер\\. Найден в базе @LoneasBasebot\\. Не в коем случае не контактируйте с данным человеком, не ведите на его уловки\\.*"

        # Добавляем примечание, если оно есть
        note_line = f"\n\n📝 Примечание:\n{escape_markdown_v2(note)}" if note else ""

        proof_line = f">Пруфы: [ссылка]({proof_url})" if proof_url else ""
        chat_line = "Наш чат: @loneasBASE"

        if proof_url:
            footer = proof_line + "\n>" + chat_line
        else:
            footer = chat_line

        msg = (
            f"👤 Пользователь: {display}\n\n"
            f"{status_line}{note_line}\n\n"
            f"{footer}"
        )

        with open('scammer.jpg', 'rb') as photo:
            update.message.reply_photo(photo=photo, caption=msg, parse_mode=ParseMode.MARKDOWN_V2)
        return

    # НЕ НАЙДЕН
    clean_query = query.lstrip('@')
    if clean_query.isdigit():
        display = f"ID: {clean_query}"
        user_id = clean_query
        username = None
    else:
        username_clean = clean_query
        user_id = None
        try:
            chat = context.bot.get_chat(f"@{username_clean}")
            if chat.type == 'private':
                user_id = chat.id
                username = username_clean
                display = f"@{escape_markdown_v2(username)} \\| ID: {user_id}"
            else:
                username = username_clean
                display = f"@{escape_markdown_v2(username)} \\| ID: неизвестен"
        except Exception as e:
            logger.warning(f"Не удалось получить ID для @{username_clean}: {e}")
            username = username_clean
            display = f"@{escape_markdown_v2(username)} \\| ID: неизвестен"

    # Формируем статус и текст
    escaped_username = escape_markdown_v2(username) if username else "неизвестен"
    status_line = f"💡Статус: НЕ НАЙДЕН 🔍\n\n⚫️Пользователь @{escaped_username} не был найден в нашей базе\\. Данная личность не проверена\\.\n\n📝 Примечание:\\nРекомендуется быть осторожным и использовать услуги проверенных гарантов \\- /mm\\."

    msg = (
        f"⚫️ {display}\n\n"
        f"{status_line}\n\n"
        f"> Наш чат: @loneasBASE"
    )
    with open('unknown.jpg', 'rb') as photo:
        update.message.reply_photo(photo=photo, caption=msg, parse_mode=ParseMode.MARKDOWN_V2)

# === КОМАНДА /mm ===

def mm_command(update: Update, context: CallbackContext):
    msg = (
        "✅ *Проверенные гарантии*\n\n"
        "Перед оплатой всегда используйте одного из них:\n"
        "• @guarantee1\n"
        "• @guarantee2\n"
        "• @guarantee3\n\n"
        "⚠️ Не забывайте проверять их статус через бота!"
    )
    update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN_V2)

# === АВТООБНОВЛЕНИЕ ID ===

def auto_update_user_id_on_message(update: Update, context: CallbackContext):
    user = update.effective_user
    if not user or not user.username:
        return  # Нет пользователя или нет username — нечего обновлять

    # Проверим, есть ли этот username в базе без ID
    conn = sqlite3.connect('scam_base.db')
    cursor = conn.cursor()

    # Проверяем в trusted
    cursor.execute("SELECT user_id FROM trusted WHERE username = ? AND user_id IS NULL", (user.username,))
    result = cursor.fetchone()
    if result:
        cursor.execute("UPDATE trusted SET user_id = ? WHERE username = ?", (user.id, user.username))
        logger.info(f"Обновлён ID для @{user.username} (trusted): {user.id}")
    else:
        # Проверяем в scammers
        cursor.execute("SELECT user_id FROM scammers WHERE username = ? AND user_id IS NULL", (user.username,))
        result2 = cursor.fetchone()
        if result2:
            cursor.execute("UPDATE scammers SET user_id = ? WHERE username = ?", (user.id, user.username))
            logger.info(f"Обновлён ID для @{user.username} (scammers): {user.id}")

    conn.commit()
    conn.close()

# === ПУБЛИКАЦИЯ В КАНАЛ ===

def publish_to_channel(context: CallbackContext, user_id, username, note, proof_url, is_scam):
    if is_scam:
        channel = CHANNEL_SCAM
        status = "МОШЕННИК ❌"
        extra = f"\n🔗 Пруфы: [ссылка]({proof_url})" if proof_url else ""
    else:
        channel = CHANNEL_TRUSTED
        status = "ГАРАНТ ✅"
        extra = ""

    msg = (
        f"👤 Пользователь: @{escape_markdown_v2(username)} \\| ID: {user_id}\n"
        f"💡Статус: {status}\n"
        f"📝 Примечание: {escape_markdown_v2(note)}{extra}"
    )

    try:
        context.bot.send_message(chat_id=channel, text=msg, parse_mode=ParseMode.MARKDOWN_V2)
    except Exception as e:
        logger.error(f"Не удалось опубликовать в канал {channel}: {e}")

# === ОСТАЛЬНЫЕ ФУНКЦИИ ===

def add_scammer_start(update: Update, context: CallbackContext):
    if update.effective_user.id not in ADMIN_IDS:
        update.message.reply_text("🚫 Только админы могут добавлять скамеров\\.", parse_mode=ParseMode.MARKDOWN_V2)
        return ConversationHandler.END
    update.message.reply_text("👤 Отправьте `@username` или `ID` скамера:")
    return WAITING_FOR_TARGET

def receive_scammer_target(update: Update, context: CallbackContext):
    target = update.message.text.strip().lstrip('@')
    if not target:
        update.message.reply_text("❌ Некорректно\\. Попробуйте снова\\.")
        return WAITING_FOR_TARGET

    if find_user_in_table(target, 'trusted'):
        move_user_between_tables(target, 'trusted', 'scammers')
        update.message.reply_text(f"ℹ️ Пользователь @{target} перемещён из «проверенных» в «скамеры»\\.")

    context.user_data['target'] = target
    update.message.reply_text("✏️ Примечание \\(или /skip\\):")
    return WAITING_FOR_NOTE

def receive_scammer_note(update: Update, context: CallbackContext):
    note = update.message.text if update.message.text != '/skip' else ""
    context.user_data['note'] = note
    update.message.reply_text("🔗 Отправьте ссылку на пруфы \\(или /skip\\):")
    return WAITING_FOR_PROOF

def skip_scammer_note(update: Update, context: CallbackContext):
    context.user_data['note'] = ""
    update.message.reply_text("🔗 Отправьте ссылку на пруфы \\(или /skip\\):")
    return WAITING_FOR_PROOF

def receive_scammer_proof(update: Update, context: CallbackContext):
    proof_url = update.message.text.strip()
    if proof_url.lower() == '/skip':
        context.user_data['proof_url'] = None
    elif proof_url.startswith(('http://', 'https://')):
        context.user_data['proof_url'] = proof_url
    else:
        update.message.reply_text("❌ Некорректная ссылка\\. Отправьте снова или /skip:")
        return WAITING_FOR_PROOF

    _save_to_db(update, context, 'scammers', publish=True)
    return ConversationHandler.END

def skip_scammer_proof(update: Update, context: CallbackContext):
    context.user_data['proof_url'] = None
    _save_to_db(update, context, 'scammers', publish=True)
    return ConversationHandler.END

def add_trusted_start(update: Update, context: CallbackContext):
    if update.effective_user.id not in ADMIN_IDS:
        update.message.reply_text("🚫 Только админы могут добавлять проверенных\\.", parse_mode=ParseMode.MARKDOWN_V2)
        return ConversationHandler.END
    update.message.reply_text("👤 Отправьте `@username` или `ID` проверенного пользователя:")
    return WAITING_FOR_TRUSTED_TARGET

def receive_trusted_target(update: Update, context: CallbackContext):
    target = update.message.text.strip().lstrip('@')
    if not target:
        update.message.reply_text("❌ Некорректно\\. Попробуйте снова\\.")
        return WAITING_FOR_TRUSTED_TARGET

    if find_user_in_table(target, 'scammers'):
        move_user_between_tables(target, 'scammers', 'trusted')
        update.message.reply_text(f"ℹ️ Пользователь @{target} перемещён из «скамеров» в «проверенные»\\.")

    context.user_data['target'] = target
    update.message.reply_text("✏️ Информация \\(или /skip\\):")
    return WAITING_FOR_TRUSTED_NOTE

def receive_trusted_note(update: Update, context: CallbackContext):
    note = update.message.text if update.message.text != '/skip' else ""
    context.user_data['note'] = note
    _save_to_db(update, context, 'trusted', publish=True)
    return ConversationHandler.END

def skip_trusted_note(update: Update, context: CallbackContext):
    context.user_data['note'] = ""
    _save_to_db(update, context, 'trusted', publish=True)
    return ConversationHandler.END

def remove_start(update: Update, context: CallbackContext):
    if update.effective_user.id not in ADMIN_IDS:
        update.message.reply_text("🚫 Только админы могут удалять\\.", parse_mode=ParseMode.MARKDOWN_V2)
        return ConversationHandler.END
    update.message.reply_text("🗑️ Отправьте `@username` или `ID` для удаления:")
    return WAITING_FOR_REMOVE_TARGET

def receive_remove_target(update: Update, context: CallbackContext):
    target = update.message.text.strip()
    deleted_scam = remove_user_from_table(target, 'scammers')
    deleted_trust = remove_user_from_table(target, 'trusted')

    if deleted_scam and deleted_trust:
        msg = "⚠️ Удалён из обеих баз\\."
    elif deleted_scam:
        msg = "🗑️ Удалён из скам\\-базы\\."
    elif deleted_trust:
        msg = "🗑️ Удалён из проверенных\\."
    else:
        msg = "❌ Не найден ни в одной базе\\."

    update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN_V2)
    return ConversationHandler.END

def list_scam(update: Update, context: CallbackContext):
    if update.effective_user.id not in ADMIN_IDS:
        update.message.reply_text("🚫 Только админы\\.", parse_mode=ParseMode.MARKDOWN_V2)
        return

    conn = sqlite3.connect('scam_base.db')
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, username FROM scammers")
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        update.message.reply_text("Скам\\-база пуста\\.", parse_mode=ParseMode.MARKDOWN_V2)
        return

    lines = [f"@{uname}" if uname else f"ID: {uid}" for uid, uname in rows]
    escaped_lines = [escape_markdown_v2(line) for line in lines]
    text = "*🔴 Список скамеров:*\n" + "\n".join(escaped_lines)
    update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN_V2)

def list_trusted(update: Update, context: CallbackContext):
    conn = sqlite3.connect('scam_base.db')
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, username FROM trusted")
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        update.message.reply_text("Нет проверенных пользователей\\.", parse_mode=ParseMode.MARKDOWN_V2)
        return

    lines = [f"@{uname}" if uname else f"ID: {uid}" for uid, uname in rows]
    escaped_lines = [escape_markdown_v2(line) for line in lines]
    text = "*🟢 Проверенные пользователи:*\n" + "\n".join(escaped_lines)
    update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN_V2)

def help_command(update: Update, context: CallbackContext):
    msg = (
        "ℹ️ *Справка по командам*\n\n"
        "*Для всех:*\n"
        "• `/check @username` или `/check ID` — проверить статус\\.\n"
        "• `/listtrusted` — список проверенных гарантов\n\n"
        
        "*Только для админов:*\n"
        "• `/addscam` — добавить скамера\n"
        "• `/addtrusted` — добавить проверенного\n"
        "• `/remove` — удалить из базы\n"
        "• `/listscam` — список скамеров\n"
        "• `/listtrusted` — список проверенных\n"
        "• `/help` — эта справка\n\n"
    )
    update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN_V2)

def _save_to_db(update: Update, context: CallbackContext, table: str, publish=False):
    target = context.user_data['target']
    note = context.user_data.get('note', '')
    proof_url = context.user_data.get('proof_url')  # Только для скамеров

    user_id = None
    username = None

    # Определяем тип цели
    if target.isdigit():
        user_id = int(target)
        username = None
    else:
        username = target
        # Пытаемся получить ID по username
        try:
            chat = context.bot.get_chat(username)
            if chat.type == 'private':
                user_id = chat.id
            else:
                user_id = None  # Это канал или группа
        except Exception as e:
            logger.warning(f"Не удалось получить ID для @{username}: {e}")
            user_id = None

    # Удаляем из другой таблицы, если есть
    other_table = 'trusted' if table == 'scammers' else 'scammers'
    if find_user_in_table(target, other_table):
        move_user_between_tables(target, other_table, table)

    # Сохраняем в базу
    if table == 'scammers':
        add_user_to_table(user_id, username, note, table, proof_url)
    else:
        add_user_to_table(user_id, username, note, table)

    # Формируем сообщение для админа
    display_parts = []
    if username:
        display_parts.append(f"@{username}")
    if user_id:
        display_parts.append(f"ID {user_id}")
    display = " | ".join(display_parts) if display_parts else target

    name = "скамер" if table == 'scammers' else "проверенный"
    update.message.reply_text(f"✅ {name.capitalize()} успешно добавлен: {display}")

    # Публикуем в канал, если нужно
    if publish and username and user_id and note:
        publish_to_channel(context, user_id, username, note, proof_url, table == 'scammers')

def cancel(update: Update, context: CallbackContext):
    update.message.reply_text("❌ Операция отменена\\.", parse_mode=ParseMode.MARKDOWN_V2)
    return ConversationHandler.END

def error_handler(update: object, context: CallbackContext):
    logger.error("Exception while handling an update:", exc_info=context.error)

# === ФУНКЦИЯ ДОБАВЛЕНИЯ ЧЕРЕЗ КАНАЛ ===

def handle_channel_message(update: Update, context: CallbackContext):
    # Проверяем, что это канал
    if update.effective_chat.type != 'channel':
        return

    # Проверяем, что это наш канал
    if str(update.effective_chat.id) != CHANNEL_ID:
        return

    text = update.effective_message.text or ""
    if not text.startswith('/addscam') and not text.startswith('/addtrusted'):
        return

    # Разделяем команду и остальную часть
    parts = text.split(' ', 1)
    if len(parts) < 2:
        return

    command_full = parts[0]  # /addscam или /addtrusted
    rest = parts[1]  # @lox1234 | лох | https://t.me/durov

    # Теперь делим по " | "
    data_parts = rest.split(' | ')
    if len(data_parts) < 2:
        return

    target = data_parts[0].lstrip('@').strip()  # @lox1234 → lox1234
    note = data_parts[1].strip() if len(data_parts) > 1 else ""
    proof_url = data_parts[2].strip() if len(data_parts) > 2 else None

    if command_full.startswith('/addscam'):
        # Проверяем, есть ли уже в базе
        if find_user_in_table(target, 'trusted'):
            move_user_between_tables(target, 'trusted', 'scammers')
        # Добавляем
        add_user_to_table(None, target, note, 'scammers', proof_url)
        context.bot.send_message(chat_id=update.effective_chat.id, text=f"✅ Скамер @{target} добавлен.")
    elif command_full.startswith('/addtrusted'):
        if find_user_in_table(target, 'scammers'):
            move_user_between_tables(target, 'scammers', 'trusted')
        add_user_to_table(None, target, note, 'trusted')
        context.bot.send_message(chat_id=update.effective_chat.id, text=f"✅ Гарант @{target} добавлен.")

# === ЗАПУСК ===

def main():
    init_db()
    updater = Updater(BOT_TOKEN, use_context=True)
    dp = updater.dispatcher

    # Диалоги
    dp.add_handler(ConversationHandler(
        entry_points=[CommandHandler('addscam', add_scammer_start)],
        states={
            WAITING_FOR_TARGET: [MessageHandler(Filters.text & ~Filters.command, receive_scammer_target)],
            WAITING_FOR_NOTE: [
                MessageHandler(Filters.text & ~Filters.command, receive_scammer_note),
                CommandHandler('skip', skip_scammer_note)
            ],
            WAITING_FOR_PROOF: [
                MessageHandler(Filters.text & ~Filters.command, receive_scammer_proof),
                CommandHandler('skip', skip_scammer_proof)
            ]
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    ))

    dp.add_handler(ConversationHandler(
        entry_points=[CommandHandler('addtrusted', add_trusted_start)],
        states={
            WAITING_FOR_TRUSTED_TARGET: [MessageHandler(Filters.text & ~Filters.command, receive_trusted_target)],
            WAITING_FOR_TRUSTED_NOTE: [
                MessageHandler(Filters.text & ~Filters.command, receive_trusted_note),
                CommandHandler('skip', skip_trusted_note)
            ]
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    ))

    dp.add_handler(ConversationHandler(
        entry_points=[CommandHandler('remove', remove_start)],
        states={
            WAITING_FOR_REMOVE_TARGET: [MessageHandler(Filters.text & ~Filters.command, receive_remove_target)]
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    ))

    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("help", help_command))
    dp.add_handler(CommandHandler("mm", mm_command))
    dp.add_handler(CommandHandler("listscam", list_scam))
    dp.add_handler(CommandHandler("listtrusted", list_trusted))
    dp.add_handler(CommandHandler("check", handle_check_command))

    # Обработка @username только в личке
    dp.add_handler(MessageHandler(Filters.text & Filters.chat_type.private & ~Filters.command, handle_check_in_pm))

    # Автообновление ID на каждое сообщение
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, auto_update_user_id_on_message))

    # Обработка сообщений в канале
    dp.add_handler(MessageHandler(Filters.text & Filters.chat_type.channel, handle_channel_message))

    dp.add_error_handler(error_handler)

    updater.start_polling()
    updater.idle()

if __name__ == '__main__':

    main()
