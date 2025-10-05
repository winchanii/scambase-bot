# setup.py
import os
import sys
import json
import subprocess
import platform
import shutil
from datetime import datetime

CONFIG_FILE = 'config.json'
MAIN_PY_TEMPLATE = 'main.py'
USERBOT_PY_TEMPLATE = 'userbot.py'

DEFAULT_CONFIG = {
    "bot_token": "",
    "admin_ids": [],
    "channel_scam": "@channel1",
    "channel_trusted": "@channel2",
    "channel_id": "-1003153261811",
    "backup": {
        "enabled": True,
        "interval_hours": 24,
        "keep_last_n": 7,
        "path": "./backups"
    },
    "github_sync": {
        "enabled": False,
        "repo_url": "https://github.com/yourusername/your-repo.git", # <<< Замените на ваш репозиторий
        "branch": "main",
        "interval_minutes": 30
    },
    "userbot": {
        "api_id": 24818772,
        "api_hash": "YOUR_API_HASH_HERE",
        "phone": "+19432259632"
    }
}

def load_config():
    """Загружает конфигурацию из файла config.json."""
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r') as f:
                return json.load(f)
        except json.JSONDecodeError:
            print(f"Ошибка: Файл {CONFIG_FILE} поврежден. Будет создан новый.")
    return DEFAULT_CONFIG.copy()

def save_config(config):
    """Сохраняет конфигурацию в файл config.json."""
    with open(CONFIG_FILE, 'w') as f:
        json.dump(config, f, indent=4, ensure_ascii=False)
    print(f"Конфигурация сохранена в {CONFIG_FILE}")

def get_input(prompt, default_value="", is_secret=False):
    """Получает ввод от пользователя с возможностью скрытия (для секретов)."""
    if is_secret:
        try:
            import getpass
            value = getpass.getpass(prompt + (f" [{default_value}]" if default_value else "") + ": ")
        except ImportError:
            value = input(prompt + (f" [{default_value}]" if default_value else "") + " (ввод будет скрыт): ")
    else:
        value = input(prompt + (f" [{default_value}]" if default_value else "") + ": ")
    return value if value else default_value

def get_admin_ids():
    """Получает список admin IDs от пользователя."""
    ids_str = input("Введите Telegram ID администраторов, разделенные запятыми (например, 123456789, 987654321): ")
    ids = []
    if ids_str:
        try:
            ids = [int(id.strip()) for id in ids_str.split(',') if id.strip().isdigit()]
        except ValueError:
            print("Ошибка в формате ID. Пожалуйста, введите числа, разделенные запятыми.")
            return get_admin_ids()
    return ids

def check_dependencies():
    """Проверяет, установлены ли необходимые библиотеки."""
    required_packages = ['python-telegram-bot', 'telethon', 'apscheduler'] # <<< Добавлен apscheduler
    missing_packages = []
    for package in required_packages:
        try:
            if package == 'python-telegram-bot':
                __import__('telegram')
            elif package == 'telethon':
                __import__('telethon')
            elif package == 'apscheduler':
                __import__('apscheduler')
        except ImportError:
            missing_packages.append(package)
    
    if missing_packages:
        print("\nОбнаружены отсутствующие зависимости:")
        for pkg in missing_packages:
            print(f"- {pkg}")
        install_now = input("\nУстановить их сейчас с помощью pip? (y/N): ").lower().strip()
        if install_now in ['y', 'yes', 'д', 'да']:
            try:
                subprocess.check_call([sys.executable, '-m', 'pip', 'install'] + missing_packages)
                print("Зависимости успешно установлены!")
            except subprocess.CalledProcessError as e:
                print(f"Ошибка установки зависимостей: {e}")
                print("Пожалуйста, установите их вручную: pip install python-telegram-bot telethon apscheduler")
        else:
            print("Установка зависимостей пропущена. Убедитесь, что они установлены перед запуском бота.")
    else:
        print("Все необходимые зависимости уже установлены.")

def configure_backup(config):
    """Настраивает резервное копирование."""
    print("\n--- Настройка резервного копирования ---")
    config['backup']['enabled'] = input("Включить автоматическое резервное копирование? (y/N): ").lower().strip() in ['y', 'yes', 'д', 'да']
    if config['backup']['enabled']:
        config['backup']['interval_hours'] = int(get_input("Интервал резервного копирования (часы)", str(config['backup']['interval_hours'])))
        config['backup']['keep_last_n'] = int(get_input("Количество последних бэкапов для хранения", str(config['backup']['keep_last_n'])))
        config['backup']['path'] = get_input("Путь для хранения бэкапов", config['backup']['path'])
        # Создаем папку для бэкапов, если её нет
        os.makedirs(config['backup']['path'], exist_ok=True)
        print(f"Папка для бэкапов создана: {config['backup']['path']}")

def configure_github_sync(config):
    """Настраивает синхронизацию с GitHub."""
    print("\n--- Настройка синхронизации с GitHub ---")
    config['github_sync']['enabled'] = input("Включить автоматическую синхронизацию с GitHub? (y/N): ").lower().strip() in ['y', 'yes', 'д', 'да']
    if config['github_sync']['enabled']:
        config['github_sync']['repo_url'] = get_input("URL вашего GitHub-репозитория (HTTPS)", config['github_sync']['repo_url'])
        config['github_sync']['branch'] = get_input("Ветка для синхронизации", config['github_sync']['branch'])
        config['github_sync']['interval_minutes'] = int(get_input("Интервал синхронизации (минуты)", str(config['github_sync']['interval_minutes'])))

