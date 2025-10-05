import logging
import sqlite3
import os
import re
import time
import json
import uuid
from telegram import Update, ParseMode, InlineQueryResultPhoto, InputTextMessageContent, InlineQueryResultArticle
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackContext, ConversationHandler, InlineQueryHandler
import shutil
from datetime import datetime, timedelta
import glob
from apscheduler.schedulers.background import BackgroundScheduler
import requests

# === ЗАГРУЗКА КОНФИГУРАЦИИ ===
CONFIG_FILE = 'config.json'
UB_REQUEST_PREFIX = "ubreq_"
UB_RESPONSE_PREFIX = "ubresp_"
COMMUNICATION_DIR = "."
def load_settings():
    """Загружает настройки из config.json."""
    if not os.path.exists(CONFIG_FILE):
        raise FileNotFoundError(f"Файл конфигурации {CONFIG_FILE} не найден. Пожалуйста, запустите setup.py для его создания.")
    try:
        with open(CONFIG_FILE, 'r') as f:
            config = json.load(f)
    except json.JSONDecodeError as e:
        raise ValueError(f"Ошибка чтения {CONFIG_FILE}: {e}")

    # Присваиваем значения глобальным переменным
    global BOT_TOKEN, ADMIN_IDS, CHANNEL_SCAM, CHANNEL_TRUSTED, CHANNEL_ID
    BOT_TOKEN = config['bot_token']
    ADMIN_IDS = set(config['admin_ids']) # Преобразуем список в множество
    CHANNEL_SCAM = config['channel_scam']
    CHANNEL_TRUSTED = config['channel_trusted']
    CHANNEL_ID = config['channel_id']

# Загружаем настройки при импорте модуля
load_settings()
# Состояния
(
    WAITING_FOR_TARGET, WAITING_FOR_NOTE,
    WAITING_FOR_PROOF,
    WAITING_FOR_TRUSTED_TARGET, WAITING_FOR_TRUSTED_NOTE,
    WAITING_FOR_REMOVE_TARGET
) = range(6)
# Дебаг
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# === ЭКРАНИРОВАНИЕ ДЛЯ MARKDOWN_V2 ===
def escape_markdown_v2(text: str) -> str:
    if not text:
        return ""
    # Все спецсимволы MarkdownV2
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    return re.sub(f'([{re.escape(escape_chars)}])', r'\\\1', str(text))

# Инициализация ДБ
def init_db():
    conn = sqlite3.connect('scam_base.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS scammers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER UNIQUE,
            username TEXT,
            original_username TEXT,
            note TEXT,
            proof_url TEXT
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS trusted (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER UNIQUE,
            username TEXT,
            original_username TEXT,
            note TEXT
        )
    ''')
# Новая таблица для хранения юзеров, которые писали боту
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT
        )
    ''')
# Новая таблица для хранения информации от юзербота
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_profiles (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            date_created TEXT,  -- Здесь храним "Dec 2013"
            is_bot INTEGER,
            all_usernames TEXT
        )
    ''')
# Новая таблица для подсчёта поисков
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS search_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            search_query TEXT,
            search_date TEXT
        )
    ''')
    conn.commit()
    conn.close()
# Сохранение пользователя, если необходимо
def save_user_if_needed(update: Update):
    user = update.effective_user
    if not user or not user.username:
        return  # Нет пользователя или нет username — нечего обновлять
    conn = sqlite3.connect('scam_base.db')
    cursor = conn.cursor()
    cursor.execute('''
        INSERT OR REPLACE INTO users (user_id, username) VALUES (?, ?)
    ''', (user.id, user.username))
    conn.commit()
    conn.close()
# Все юзернеймы по айди
def get_all_usernames_by_user_id(user_id):
    """
    Получает все юзернеймы пользователя по его ID.
    """
    if not user_id:
        return []
    conn = sqlite3.connect('scam_base.db')
    cursor = conn.cursor()
    cursor.execute('SELECT all_usernames FROM user_profiles WHERE user_id = ?', (user_id,))
    row = cursor.fetchone()
    conn.close()
    if row and row[0]:
        # all_usernames хранится как строка, разделённая запятыми
        return [uname.strip() for uname in row[0].split(',') if uname.strip()]
    return []
# Логи поиска
def log_search(user_id, query):
    from datetime import datetime
    date = datetime.now().isoformat()
    conn = sqlite3.connect('scam_base.db')
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO search_log (user_id, search_query, search_date) VALUES (?, ?, ?)
    ''', (user_id, query, date))
    conn.commit()
    conn.close()
# Сколько раз в базе искали человекчка
def get_search_count(user_id):
    if not user_id:
        return 0
    conn = sqlite3.connect('scam_base.db')
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM search_log WHERE user_id = ?', (user_id,))
    count = cursor.fetchone()[0]
    conn.close()
    return count
# Мейн функция для сохранения чела в базу через юзбота
def save_user_profile_from_userbot(user_id, profile):
    all_usernames = profile.get('all_usernames', [])
    if isinstance(all_usernames, list):
        all_usernames_str = ','.join(all_usernames)
    elif isinstance(all_usernames, str):
        all_usernames_str = all_usernames
    else:
        all_usernames_str = profile.get('username', '') or ''
    account_creation = profile.get('account_creation', 'неизвестно')  # <<< НОВОЕ
    conn = sqlite3.connect('scam_base.db')
    cursor = conn.cursor()
    cursor.execute('''
        INSERT OR REPLACE INTO user_profiles (
            user_id, username, first_name, last_name, date_created, is_bot, all_usernames
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (
        user_id,
        profile.get('username'),
        profile.get('first_name'),
        profile.get('last_name'),
        account_creation,  # <<< НОВОЕ
        1 if profile.get('is_bot') else 0,
        all_usernames_str
    ))
    conn.commit()
    conn.close()
