import os
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

from database import inicializar_db, guardar_mensaje
from ai_service import generar_respuesta_bot

load_dotenv()
TOKEN = os.getenv("TELEGRAM_TOKEN")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    nombre = update.effective_user.first_name
    await update.message.reply_text(f"¡Hola {nombre}! Soy CineBot. 🎬")

async def procesar_mensaje(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    texto_usuario = update.message.text

    # 1. Generar respuesta con Gemini (lee el historial guardado hasta ahora)
    respuesta_ia = generar_respuesta_bot(chat_id, texto_usuario)

    if respuesta_ia is None:
        await update.message.reply_text("Uy, se me cortó la película 🎬 Probá de nuevo en un ratito.")
        return

    # 2. Recién ahora guardamos el mensaje del usuario y la respuesta en la DB
    guardar_mensaje(chat_id, "user", texto_usuario)
    guardar_mensaje(chat_id, "model", respuesta_ia)

    # 3. Enviar mensaje por Telegram
    await update.message.reply_text(respuesta_ia)

def main():
    inicializar_db()
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, procesar_mensaje))
    print("CineBot está corriendo. ¡Escribile en Telegram!")
    app.run_polling()

if __name__ == "__main__":
    main()