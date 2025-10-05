
from telethon import TelegramClient
# Импортируем типы для проверки
from telethon.tl.types import User, Channel, Chat
# ИСПРАВЛЕНО: Удалён несуществующий импорт LastNameInvalidError
from telethon.errors import (
    UsernameInvalidError, PeerIdInvalidError, FloodWaitError,
    PhoneNumberInvalidError, PhoneCodeInvalidError, SessionPasswordNeededError,
    AuthKeyUnregisteredError, UserDeactivatedError, AuthKeyDuplicatedError,
    FirstNameInvalidError
    # LastNameInvalidError - УДАЛЕН, так как не существует в этой версии Telethon
)
import logging
import json
import time
import os
import asyncio
import glob
import traceback
from datetime import datetime
import glob
CONFIG_FILE = 'config.json'

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
    global API_ID, API_HASH, PHONE
    API_ID = config['userbot']['api_id']
    API_HASH = config['userbot']['api_hash']
    PHONE = config['userbot']['phone']

# Загружаем настройки при импорте модуля
load_settings()
# Префиксы для файлов запросов и ответов (должны совпадать с main.py)
UB_REQUEST_PREFIX = "ubreq_"
UB_RESPONSE_PREFIX = "ubresp_"
# Папка для файлов (по умолчанию текущая директория)
COMMUNICATION_DIR = "."
CHECK_INTERVAL = 0.1 # Минимальная задержка для снижения нагрузки на CPU
MAX_CONCURRENT_TASKS = 500 # Максимальное количество одновременных задач обработки

client = TelegramClient(os.path.join(COMMUNICATION_DIR, 'userbot_session'), API_ID, API_HASH)

# Настройка логирования с одновременной записью в файл и вывод в консоль
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.FileHandler(os.path.join(COMMUNICATION_DIR, 'userbot.log')),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Семафор для ограничения количества одновременных задач
semaphore = asyncio.Semaphore(MAX_CONCURRENT_TASKS)

async def get_user_info(username_or_id):
    """Получает информацию о пользователе."""
    try:
        # Проверка на пустой или слишком короткий запрос
        if not username_or_id or (isinstance(username_or_id, str) and len(username_or_id.lstrip('@')) < 4):
             logger.warning(f"Запрос слишком короткий или пустой: '{username_or_id}'. Пропускаем.")
             return {'error': 'Запрос слишком короткий или пустой.'}

        logger.info(f"Попытка получить информацию для: {username_or_id}")
        entity = await client.get_entity(username_or_id)
        
        # Проверяем, является ли сущность пользователем
        if not isinstance(entity, User):
            logger.warning(f"Найденная сущность '{username_or_id}' не является пользователем (тип: {type(entity).__name__}). Пропускаем.")
            return {'error': f'Сущность "{username_or_id}" не является пользователем.'}

        # Получаем дату создания аккаунта (для пользователей это обычно дата регистрации)
        # У объекта User нет прямого атрибута date. Возможно, имелось в виду другое?
        # Например, можно попробовать получить дату из ограничений (restricted_until) или других полей,
        # но чаще всего дата "создания" аккаунта в контексте Telegram - это дата его появления в вашей системе.
        # Для демонстрации оставим "неизвестно". Если нужно другое, уточните.
        account_date_str = "неизвестно" 

        # Получаем все юзернеймы
        all_usernames = []
        # У объекта User есть атрибут usernames (список Username), но не всегда.
        # Устаревший способ - атрибут username (строка).
        if hasattr(entity, 'usernames') and entity.usernames is not None:
            # usernames - это список объектов Username, у которых есть active и username
            all_usernames = [u.username for u in entity.usernames if getattr(u, 'active', True)] # Предполагаем, что активные по умолчанию
        elif hasattr(entity, 'username') and entity.username:
            # Если новый способ не сработал, пробуем старый
            all_usernames = [entity.username]
            
        # Формируем словарь с информацией
        user_info = {
            'id': entity.id,
            'username': entity.username, # Основной username
            'first_name': entity.first_name,
            'last_name': entity.last_name,
            'is_bot': entity.bot, # У User есть атрибут bot
            'account_creation': account_date_str, # <<< НОВОЕ: дата создания (в данном случае "неизвестно")
            'all_usernames': all_usernames # <<< НОВОЕ: список всех юзернеймов
        }
        
        logger.info(f"Информация для {username_or_id} успешно получена.")
        return user_info

    except UsernameInvalidError:
        logger.error(f"Недопустимое имя пользователя или ID: {username_or_id}")
        return {'error': f'Недопустимое имя пользователя или ID: {username_or_id}'}
    except PeerIdInvalidError:
        logger.error(f"Недопустимый ID пользователя: {username_or_id}")
        return {'error': f'Недопустимый ID пользователя: {username_or_id}'}
    except FloodWaitError as e:
        logger.error(f"Flood wait for {e.seconds} seconds.")
        # Можно сразу повторить запрос, но для простоты просто возвращаем ошибку
        # return await get_user_info(username_or_id) # <<< Вариант с повтором
        return {'error': f'Flood wait: {e.seconds} секунд.'}
    except Exception as e:
        logger.error(f"Неожиданная ошибка при получении информации для {username_or_id}: {e}", exc_info=True)
        return {'error': f'Произошла ошибка: {e}'}