def get_user_info_via_userbot(query: str) -> dict:
    """
    Отправляет запрос юзерботу через файл и ждёт ответ.
    Адаптировано для надёжной работы в Linux.
    """
    # Генерируем уникальный ID для этой операции
    request_uuid = str(uuid.uuid4())
    
    # Формируем имена файлов
    request_filename = f"{UB_REQUEST_PREFIX}{request_uuid}.txt"
    response_filename = f"{UB_RESPONSE_PREFIX}{request_uuid}.json"
    
    # Полные пути к файлам
    full_request_path = os.path.join(COMMUNICATION_DIR, request_filename)
    full_response_path = os.path.join(COMMUNICATION_DIR, response_filename)
    
    logger.info(f"[Main->UB] Отправка запроса: '{query}' (UUID: {request_uuid})")

    max_retries = 3
    retry_delay = 0.1 # Начальная задержка 100мс

    for attempt in range(1, max_retries + 1):
        try:
            # 1. Создаём файл запроса
            # Формат: query\nresponse_filename (относительное имя файла ответа)
            with open(full_request_path, 'w', encoding='utf-8') as f:
                f.write(f"{query}\n{response_filename}")
            logger.debug(f"[Main->UB] Файл запроса создан: {full_request_path} (попытка {attempt})")

            # 2. Ждём появления файла ответа с таймаутом
            timeout = 30  # секунд
            start_time = time.time()
            while not os.path.exists(full_response_path):
                if time.time() - start_time > timeout:
                    logger.error(f"[Main->UB] Таймаут ожидания ответа от юзербота для UUID {request_uuid} (попытка {attempt})")
                    # Пытаемся удалить файл запроса, если он всё ещё есть
                    try:
                        os.remove(full_request_path)
                        logger.debug(f"[Main->UB] Файл запроса удалён по таймауту: {full_request_path}")
                    except OSError as oe:
                        logger.debug(f"[Main->UB] Не удалось удалить файл запроса по таймауту {full_request_path}: {oe}")
                    if attempt < max_retries:
                        logger.info(f"[Main->UB] Повторная попытка {attempt + 1}/{max_retries} через {retry_delay}s...")
                        time.sleep(retry_delay)
                        retry_delay *= 2 # Экспоненциальная задержка
                        break # Выходим из while, чтобы перейти к следующей попытке for
                    else:
                        return {"error": "timeout"}
                time.sleep(0.1) # Проверяем каждые 100мс
            
            if not os.path.exists(full_response_path):
                 # Если вышли из while по таймауту и файл так и не появился, продолжаем цикл for
                 continue

            logger.debug(f"[Main->UB] Файл ответа найден: {full_response_path} (попытка {attempt})")

            # 3. Читаем данные из файла ответа
            read_max_retries = 3
            read_retry_delay = 0.1
            for read_attempt in range(1, read_max_retries + 1):
                try:
                    with open(full_response_path, 'r', encoding='utf-8') as f:
                        data = f.read()
                    logger.debug(f"[Main->UB] Данные из файла ответа прочитаны (попытка чтения {read_attempt}/{read_max_retries}).")
                    break # Успешно прочитано, выходим из цикла чтения
                except PermissionError as pe:
                    logger.warning(f"[Main->UB] Попытка {read_attempt}/{read_max_retries}: Ошибка доступа к файлу {full_response_path}: {pe}. Повтор через {read_retry_delay}s...")
                    if read_attempt < read_max_retries:
                        time.sleep(read_retry_delay)
                        read_retry_delay *= 2 # Экспоненциальная задержка
                    else:
                        raise # Если все попытки исчерпаны, выбрасываем исключение
                except Exception as e:
                    logger.error(f"[Main->UB] Неожиданная ошибка при чтении файла {full_response_path}: {e}")
                    raise # Для других ошибок сразу выбрасываем
            
            # 4. Парсим JSON
            try:
                result = json.loads(data)
                logger.info(f"[Main->UB] Ответ от юзербота для UUID {request_uuid} успешно получен и распарсен.")
            except json.JSONDecodeError as je:
                logger.error(f"[Main->UB] Ошибка декодирования JSON из ответа юзербота: {je}. Данные: {data[:100]}...")
                result = {"error": f"json_decode_error: {je}"}

            # 5. Удаляем файлы запроса и ответа
            try:
                os.remove(full_request_path)
                logger.debug(f"[Main->UB] Файл запроса удалён: {full_request_path}")
            except OSError as oe:
                logger.warning(f"[Main->UB] Не удалось удалить файл запроса {full_request_path}: {oe}")
            try:
                os.remove(full_response_path)
                logger.debug(f"[Main->UB] Файл ответа удалён: {full_response_path}")
            except OSError as oe:
                logger.warning(f"[Main->UB] Не удалось удалить файл ответа {full_response_path}: {oe}")

            return result

        except Exception as e:
            logger.error(f"[Main->UB] Ошибка при взаимодействии с юзерботом для запроса '{query}' (попытка {attempt}): {e}", exc_info=True)
            # Пытаемся удалить файлы в случае ошибки
            for file_path in [full_request_path, full_response_path]:
                try:
                    os.remove(file_path)
                    logger.debug(f"[Main->UB] Файл {file_path} удалён из-за ошибки.")
                except (OSError, UnboundLocalError):
                    pass
            
            if attempt < max_retries:
                 logger.info(f"[Main->UB] Повторная попытка {attempt + 1}/{max_retries} через {retry_delay}s...")
                 time.sleep(retry_delay)
                 retry_delay *= 2 # Экспоненциальная задержка
            else:
                 logger.error(f"[Main->UB] Все попытки взаимодействия с юзерботом для '{query}' исчерпаны.")
                 return {"error": f"critical_error_after_{max_retries}_attempts: {e}"}

    # Этот return теоретически недостижим, но добавим для полноты
    return {"error": "unreachable_code_reached"}
# Поиск чела в базе
def find_user_in_table(target: str, table: str):
    conn = sqlite3.connect('scam_base.db')
    cursor = conn.cursor()
    target_clean = target.lstrip('@').lower()
    is_digit = target_clean.isdigit()
    # === ВСЕГДА ИЩЕМ ПО user_id ===
    if is_digit:
        user_id = int(target_clean)
    else:
        # Если это username — ищем user_id в user_profiles
        cursor.execute('SELECT user_id FROM user_profiles WHERE LOWER(username) = ?', (target_clean,))
        result = cursor.fetchone()
        if result:
            user_id = result[0]
        else:
            # Не нашли user_id — возвращаем None
            conn.close()
            return None
    # Теперь ищем в таблице по user_id
    if table == 'scammers':
        cursor.execute('''
            SELECT user_id, username, original_username, note, proof_url FROM scammers
            WHERE user_id = ?
        ''', (user_id,))
    else:
        cursor.execute('''
            SELECT user_id, username, original_username, note, NULL as proof_url FROM trusted
            WHERE user_id = ?
        ''', (user_id,))
    result = cursor.fetchone()
    conn.close()
    return result
