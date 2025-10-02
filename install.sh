#!/bin/bash

# install.sh

set -e  # Выход при ошибке

echo "=== Установка скам-базы Telegram-бота ==="

# 1. Проверка Python
if ! command -v python3 &> /dev/null; then
    echo "❌ Python3 не найден. Установка..."
    sudo apt update
    sudo apt install -y python3 python3-pip
else
    echo "✅ Python3 уже установлен"
fi

# 2. Установка зависимостей
echo "📦 Установка зависимостей..."
pip3 install python-telegram-bot

# 3. Запрос данных у пользователя
echo
echo "=== Введите данные для настройки бота ==="
read -p "Введите токен бота (от BotFather): " BOT_TOKEN
read -p "Введите ID администраторов через запятую (например, 123456789,987654321): " ADMIN_IDS_RAW
read -p "Введите канал для скамеров (например, @channel1): " CHANNEL_SCAM
read -p "Введите канал для гарантов (например, @channel2): " CHANNEL_TRUSTED
read -p "Введите ID канала для добавления (например, -1001234567890): " CHANNEL_ID

# 4. Форматирование ADMIN_IDS
ADMIN_IDS="[${ADMIN_IDS_RAW// /}]"

# 5. Создание config.py
echo "📝 Создание файла config.py..."
cat > config.py <<EOF
# config.py

# === НАСТРОЙКИ ===
BOT_TOKEN = '$BOT_TOKEN'
ADMIN_IDS = set($ADMIN_IDS)
CHANNEL_SCAM = "$CHANNEL_SCAM"
CHANNEL_TRUSTED = "$CHANNEL_TRUSTED"
CHANNEL_ID = "$CHANNEL_ID"
EOF

echo "✅ Файл config.py создан."

# 6. Запуск бота (по желанию)
echo
read -p "Запустить бота? (y/n): " start_bot
if [[ "$start_bot" =~ ^[Yy]$ ]]; then
    echo "🚀 Запуск бота..."
    python3 main.py
else
    echo "✅ Установка завершена. Запустите бота командой: python3 main.py"
fi