async def process_single_request_file(filepath):
    """Обрабатывает один файл запроса."""
    async with semaphore: # Ограничиваем количество одновременных задач
        logger.info(f"[UB] Обработка файла запроса: {filepath}")
        try:
            # Извлекаем UUID из имени файла для логов
            filename = os.path.basename(filepath)
            if filename.startswith(UB_REQUEST_PREFIX) and filename.endswith('.txt'):
                request_uuid = filename[len(UB_REQUEST_PREFIX):-4] # ubreq_<uuid>.txt -> <uuid>
            else:
                request_uuid = "unknown"

            # Читаем файл запроса
            with open(filepath, 'r', encoding='utf-8') as f:
                lines = f.read().strip().splitlines()
            
            if len(lines) < 2:
                logger.error(f"[UB] Файл запроса {filepath} имеет неверный формат (меньше 2 строк).")
                try:
                    os.remove(filepath) # Удаляем битый файл
                    logger.debug(f"[UB] Битый файл запроса удалён: {filepath}")
                except OSError:
                    pass
                return # Завершаем задачу

            query = lines[0].strip()
            expected_response_filename = lines[1].strip() # Относительное имя файла ответа

            # Проверяем, что expected_response_filename находится в рабочей директории
            # и имеет правильный префикс, чтобы избежать записи куда-то не туда
            if not expected_response_filename.startswith(UB_RESPONSE_PREFIX):
                 logger.error(f"[UB] Недопустимое имя файла ответа в запросе {filepath}: {expected_response_filename}")
                 try:
                     os.remove(filepath)
                     logger.debug(f"[UB] Файл запроса с недопустимым ответом удалён: {filepath}")
                 except OSError:
                     pass
                 return # Завершаем задачу

            full_expected_response_path = os.path.join(COMMUNICATION_DIR, expected_response_filename)
            # Проверяем, что путь не вышел за рамки COMMUNICATION_DIR (предотвращение path traversal)
            if not os.path.abspath(full_expected_response_path).startswith(os.path.abspath(COMMUNICATION_DIR)):
                 logger.error(f"[UB] Попытка записи файла ответа вне рабочей директории: {full_expected_response_path}")
                 try:
                     os.remove(filepath)
                     logger.debug(f"[UB] Файл запроса с опасным путем ответа удалён: {filepath}")
                 except OSError:
                     pass
                 return # Завершаем задачу

            logger.info(f"[UB] Запрос UUID {request_uuid}: '{query}', Ответ ожидается в: {full_expected_response_path}")

            # Удаляем файл запроса сразу после чтения и валидации
            try:
                os.remove(filepath)
                logger.debug(f"[UB] Файл запроса обработан и удалён: {filepath}")
            except OSError as oe:
                logger.warning(f"[UB] Не удалось удалить файл запроса {filepath} после чтения: {oe}")
                # Если не смогли удалить, возможно, другая задача уже обрабатывает или удалила.
                # Продолжаем, но логгируем.
            
            if not query:
                logger.info(f"[UB] Запрос UUID {request_uuid} был пустым. Пропускаем.")
                response_data = json.dumps({"error": "Пустой запрос"}, ensure_ascii=False)
                with open(full_expected_response_path, 'w', encoding='utf-8') as f:
                    f.write(response_data)
                logger.debug(f"[UB] Ответ 'пустой запрос' записан для UUID {request_uuid}")
                return
                    
            # Получаем информацию (это ваша существующая функция)
            info = await get_user_info(query) # <<< Убедитесь, что эта функция существует
            
            # Записываем ответ в указанный файл
            response_data = json.dumps(info, ensure_ascii=False, indent=2)
            # Используем 'x' режим, чтобы упасть, если файл уже существует (что маловероятно, но на всякий случай)
            with open(full_expected_response_path, 'x', encoding='utf-8') as f:
                f.write(response_data)
            
            logger.info(f"[UB] Ответ для запроса UUID {request_uuid} ('{query}') записан в {full_expected_response_path}")

        except FileNotFoundError:
            # Файл мог быть удален другой задачей или процессом
            logger.debug(f"[UB] Файл запроса {filepath} не найден (возможно, уже обработан или удалён другой задачей).")
            pass # Игнорируем
        except PermissionError as pe:
            logger.error(f"[UB] Ошибка доступа при обработке файла {filepath}: {pe}")
            # Не удаляем файл, возможно, он заблокирован
        except Exception as e:
            logger.error(f"[UB] Критическая ошибка при обработке файла {filepath}: {e}", exc_info=True)
            # Пытаемся удалить файл запроса, чтобы не зациклиться
            try:
                os.remove(filepath)
                logger.debug(f"[UB] Файл запроса удалён из-за критической ошибки: {filepath}")
            except (OSError, UnboundLocalError):
                pass
            # Записываем файл ошибки, если ожидаемый файл ответа корректен
            try:
                if 'full_expected_response_path' in locals():
                    response_data = json.dumps({"error": f"Критическая ошибка обработки в юзерботе: {type(e).__name__}: {e}"}, ensure_ascii=False, indent=2)
                    with open(full_expected_response_path, 'x', encoding='utf-8') as f:
                        f.write(response_data)
                    logger.debug(f"[UB] Файл ошибки записан: {full_expected_response_path}")
            except FileExistsError:
                 logger.warning(f"[UB] Файл ошибки {full_expected_response_path} уже существует, не перезаписываем.")
            except Exception as write_error:
                 logger.error(f"[UB] Не удалось записать файл ошибки {full_expected_response_path}: {write_error}")