# Добавление чела в базу
def add_user_to_table(user_id, username, original_username, note, table, proof_url=None):
    # Убираем @, если есть
    if username:
        username = username.lstrip('@')
    # original_username тоже может быть None, если добавляем по ID
    if original_username:
        original_username = original_username.lstrip('@')
    conn = sqlite3.connect('scam_base.db')
    cursor = conn.cursor()
    if table == 'scammers':
        cursor.execute(f'''
            INSERT OR REPLACE INTO {table} (user_id, username, original_username, note, proof_url)
            VALUES (?, ?, ?, ?, ?)
        ''', (user_id, username, original_username, note, proof_url))
    else:
        cursor.execute(f'''
            INSERT OR REPLACE INTO {table} (user_id, username, original_username, note)
            VALUES (?, ?, ?, ?)
        ''', (user_id, username, original_username, note))
    conn.commit()
    conn.close()
# Удаление из базы
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
    user_id, username, original_username, note, proof_url = info
    remove_user_from_table(target, from_table)
    add_user_to_table(user_id, username, original_username, note, to_table, proof_url)
    return True
# Блок с инфой об юзере
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
        msg += f"\n📝 Примечание:\n{escape_markdown_v2(note)}"
    return msg
# Чат + канал
def get_social_footer(proof_url: str = None) -> str:
    footer = (
        "💬 Наш чат: @loneasBASE"
    )
    if proof_url:
        footer += f"\n🔗 Пруфы: [ссылка]({proof_url})"
    return footer

# === ОСНОВНЫЕ КОМАНДЫ ===
def start(update: Update, context: CallbackContext):
    # Сохраняем юзера при /start
    save_user_if_needed(update)
    msg = (
        "🛡️ *Скам\\-база Лонеаса*\n"
        "🔍 Отправьте `@username` для проверки\\.\n"
        "✅ Бот покажет статус: _скамер_ или _проверенный гарант_\\.\n"
    )
    update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN_V2)
# /check
def handle_check_command(update: Update, context: CallbackContext):
    if not context.args:
        update.message.reply_text("❌ Используйте: /check @username или /check ID", parse_mode=ParseMode.MARKDOWN_V2)
        return
    query = context.args[0].strip()
    if not query:
        update.message.reply_text("❌ Пустой запрос\\.", parse_mode=ParseMode.MARKDOWN_V2)
        return
    # Логируем поиск
    log_search(None, query)
    _handle_user_check(update, context, query)
# @username in DM
def handle_check_in_pm(update: Update, context: CallbackContext):
    # Обрабатываем @username в личке
    query = update.message.text.strip()
    if query.startswith('@'):
        log_search(None, query)
        _handle_user_check(update, context, query)

# Мейн функция вывода состояния чела в базе