def create_service_files(config):
    """Создает файлы для запуска как службы (systemd unit для Linux, .bat для Windows)."""
    system = platform.system().lower()
    
    if system == "linux":
        service_content_main = f"""[Unit]
Description=Scam Base Bot Main Service
After=network.target

[Service]
Type=simple
User={os.getenv('USER') or 'root'}
WorkingDirectory={os.getcwd()}
ExecStart={sys.executable} {MAIN_PY_TEMPLATE}
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
"""
        service_content_userbot = f"""[Unit]
Description=Scam Base Bot Userbot Service
After=network.target

[Service]
Type=simple
User={os.getenv('USER') or 'root'}
WorkingDirectory={os.getcwd()}
ExecStart={sys.executable} {USERBOT_PY_TEMPLATE}
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
"""
        try:
            with open("scambase_main.service", "w") as f:
                f.write(service_content_main)
            with open("scambase_userbot.service", "w") as f:
                f.write(service_content_userbot)
            print("\nФайлы служб systemd созданы:")
            print("- scambase_main.service")
            print("- scambase_userbot.service")
            print("Чтобы установить и запустить как службы, выполните:")
            print("sudo cp scambase_main.service /etc/systemd/system/")
            print("sudo cp scambase_userbot.service /etc/systemd/system/")
            print("sudo systemctl daemon-reload")
            print("sudo systemctl enable scambase_main.service")
            print("sudo systemctl enable scambase_userbot.service")
            print("sudo systemctl start scambase_main.service")
            print("sudo systemctl start scambase_userbot.service")
        except Exception as e:
            print(f"Ошибка создания файлов служб: {e}")

    elif system == "windows":
        bat_content_main = f"""@echo off
cd /d "{os.getcwd()}"
"{sys.executable}" {MAIN_PY_TEMPLATE}
pause
"""
        bat_content_userbot = f"""@echo off
cd /d "{os.getcwd()}"
"{sys.executable}" {USERBOT_PY_TEMPLATE}
pause
"""
        try:
            with open("run_main.bat", "w") as f:
                f.write(bat_content_main)
            with open("run_userbot.bat", "w") as f:
                f.write(bat_content_userbot)
            print("\nBAT-файлы для запуска созданы:")
            print("- run_main.bat")
            print("- run_userbot.bat")
            print("Вы можете запустить их двойным кликом или использовать для планировщика заданий Windows.")
        except Exception as e:
            print(f"Ошибка создания BAT-файлов: {e}")

def main():
    """Главная функция установщика."""
    print("=== Установщик конфигурации ScamBase Bot ===")
    
    # 1. Загрузить существующую конфигурацию или использовать умолчания
    config = load_config()
    print(f"Текущая конфигурация загружена из {CONFIG_FILE} (если файл существует).")

    # 2. Получить настройки от пользователя
    print("\n--- Настройка основного бота ---")
    config['bot_token'] = get_input("Введите токен вашего Telegram-бота", config['bot_token'], is_secret=True)
    
    print("\nВведите Telegram ID администраторов.")
    new_admin_ids = get_admin_ids()
    if new_admin_ids:
        config['admin_ids'] = new_admin_ids

    config['channel_scam'] = get_input("Введите юзернейм или ID канала для скамеров", config['channel_scam'])
    config['channel_trusted'] = get_input("Введите юзернейм или ID канала для гарантов", config['channel_trusted'])
    config['channel_id'] = get_input("Введите ID канала для автоматического добавления (например, -1001234567890)", config['channel_id'])

    # 3. Настройка бэкапа
    configure_backup(config)

    # 4. Настройка синхронизации с GitHub
    configure_github_sync(config)

    # 5. Настройка юзербота
    print("\n--- Настройка юзербота ---")
    config['userbot']['api_id'] = int(get_input("Введите ваш API ID (с my.telegram.org)", str(config['userbot']['api_id'])))
    config['userbot']['api_hash'] = get_input("Введите ваш API Hash (с my.telegram.org)", config['userbot']['api_hash'], is_secret=True)
    config['userbot']['phone'] = get_input("Введите номер телефона для юзербота (+1234567890)", config['userbot']['phone'])

    # 6. Сохранить конфигурацию
    save_config(config)
    
    # 7. Проверить зависимости
    check_dependencies()
    
    # 8. Создать файлы для запуска как службы/скрипты
    create_service_files(config)
    
    print("\n=== Установка завершена! ===")
    print("Конфигурация сохранена.")
    print("Теперь вы можете запустить бота:")
    print(f"- Основной бот: {sys.executable} {MAIN_PY_TEMPLATE}")
    print(f"- Юзербот:      {sys.executable} {USERBOT_PY_TEMPLATE}")
    print("(Убедитесь, что зависимости установлены и config.json настроен правильно)")

if __name__ == '__main__':
    main()