async def main_loop():
    """Главная функция цикла юзербота."""
    await client.start(phone=PHONE) # <<< Убедитесь, что client, PHONE определены
    logger.info("[UB] Юзербот запущен и авторизован.")
    logger.info(f"[UB] Рабочая директория: {os.path.abspath(COMMUNICATION_DIR)}")

    while True:
        try:
            # Ищем все файлы запросов с нужным префиксом
            request_pattern = os.path.join(COMMUNICATION_DIR, f"{UB_REQUEST_PREFIX}*.txt")
            request_files = glob.glob(request_pattern)
            
            if request_files:
                logger.info(f"[UB] Найдено {len(request_files)} файлов запросов для обработки.")
                tasks = []
                for filepath in request_files:
                    # Создаем задачу для каждой пары файлов
                    task = asyncio.create_task(process_single_request_file(filepath))
                    tasks.append(task)
                
                if tasks:
                    # Ждем завершения всех задач обработки
                    # return_exceptions=True позволяет продолжить работу, даже если одна задача упала
                    results = await asyncio.gather(*tasks, return_exceptions=True)
                    # Можно прологгировать результаты, если нужно
                    # for i, res in enumerate(results):
                    #     if isinstance(res, Exception):
                    #         logger.error(f"[UB] Задача {i} завершилась с ошибкой: {res}")
            
            # else:
            #     logger.debug(f"[UB] Файлы с паттерном {request_pattern} не найдены. Ждем {CHECK_INTERVAL} секунд...")
            
        except Exception as e:
            logger.error(f"[UB] Критическая ошибка в главном цикле: {e}", exc_info=True)
            # Продолжаем цикл даже при критической ошибке
        
        # Ждем перед следующей проверкой
        await asyncio.sleep(CHECK_INTERVAL)

# Если у вас основная точка входа в функции main(), замените её или вызывайте main_loop из неё
async def main():
    """Точка входа."""
    try:
        await main_loop()
    except KeyboardInterrupt:
        logger.info("[UB] Получен сигнал завершения (Ctrl+C). Закрываем клиент...")
    except Exception as e:
        logger.critical(f"[UB] Необработанное исключение в main: {e}", exc_info=True)
    finally:
        await client.disconnect() # <<< Убедитесь, что client определен
        logger.info("[UB] Клиент Telegram отключен.")

if __name__ == '__main__':
    client.loop.run_until_complete(main()) # <<< Убедитесь, что client определен