def _handle_user_check(update: Update, context: CallbackContext, query: str):
    clean_query = query.lstrip('@')
    is_id = clean_query.isdigit()
    # === СНАЧАЛА ПОЛУЧАЕМ user_id ===
    user_id = None
    username = None
    if is_id:
        user_id = int(clean_query)
        # Пробуем получить username из user_profiles
        conn = sqlite3.connect('scam_base.db')
        cursor = conn.cursor()
        cursor.execute('SELECT username FROM user_profiles WHERE user_id = ?', (user_id,))
        result = cursor.fetchone()
        conn.close()
        if result:
            username = result[0]
    else:
        username = clean_query
        # Пробуем получить ID из user_profiles
        conn = sqlite3.connect('scam_base.db')
        cursor = conn.cursor()
        cursor.execute('SELECT user_id FROM user_profiles WHERE LOWER(username) = ?', (username.lower(),))
        result = cursor.fetchone()
        conn.close()
        if result:
            user_id = result[0]
        else:
            # Пробуем получить ID через юзербота
            user_info = get_user_info_via_userbot(query)
            if user_info and 'error' not in user_info:
                user_id = user_info['id']
                username = user_info.get('username')
                # Сохраняем в user_profiles
                save_user_profile_from_userbot(user_id, user_info)

    # === ЛОГИРУЕМ ПОИСК (всегда с user_id, если есть) ===
    log_search(user_id, query)
    # === Если не получили user_id — не ищем ===
    if not user_id:
        update.message.reply_text("❌ Не удалось получить ID пользователя\\. Попробуйте позже \\(Возможно\\, это канал или чат переходник\\, пришлите юзернейм из этого канала или чата\\, возможно сработает\\)\\.", parse_mode=ParseMode.MARKDOWN_V2)
        return

    # === ПОЛУЧАЕМ ПРОФИЛЬ ИЗ user_profiles ===
    conn = sqlite3.connect('scam_base.db')
    cursor = conn.cursor()
    cursor.execute('SELECT user_id, username, first_name, last_name, date_created, is_bot, all_usernames FROM user_profiles WHERE user_id = ?', (user_id,))
    profile_row = cursor.fetchone()
    conn.close()

    # === АВТООБНОВЛЕНИЕ ПРОФИЛЯ (если пользователь найден в базе скам/гарант) ===
    # Проверим, есть ли пользователь в scammers или trusted
    is_user_in_db = find_user_in_table(str(user_id), 'scammers') or find_user_in_table(str(user_id), 'trusted')

    if is_user_in_db and profile_row:
        # Пользователь есть в нашей базе, проверим полноту профиля
        db_user_id, db_username, db_first_name, db_last_name, db_date_created, db_is_bot, db_all_usernames = profile_row
        
        # Определим, достаточно ли данных. Например, если first_name и date_created пусты:
        # Можно задать свои критерии, например, если нет first_name ИЛИ нет date_created
        profile_needs_update = not db_first_name or db_first_name == 'неизвестно' or not db_date_created or db_date_created == 'неизвестно' or not db_all_usernames

        if profile_needs_update:
            logger.info(f"Профиль пользователя {db_user_id} ({db_username}) неполный. Попытка обновления через юзербота...")
            
            # Получаем username для запроса юзербота. Приоритет: из user_profiles -> из users -> из запроса
            username_for_request = db_username
            if not username_for_request:
                 # Пробуем получить username из таблицы users
                conn = sqlite3.connect('scam_base.db')
                cursor = conn.cursor()
                cursor.execute("SELECT username FROM users WHERE user_id = ?", (user_id,))
                result = cursor.fetchone()
                conn.close()
                if result:
                    username_for_request = result[0] # Уже без @

            # Если так и не нашли username, используем user_id (если юзербот это поддерживает)
            if not username_for_request:
                username_for_request = f"id{user_id}"

            # Запрашиваем данные через юзербота
            user_info = get_user_info_via_userbot(f"@{username_for_request}" if not username_for_request.startswith('id') else username_for_request)
            
            if user_info and 'error' not in user_info:
                # Обновляем профиль в базе
                save_user_profile_from_userbot(user_id, user_info)
                logger.info(f"Профиль пользователя {user_id} успешно обновлён.")
                
                # Обновляем profile_row для дальнейшего использования
                conn = sqlite3.connect('scam_base.db')
                cursor = conn.cursor()
                cursor.execute('SELECT user_id, username, first_name, last_name, date_created, is_bot, all_usernames FROM user_profiles WHERE user_id = ?', (user_id,))
                profile_row = cursor.fetchone()
                conn.close()
            else:
                 logger.warning(f"Не удалось получить обновлённые данные для пользователя {user_id} (@{username_for_request}). Используются старые данные.")

    # === ИСПОЛЬЗУЕМ profile_row ДЛЯ ФОРМИРОВАНИЯ ОТВЕТА ===
    if profile_row:
        # Распаковываем обновлённые (или старые) данные
        _, _, first_name, last_name, date_created, is_bot, all_usernames = profile_row
    else:
        # Профиля нет совсем, используем дефолтные значения
        first_name = 'неизвестно'
        last_name = ''
        date_created = 'неизвестно'
        all_usernames = ''
        # is_bot и username не используются напрямую здесь, но можно сохранить
        # username = username # из более раннего кода
        # is_bot = 0 # предположим
        
    # Получаем количество поисков
    search_count = get_search_count(user_id)

    # Формируем список юзернеймов
    if all_usernames:
        all_usernames_list = [uname.strip() for uname in all_usernames.split(',') if uname.strip()]
        all_usernames_str = ', '.join([f"@{escape_markdown_v2(uname)}" for uname in all_usernames_list])
    else:
        all_usernames_str = f"@{escape_markdown_v2(username)}" if username else "неизвестен"

    # === Ищем в trusted ===
    trust_info = find_user_in_table(str(user_id), 'trusted')
    if trust_info:
        t_user_id, t_username, t_original_username, t_note, _ = trust_info
        # Если искали по @username — показываем его
        if not is_id and clean_query:
            display_username = clean_query  # тот, по которому искали
        else:
            display_username = t_username if t_username else "неизвестен"
        display = f"@{escape_markdown_v2(display_username)} \\| ID: `{t_user_id}`" if display_username and t_user_id else f"ID: {t_user_id}\n"
        status_line = "💡`Статус`: *__ГАРАНТ__* ✅\n\n🟢*Данный пользователь является гарантом\\! Следующий вывод был основан на его репутации\\.*"
        note_line = f"\n\n🔓Дополнительная информация🔑:\n\n{escape_markdown_v2(t_note)}" if t_note else ""
        info_lines = (
            f"🔮 Имя: {escape_markdown_v2(first_name)} {escape_markdown_v2(last_name)}\n"
            f"📓 Юзернеймы: {all_usernames_str}\n"
            f"🪬 Дата создания: {date_created}\n"
        )
        msg = (
            f"👤 Пользователь: {display}\n\n"
            f"{info_lines}\n"
            f"{status_line}{note_line}\n\n"
            f">Наш чат: @loneasBASE\n>Наш канал: @loneasproofs"
        )
        with open('guarantee.jpg', 'rb') as photo:
            update.message.reply_photo(photo=photo, caption=msg, parse_mode=ParseMode.MARKDOWN_V2)
        return

    # === Ищем в scammers ===
    scam_info = find_user_in_table(str(user_id), 'scammers')
    if scam_info:
        s_user_id, s_username, s_original_username, s_note, s_proof_url = scam_info
        # Если искали по @username — показываем его
        if not is_id and clean_query:
            display_username = clean_query  # тот, по которому искали
        else:
            display_username = s_username if s_username else "неизвестен"
        display = f"@{escape_markdown_v2(display_username)} \\| ID: `{s_user_id}`" if display_username and s_user_id else f"ID: {s_user_id}\n"
        status_line = "⚠️ НАЙДЕН В СКАМ\\-БАЗЕ\\!⚠️\n\n💡`Статус`: *__МОШЕННИК__*❌\n\n🔴*Пользователь — скамер\\. Найден в базе @LoneasBasebot\\. Ни в коем случае не контактируйте с данным человеком, не ведитесь на его уловки\\.*"
        note_line = f"\n\n🔓Дополнительная информация🔑:\n\n{escape_markdown_v2(s_note)}" if s_note else ""
        info_lines = (
            f"🔮 Имя: {escape_markdown_v2(first_name)} {escape_markdown_v2(last_name)}\n"
            f"📓 Юзернеймы: {all_usernames_str}\n"
            f"🪬 Дата создания: {date_created}\n"
            f"🔍 Искали в базе {search_count} раз\\(а\\)\n"
        )
        proof_line = f">Пруфы: [ссылка]({s_proof_url})" if s_proof_url else ""
        chat_line = ">Наш чат: @loneasBASE\n>Наш канал: @loneasproofs"
        if s_proof_url:
            footer = proof_line + "\n>" + chat_line
        else:
            footer = chat_line
        msg = (
            f"👤 Пользователь: {display}\n\n"
            f"{info_lines}\n"
            f"{status_line}{note_line}\n\n"
            f"{footer}"
        )
        with open('scammer.jpg', 'rb') as photo:
            update.message.reply_photo(photo=photo, caption=msg, parse_mode=ParseMode.MARKDOWN_V2)
        return

    # === Не найден ===
    # Если искали по @username — показываем его
    if not is_id and clean_query:
        display_username = clean_query  # тот, по которому искали
    else:
        display_username = username if username else "неизвестен"
    display = f"@{escape_markdown_v2(display_username)} \\| ID: `{user_id}`" if display_username and user_id else f"ID: {user_id}"
    status_line = f"💡`Статус`: *__НЕ НАЙДЕН__* 🔍\n\n⚫️Пользователь @{escape_markdown_v2(display_username)} не был найден в нашей базе\\. Данная личность не проверена\\.\n\n🔓Дополнительная информация🔑:\n\nРекомендуется быть осторожным и использовать услуги проверенных гарантов \\- /listtrusted\\.\n"
    info_lines = (
        f"🔮 Имя: {escape_markdown_v2(first_name)} {escape_markdown_v2(last_name)}\n"
        f"📓 Юзернеймы: {all_usernames_str}\n"
        f"🪬 Дата создания: {date_created}\n"
        f"🔍 Искали в базе: {search_count} раз\\(а\\)\n"
    )
    msg = (
        f"👤 Пользователь: {display}\n\n"
        f"{info_lines}\n"
        f"{status_line}\n"
        f">Наш чат: @loneasBASE\n>Наш канал: @loneasproofs"
    )
    with open('unknown.jpg', 'rb') as photo:
        update.message.reply_photo(photo=photo, caption=msg, parse_mode=ParseMode.MARKDOWN_V2)

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

# === INLINE РЕЖИМ ===
def inline_query(update: Update, context: CallbackContext):
    query = update.inline_query.query
    user_id = update.inline_query.from_user.id
    logger.info(f"ПОЛУЧЕН INLINE ЗАПРОС: '{query}' от user_id: {user_id}")

    if not query:
        logger.info("Inline запрос пустой, возвращаем пустой результат.")
        update.inline_query.answer(results=[])
        return

    query = query.strip()
    clean_query = query.lstrip('@')
    is_id = clean_query.isdigit()

    # === СНАЧАЛА ПОЛУЧАЕМ user_id ===
    user_id_to_search = None
    username_to_display = None
    first_name = 'неизвестно'
    last_name = ''
    date_created = 'неизвестно'
    all_usernames = ''
    search_count = 0

    if is_id:
        user_id_to_search = int(clean_query)
        # Пробуем получить username и профиль из user_profiles
        conn = sqlite3.connect('scam_base.db')
        cursor = conn.cursor()
        cursor.execute('SELECT username FROM user_profiles WHERE user_id = ?', (user_id_to_search,))
        result = cursor.fetchone()
        if result:
            username_to_display = result[0]

        cursor.execute('SELECT first_name, last_name, date_created, all_usernames FROM user_profiles WHERE user_id = ?', (user_id_to_search,))
        profile = cursor.fetchone()
        if profile:
            first_name = profile[0] if profile[0] else 'неизвестно'
            last_name = profile[1] if profile[1] else ''
            date_created = profile[2] if profile and profile[2] else 'неизвестно'
            all_usernames = profile[3] if profile[3] else ''
        conn.close()

        # Получаем количество поисков
        search_count = get_search_count(user_id_to_search)

    else:
        username_to_display = clean_query
        # Пробуем получить ID из user_profiles
        conn = sqlite3.connect('scam_base.db')
        cursor = conn.cursor()
        cursor.execute('SELECT user_id FROM user_profiles WHERE LOWER(username) = ?', (username_to_display.lower(),))
        result = cursor.fetchone()
        if result:
            user_id_to_search = result[0]

            # Получаем профиль из user_profiles
            cursor.execute('SELECT first_name, last_name, date_created, all_usernames FROM user_profiles WHERE user_id = ?', (user_id_to_search,))
            profile = cursor.fetchone()
            if profile:
                first_name = profile[0] if profile[0] else 'неизвестно'
                last_name = profile[1] if profile[1] else ''
                date_created = profile[2] if profile and profile[2] else 'неизвестно'
                all_usernames = profile[3] if profile[3] else ''
        conn.close()

        # Получаем количество поисков
        if user_id_to_search:
            search_count = get_search_count(user_id_to_search)

        if not user_id_to_search:
            # Пробуем получить ID через юзербота
            user_info = get_user_info_via_userbot(query)
            if user_info and 'error' not in user_info:
                user_id_to_search = user_info['id']
                username_to_display = user_info.get('username') # <<< username может быть с | или др. символами
                # Сохраняем в user_profiles
                save_user_profile_from_userbot(user_id_to_search, user_info)
                # Обновляем переменные профиля
                first_name = user_info.get('first_name', 'неизвестно')
                last_name = user_info.get('last_name', '')
                date_created = user_info.get('account_creation', 'неизвестно') # Используем account_creation
                all_usernames_list = user_info.get('all_usernames', [])
                if isinstance(all_usernames_list, list):
                    all_usernames = ','.join(all_usernames_list)
                else:
                    all_usernames = user_info.get('username', '') or ''

                # Получаем количество поисков
                search_count = get_search_count(user_id_to_search)


    # === Если не получили user_id — возвращаем пустой результат ===
    if not user_id_to_search:
        update.inline_query.answer(results=[], cache_time=0)
        return

    # Формируем список юзернеймов
    if all_usernames:
        all_usernames_list = [uname.strip() for uname in all_usernames.split(',') if uname.strip()]
        all_usernames_str = ', '.join([f"@{escape_markdown_v2(uname)}" for uname in all_usernames_list])
    else:
        all_usernames_str = f"@{escape_markdown_v2(username_to_display)}" if username_to_display else "неизвестен"

    # === Ищем в trusted ===
    trust_info = find_user_in_table(str(user_id_to_search), 'trusted')
    if trust_info:
        t_user_id, t_username, t_original_username, t_note, _ = trust_info
        if not is_id and clean_query:
            display_username = clean_query  # тот, по которому искали
        else:
            display_username = t_username if t_username else username_to_display if username_to_display else "неизвестен" # юзернейм из базы или от юзербота

        # <<< ЛОГИРОВАНИЕ >>>
        logger.info(f"Inline trusted - clean_query: '{clean_query}', t_username: '{t_username}', username_to_display: '{username_to_display}', display_username: '{display_username}', first_name: '{first_name}', last_name: '{last_name}', date_created: '{date_created}'")

        # Формируем сообщение как в _handle_user_check, но с экранированием
        # Формируем строку отображения
        if display_username and t_user_id:
            display = f"@{escape_markdown_v2(display_username)} \\| ID: `{t_user_id}`"
        elif display_username:
            display = f"@{escape_markdown_v2(display_username)} \\| ID: неизвестен"
        elif t_user_id:
            display = f"ID: `{t_user_id}`"
        else:
            display = "неизвестен"
        msg = f"👤 Пользователь: {display}"

        info_lines = (
            f"\n🔮 Имя: {escape_markdown_v2(first_name)} {escape_markdown_v2(last_name)}\n"
            f"📓 Юзернеймы: {all_usernames_str}\n\n" # all_usernames_str уже содержит экранированные юзернеймы
            f"🪬 Дата создания: {escape_markdown_v2(date_created)}\n" # <<< Экранируем date_created
        )
        status_line = "💡`Статус`: *__ГАРАНТ__* ✅\n\n🟢*Данный пользователь является гарантом\\! Следующий вывод был основан на его репутации\\.*"
        note_line = f"\n\n🔓Дополнительная информация🔑:\n\n{escape_markdown_v2(t_note)}\n" if t_note else ""
        chat_line = ">Наш чат: @loneasBASE\n>Наш канал: @loneasproofs" # <<< Цитаты > теперь в caption
        msg += f"\n{info_lines}\n{status_line}{note_line}\n{chat_line}"

        # Title и description для отображения в инлайн-поиске
        title = f"✅ @{escape_markdown_v2(display_username)} (Гарант)" # <<< Экранируем юзернейм в title
        description = f"ID: {t_user_id}"

        # Используем InlineQueryResultArticle с форматированным сообщением
        result = InlineQueryResultArticle(
            id=str(hash(f"trusted_{user_id_to_search}") % 10**16), # Уникальный ID
            title=title,
            description=description,
            input_message_content=InputTextMessageContent(
                message_text=msg, # <<< Полное сообщение с форматированием
                parse_mode=ParseMode.MARKDOWN_V2, # <<< parse_mode включен
                disable_web_page_preview=True
            ),
            thumb_url="https://winchanii.ru/media/sb/guarantee8.jpg", # URL миниатюры (иконки)
            thumb_width=48, # Ширина миниатюры
            thumb_height=48, # Высота миниатюры
        )
        update.inline_query.answer(results=[result], cache_time=0)
        return

    # === Ищем в scammers ===
    scam_info = find_user_in_table(str(user_id_to_search), 'scammers')
    if scam_info:
        s_user_id, s_username, s_original_username, s_note, s_proof_url = scam_info
        # <<< Правильная логика для display_username >>>
        if not is_id and clean_query:
            display_username = clean_query  # тот, по которому искали
        else:
            display_username = s_username if s_username else username_to_display if username_to_display else "неизвестен" # юзернейм из базы или от юзербота

        # <<< ЛОГИРОВАНИЕ >>>
        logger.info(f"Inline scammer - clean_query: '{clean_query}', s_username: '{s_username}', username_to_display: '{username_to_display}', display_username: '{display_username}', first_name: '{first_name}', last_name: '{last_name}', date_created: '{date_created}'")

        # Формируем строку отображения
        if display_username and s_user_id:
            display = f"@{escape_markdown_v2(display_username)} \\| ID: `{s_user_id}`"
        elif display_username:
            display = f"@{escape_markdown_v2(display_username)} \\| ID: неизвестен"
        elif s_user_id:
            display = f"ID: `{s_user_id}`"
        else:
            display = "неизвестен"
        msg = f"👤 Пользователь: {display}"

        info_lines = (
            f"\n🔮 Имя: {escape_markdown_v2(first_name)} {escape_markdown_v2(last_name)}\n"
            f"📓 Юзернеймы: {all_usernames_str}\n" # all_usernames_str уже содержит экранированные юзернеймы
            f"🪬 Дата создания: {escape_markdown_v2(date_created)}\n" # <<< Экранируем date_created
            f"🔍 Искали в базе: {search_count} раз\\(а\\)"
        )
        status_line = "\n⚠️ НАЙДЕН В СКАМ\\-БАЗЕ\\!⚠️\n\n💡`Статус`: *__МОШЕННИК__*❌\n\n🔴*Пользователь — скамер\\. Найден в базе @LoneasBasebot\\. Ни в коем случае не контактируйте с данным человеком, не ведитесь на его уловки\\.*"
        note_line = f"\n\n🔓Дополнительная информация🔑:\n\n{escape_markdown_v2(s_note)}\n" if s_note else ""
        proof_line = f">Пруфы: [ссылка]({s_proof_url})" if s_proof_url else ""
        chat_line = ">Наш чат: @loneasBASE\n>Наш канал: @loneasproofs"
        if s_proof_url:
            footer = proof_line + "\n>" + chat_line
        else:
            footer = chat_line
        msg += f"\n{info_lines}\n{status_line}{note_line}\n{footer}"

        # Title и description для отображения в инлайн-поиске
        title = f"❌ @{escape_markdown_v2(display_username)} (Скамер)" # <<< Экранируем юзернейм в title
        description = f"ID: {s_user_id}"

        # Используем InlineQueryResultArticle с форматированным сообщением
        result = InlineQueryResultArticle(
            id=str(hash(f"scammer_{user_id_to_search}") % 10**16), # Уникальный ID
            title=title,
            description=description,
            input_message_content=InputTextMessageContent(
                message_text=msg, # <<< Полное сообщение с форматированием
                parse_mode=ParseMode.MARKDOWN_V2, # <<< parse_mode включен
                disable_web_page_preview=True
            ),
            thumb_url="https://winchanii.ru/media/sb/scammer8.jpg", # URL миниатюры (иконки)
            thumb_width=48, # Ширина миниатюры
            thumb_height=48, # Высота миниатюры
        )
        update.inline_query.answer(results=[result], cache_time=0)
        return

    # === Не найден ===
    if not is_id and clean_query:
        display_username = clean_query  # тот, по которому искали
    else:
        display_username = username_to_display if username_to_display else "неизвестен" # юзернейм, полученный по ID

    # <<< ЛОГИРОВАНИЕ >>>
    logger.info(f"Inline unknown - clean_query: '{clean_query}', username_to_display: '{username_to_display}', display_username: '{display_username}', first_name: '{first_name}', last_name: '{last_name}', date_created: '{date_created}'")

    # Формируем сообщение как в _handle_user_check, но с экранированием
    # Формируем строку отображения
    if display_username and user_id_to_search:
        display = f"@{escape_markdown_v2(display_username)} \\| ID: `{user_id_to_search}`"
    elif display_username:
        display = f"@{escape_markdown_v2(display_username)} \\| ID: неизвестен"
    elif user_id_to_search:
        display = f"ID: `{user_id_to_search}`"
    else:
        display = "неизвестен"
    msg = f"👤 Пользователь: {display}"

    info_lines = (
        f"\n🔮 Имя: {escape_markdown_v2(first_name)} {escape_markdown_v2(last_name)}\n"
        f"📓 Юзернеймы: {all_usernames_str}\n" # all_usernames_str уже содержит экранированные юзернеймы
        f"🪬 Дата создания: {escape_markdown_v2(date_created)}\n" # <<< Экранируем date_created
        f"🔍 Найден в базе: {search_count} раз\\(а\\)"
    )
    status_line = f"\n💡`Статус`: *__НЕ НАЙДЕН__* 🔍\n\n⚫️Пользователь @{escape_markdown_v2(display_username)} не был найден в нашей базе\\. Данная личность не проверена\\.\n\n🔓Дополнительная информация🔑:\n\nРекомендуется быть осторожным и использовать услуги проверенных гарантов \\- /listtrusted\\.\n"
    chat_line = ">Наш чат: @loneasBASE\n>Наш канал: @loneasproofs"
    msg += f"\n{info_lines}\n{status_line}\n{chat_line}"

    # Title и description для отображения в инлайн-поиске
    title = f"🔍 @{escape_markdown_v2(display_username)} (Не найден)" # <<< Экранируем юзернейм в title
    description = f"ID: {user_id_to_search}"

    # Используем InlineQueryResultArticle с форматированным сообщением
    result = InlineQueryResultArticle(
        id=str(hash(f"unknown_{user_id_to_search}") % 10**16), # Уникальный ID
        title=title,
        description=description,
        input_message_content=InputTextMessageContent(
            message_text=msg, # <<< Полное сообщение с форматированием
            parse_mode=ParseMode.MARKDOWN_V2, # <<< parse_mode включен
            disable_web_page_preview=True
        ),
        thumb_url="https://winchanii.ru/media/sb/unknown8.jpg", # URL миниатюры (иконки)
        thumb_width=48, # Ширина миниатюры
        thumb_height=48, # Высота миниатюры
    )
    update.inline_query.answer(results=[result], cache_time=0)


# === ОСТАЛЬНЫЕ ФУНКЦИИ ===
# Добавить в скам
def add_scammer_start(update: Update, context: CallbackContext):
    if update.effective_user.id not in ADMIN_IDS:
        update.message.reply_text("🚫 Только админы могут добавлять скамеров\\.", parse_mode=ParseMode.MARKDOWN_V2)
        return ConversationHandler.END
    update.message.reply_text("👤 Отправьте `@username` или `ID` скамера:")
    return WAITING_FOR_TARGET
# Перемещение из гарант в скам
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
# Пруфы скамера
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
# Добавить гаранта
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
# Удаление из базы
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
# /listscam
def list_scam(update: Update, context: CallbackContext):
    conn = sqlite3.connect('scam_base.db')
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, original_username FROM scammers") # <<< Используем original_username
    rows = cursor.fetchall()
    conn.close()
    if not rows:
        update.message.reply_text("Скам\\-база пуста\\.", parse_mode=ParseMode.MARKDOWN_V2)
        return
    lines = [f"@{uname}" if uname else f"ID: {uid}" for uid, uname in rows]
    escaped_lines = [escape_markdown_v2(line) for line in lines]
    text = "*🔴 Список скамеров:*\n" + "\n".join(escaped_lines)
    update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN_V2)
# /listtrusted
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
# /help
def help_command(update: Update, context: CallbackContext):
    msg = (
        "ℹ️ *Справка по командам*\n"
        "*Для всех:*\n"
        "• `/check @username` или `/check ID` — проверить статус\\.\n"
        "• `/listscam` — список скамеров\n"
        "• `/listtrusted` — список проверенных гарантов\n"
        "*Только для админов:*\n"
        "• `/addscam` — добавить скамера\n"
        "• `/addtrusted` — добавить проверенного\n"
        "• `/remove` — удалить из базы\n"
        "• `/help` — эта справка\n"
    )
    update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN_V2)
# сохранение в дб
def _save_to_db(update: Update, context: CallbackContext, table: str, publish=False):
    target = context.user_data['target']
    note = context.user_data.get('note', '')
    proof_url = context.user_data.get('proof_url')  # Только для скамеров
    user_id = None
    username = None
    original_username = None # <<< Новое поле
    # === Определяем user_id ===
    if target.isdigit():
        user_id = int(target)
        # Пробуем получить username из таблицы users
        conn = sqlite3.connect('scam_base.db')
        cursor = conn.cursor()
        cursor.execute("SELECT username FROM users WHERE user_id = ?", (user_id,))
        result = cursor.fetchone()
        conn.close()
        if result:
            username = result[0]  # Уже без @
    else:
        username = target.lstrip('@')
        original_username = username # <<< Сохраняем оригинальный юзернейм
        # Пробуем получить ID из таблицы users
        conn = sqlite3.connect('scam_base.db')
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM users WHERE LOWER(username) = ?", (username.lower(),))
        result = cursor.fetchone()
        conn.close()
        if result:
            user_id = result[0]
        else:
            # Пробуем получить ID через юзербота
            user_info = get_user_info_via_userbot(username)
            if user_info and 'error' not in user_info:
                user_id = user_info['id']
                username = user_info.get('username')
                # Сохраняем в user_profiles
                save_user_profile_from_userbot(user_id, user_info)

    # === Если не получили user_id — не добавляем ===
    if not user_id:
        update.message.reply_text("❌ Не удалось получить ID пользователя\\. Попробуйте позже \\(Возможно\\, это канал или чат переходник\\, пришлите юзернейм из этого канала или чата\\, возможно сработает\\)\\.", parse_mode=ParseMode.MARKDOWN_V2)
        return

    # === Удаляем из другой таблицы, если есть ===
    other_table = 'trusted' if table == 'scammers' else 'scammers'
    if find_user_in_table(str(user_id), other_table):
        move_user_between_tables(str(user_id), other_table, table)

    # === Сохраняем в базу (только по user_id) ===
    if table == 'scammers':
        add_user_to_table(user_id, username, original_username, note, table, proof_url)
    else:
        add_user_to_table(user_id, username, original_username, note, table)

    # === Формируем сообщение для админа ===
    display_parts = []
    if username:
        display_parts.append(f"@{username}")
    if user_id:
        display_parts.append(f"ID {user_id}")
    display = " \\| ".join(display_parts) if display_parts else target
    name = "скамер" if table == 'scammers' else "проверенный"
    update.message.reply_text(f"✅ {name.capitalize()} успешно добавлен: {display}")

    # === Публикуем в канал, если нужно ===
    if publish and username and user_id and note:
        publish_to_channel(context, user_id, username, note, proof_url, table == 'scammers')

def cancel(update: Update, context: CallbackContext):
    update.message.reply_text("❌ Операция отменена\\.", parse_mode=ParseMode.MARKDOWN_V2)
    return ConversationHandler.END

def error_handler(update: object, context: CallbackContext):
    logger.error("Exception while handling an update:", exc_info=context.error)

# === АВТОМАТИЧЕСКОЕ ДОБАВЛЕНИЕ ИЗ КАНАЛА ===
def monitor_channel_messages(update: Update, context: CallbackContext):
    # Определяем, является ли обновление сообщением или постом в канале
    message = update.message or update.channel_post

    # Проверяем, что сообщение существует и из нужного канала
    if not message or str(message.chat.id) != CHANNEL_ID:
        return

    # Проверяем, есть ли текст и медиа
    full_text = message.caption if message.caption else message.text
    has_media = message.photo or message.video or message.document or message.animation or message.sticker or message.voice or message.audio or message.video_note

    if not full_text or not has_media:
        return

    # Пытаемся найти юзернейм и суть
    match = re.search(r'❌Пользователь.*?@(\w+).*?❌\s*\nСуть:\s*(.+?)(?:\n|$)', full_text, re.DOTALL | re.IGNORECASE)

    if match:
        username = match.group(1)
        note = match.group(2).strip()

        # Получаем ссылку на сообщение в канале
        proof_url = f"https://t.me/c/{str(message.chat.id)[4:]}/{message.message_id}"

        # Получаем user_id через юзербота
        user_info = get_user_info_via_userbot(f"@{username}")
        if user_info and 'error' not in user_info:
            user_id = user_info['id']

            # Сохраняем профиль
            save_user_profile_from_userbot(user_id, user_info)

            # Добавляем в базу скамеров, используя username как original_username
            add_user_to_table(user_id, username, username, note, 'scammers', proof_url)
            # Больше никаких уведомлений в канал
# Бекапы
def backup_database():
    if not CONFIG_FILE.get('backup', {}).get('enabled', False):
        return

    backup_config = CONFIG_FILE['backup']
    db_path = 'scam_base.db'
    backup_dir = backup_config['path']
    keep_last_n = backup_config['keep_last_n']

    if not os.path.exists(db_path):
        logger.warning(f"База данных {db_path} не найдена для бэкапа.")
        return

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_filename = f"scam_base_backup_{timestamp}.db"
    backup_filepath = os.path.join(backup_dir, backup_filename)

    try:
        shutil.copy2(db_path, backup_filepath)
        logger.info(f"Резервная копия базы данных создана: {backup_filepath}")

        # Удаление старых бэкапов
        all_backups = glob.glob(os.path.join(backup_dir, "scam_base_backup_*.db"))
        all_backups.sort(key=os.path.getmtime) # Сортируем по времени создания

        if len(all_backups) > keep_last_n:
            backups_to_delete = all_backups[:-keep_last_n]
            for old_backup in backups_to_delete:
                try:
                    os.remove(old_backup)
                    logger.info(f"Старый бэкап удален: {old_backup}")
                except OSError as e:
                    logger.error(f"Ошибка удаления старого бэкапа {old_backup}: {e}")

    except Exception as e:
        logger.error(f"Ошибка создания резервной копии базы данных: {e}")

# === ФУНКЦИИ ДЛЯ СИНХРОНИЗАЦИИ С GITHUB ===
def sync_with_github():
    if not CONFIG_FILE.get('github_sync', {}).get('enabled', False):
        return

    github_config = CONFIG_FILE['github_sync']
    repo_url = github_config['repo_url']
    branch = github_config['branch']

    try:
        # Простая проверка: можно ли получить доступ к репозиторию
        # Для полноценной синхронизации потребуется более сложная логика (git pull)
        # или использование библиотеки gitpython. Здесь делаем базовую проверку.
        api_url = repo_url.replace(".git", "").replace("github.com", "api.github.com/repos")
        if not api_url.startswith("https://api.github.com/repos/"):
             api_url = f"https://api.github.com/repos/{repo_url.split('/')[-2]}/{repo_url.split('/')[-1].replace('.git', '')}"
        
        response = requests.get(f"{api_url}/branches/{branch}", timeout=10)
        if response.status_code == 200:
            commit_sha = response.json()['commit']['sha']
            logger.info(f"GitHub синхронизация: последний коммит в ветке '{branch}': {commit_sha[:8]}...")
            # Здесь должна быть логика `git pull origin {branch}`
            # Например, используя subprocess:
            # result = subprocess.run(['git', 'pull', 'origin', branch], cwd=os.getcwd(), capture_output=True, text=True)
            # if result.returncode == 0:
            #     logger.info("Код успешно синхронизирован с GitHub.")
            #     # Тут можно добавить логику перезапуска бота, если файлы изменились
            # else:
            #     logger.error(f"Ошибка синхронизации с GitHub: {result.stderr}")
        else:
            logger.error(f"Ошибка проверки GitHub: {response.status_code} - {response.text}")
    except requests.exceptions.RequestException as e:
        logger.error(f"Ошибка сети при синхронизации с GitHub: {e}")
    except Exception as e:
        logger.error(f"Неожиданная ошибка при синхронизации с GitHub: {e}")

# === НАСТРОЙКА ПЛАНИРОВЩИКА ЗАДАЧ (APSCHEDULER) ===
def setup_scheduler():
    """Настраивает планировщик задач для бэкапа и синхронизации."""
    scheduler = BackgroundScheduler()
    
    # Задача бэкапа
    backup_config = CONFIG_FILE.get('backup', {})
    if backup_config.get('enabled', False):
        interval_hours = backup_config.get('interval_hours', 24)
        scheduler.add_job(backup_database, 'interval', hours=interval_hours, id='backup_job')
        logger.info(f"Планировщик бэкапа настроен: каждые {interval_hours} часов.")

    # Задача синхронизации с GitHub
    github_config = CONFIG_FILE.get('github_sync', {})
    if github_config.get('enabled', False):
        interval_minutes = github_config.get('interval_minutes', 30)
        scheduler.add_job(sync_with_github, 'interval', minutes=interval_minutes, id='github_sync_job')
        logger.info(f"Планировщик синхронизации с GitHub настроен: каждые {interval_minutes} минут.")

    if scheduler.get_jobs():
        scheduler.start()
        logger.info("Планировщик задач запущен.")
    else:
        logger.info("Планировщик задач не настроен (все задачи отключены).")
# === старт ===
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
    dp.add_handler(CommandHandler("listscam", list_scam)) # <<< Теперь доступна всем
    dp.add_handler(CommandHandler("listtrusted", list_trusted))
    dp.add_handler(CommandHandler("check", handle_check_command))
    # Обработка @username только в личке
    dp.add_handler(MessageHandler(Filters.text & Filters.chat_type.private & ~Filters.command, handle_check_in_pm))
    # Автообновление ID на каждое сообщение
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, auto_update_user_id_on_message))
    # Inline обработчик
    dp.add_handler(InlineQueryHandler(inline_query))
    # Обработчик сообщений в канале для автоматического добавления
    dp.add_handler(MessageHandler(Filters.photo & (Filters.caption | Filters.text), monitor_channel_messages)) # <<< Новый обработчик
    dp.add_error_handler(error_handler)

    updater.start_polling()
    updater.idle()

if __name__ == '__main__':
    main()
