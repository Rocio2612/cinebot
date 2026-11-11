import logging                          # registro de eventos en la terminal
import os                               # acceso a las variables de entorno

from dotenv import load_dotenv          # lee el archivo .env
from google import genai                # SDK oficial de Gemini
from telegram import Update             # representa cada novedad recibida de Telegram
from telegram.constants import ChatAction   # acciones de chat, como "escribiendo..."
from telegram.ext import (
    Application,                        # motor del bot: recibe y distribuye updates
    CommandHandler,                     # handler para comandos (/start, /help)
    ContextTypes,                       # tipo del parámetro context de los handlers
    MessageHandler,                     # handler para mensajes que no son comandos
    filters,                            # filtros para seleccionar qué mensajes atender
)

import db                               # módulo propio: acceso a la base SQLite
from prompts import PROMPT_SISTEMA      # prompt de sistema definido en prompts.py

# Carga en el entorno las variables del archivo .env.
load_dotenv()

# Lectura de las variables. os.getenv devuelve None si la variable no existe.
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
# El segundo argumento es el valor por defecto si GEMINI_MODEL no está en el .env.
# Leer el modelo del .env permite cambiarlo sin modificar el código.
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")

# Cantidad de mensajes anteriores que se envían al modelo en cada consulta
# (20 mensajes = 10 preguntas y sus respuestas). Un valor mayor da más memoria,
# pero cada consulta resulta más lenta y consume más cuota.
MAX_MENSAJES_HISTORIAL = 20

# Formato de cada línea de registro: fecha y hora | nivel | origen | mensaje.
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,                 # se muestran INFO, WARNING, ERROR y CRITICAL
)
# httpx registra cada consulta HTTP; se limita a advertencias para no saturar la consola.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpx2").setLevel(logging.WARNING)    # httpx2 es el que usa la versión actual de google-genai
# Registrador propio del bot; sus mensajes aparecen con el nombre "cinebot".
logger = logging.getLogger("cinebot")

# Longitud máxima de un mensaje de Telegram, en caracteres.
LIMITE_TELEGRAM = 4096

# Se crea un único cliente para todo el programa. El cliente guarda la clave
# de API y administra las conexiones HTTP con los servidores de Google.
# Crearlo una sola vez evita abrir una conexión nueva en cada mensaje.
cliente_gemini = genai.Client(api_key=GEMINI_API_KEY)


def armar_entrada(historial: list[tuple[str, str]], mensaje_nuevo: str) -> list[dict]:
    """
    Convierte el historial guardado en la lista de pasos (steps) que espera
    la Interactions API y agrega al final el mensaje nuevo del usuario.
    """
    # Equivalencia entre el rol guardado en la base y el tipo de paso de la API.
    tipo_de_paso = {"user": "user_input", "model": "model_output"}

    # Lista por comprensión: un diccionario por cada fila del historial.
    # Cada paso indica su tipo y su contenido, que es una lista de bloques;
    # aquí, un único bloque de texto.
    pasos = [
        {"type": tipo_de_paso[rol], "content": [{"type": "text", "text": texto}]}
        for rol, texto in historial
    ]

    # El último paso es siempre el mensaje que el usuario acaba de enviar.
    pasos.append({"type": "user_input", "content": [{"type": "text", "text": mensaje_nuevo}]})
    return pasos


async def consultar_gemini(historial: list[tuple[str, str]], mensaje: str) -> str:
    """Envía a Gemini la conversación completa y devuelve el texto de la respuesta."""

    # .aio accede a la versión asíncrona del cliente; por eso la llamada
    # lleva await. Mientras se espera la respuesta, el bucle de eventos
    # puede atender los mensajes de otros usuarios.
    interaccion = await cliente_gemini.aio.interactions.create(
        model=GEMINI_MODEL,                       # nombre del modelo, leído del .env
        system_instruction=PROMPT_SISTEMA,        # personalidad y reglas de CineBot
        input=armar_entrada(historial, mensaje),  # conversación completa, no solo el último texto
        store=False,                              # la memoria la administra el bot, no Google
    )

    # output_text contiene el texto generado por el modelo. Si llega vacío
    # (None o ""), el operador "or" devuelve el mensaje alternativo para que
    # el bot nunca envíe una respuesta en blanco.
    return interaccion.output_text or "No se me ocurrió nada para responder 🤔"


def describir_error(error: Exception) -> str:
    """Traduce un error de la API a un mensaje comprensible para el usuario."""

    # Los errores HTTP de la API traen el código en el atributo status_code.
    # getattr devuelve None si el error no tiene ese atributo (por ejemplo,
    # una falla de red sin respuesta del servidor).
    codigo = getattr(error, "status_code", None)

    if codigo == 429:                   # 429: se superó la cuota de consultas
        return "Estoy recibiendo demasiadas consultas. Probá de nuevo en un minuto, por favor. ⏳"
    if codigo in (401, 403):            # 401/403: clave inválida o sin permiso
        return "Tengo un problema de configuración. Avisale a quien me administra. 🔧"
    # Cualquier otro caso: falla de red, error del servicio, modelo inexistente.
    return "Tuve un problema para pensar la respuesta. ¿Me lo preguntás de nuevo? 🙏"


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    nombre = update.effective_user.first_name
    await update.message.reply_text(f"¡Hola {nombre}! Soy CineBot. 🎬")


async def ayuda(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Responde al comando /help."""
    await update.message.reply_text(
        "Estos son mis comandos:\n\n"
        "/start - Presentación de CineBot\n"
        "/help - Esta ayuda\n"
        "/olvidar - Borra nuestra conversación y empezamos de cero\n\n"
        "Y, sobre todo, podés escribirme qué tenés ganas de ver: te recomiendo "
        "películas o series, te cuento de qué trata una o te comento tu reseña. "
        "Me acuerdo de lo que hablamos."
    )


async def olvidar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Responde al comando /olvidar: borra el historial del usuario."""
    borrados = await db.borrar_historial(update.effective_user.id)
    logger.info("Usuario %s borró %d mensajes", update.effective_user.id, borrados)
    await update.message.reply_text("Listo, empezamos de cero. ¿Qué tenés ganas de ver? 🍿")


async def responder(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Responde con Gemini, teniendo en cuenta la conversación previa."""
    user_id = update.effective_user.id                  # identifica de quién es la conversación
    texto = update.message.text                         # texto enviado por el usuario
    logger.info("Mensaje de %s: %s", user_id, texto)

    # Muestra "escribiendo..." en el chat. La indicación dura unos segundos
    # o hasta que el bot envía su respuesta.
    await update.message.chat.send_action(ChatAction.TYPING)

    # Paso 1: recuperar lo que ya se habló con este usuario.
    historial = await db.obtener_historial(user_id, MAX_MENSAJES_HISTORIAL)

    # Paso 2: consultar al modelo con el historial más el mensaje nuevo.
    # La consulta a una API externa puede fallar por causas ajenas al código;
    # try/except evita que el error llegue al usuario como silencio.
    try:
        respuesta = await consultar_gemini(historial, texto)
    except Exception as error:
        logger.error("Error al consultar a Gemini: %s", error)   # detalle técnico, para el desarrollador
        await update.message.reply_text(describir_error(error))  # mensaje amable, para el usuario
        # Si la consulta falló, no se guarda nada: así el historial nunca
        # queda con una pregunta sin su respuesta.
        return

    # Paso 3: guardar la pregunta y la respuesta para la próxima consulta.
    await db.guardar_intercambio(user_id, texto, respuesta)

    # Si la respuesta supera el límite de Telegram, se envía en partes.
    # range(0, len, LIMITE) produce 0, 4096, 8192, ...: el inicio de cada parte.
    for inicio in range(0, len(respuesta), LIMITE_TELEGRAM):
        await update.message.reply_text(respuesta[inicio:inicio + LIMITE_TELEGRAM])


async def manejar_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Registra en la terminal los errores no previstos que ocurren en los handlers."""
    logger.error("Error no previsto", exc_info=context.error)


async def al_iniciar(app: Application) -> None:
    """Se ejecuta una sola vez, antes de comenzar a recibir mensajes."""
    # Es una corrutina porque inicializar la base requiere await;
    # por eso no puede llamarse directamente desde main(), que es síncrona.
    await db.inicializar()
    logger.info("Base de datos lista (%s).", db.RUTA_DB)


def main() -> None:
    # Lista con los nombres de las variables obligatorias que faltan en el .env.
    faltantes = [nombre for nombre, valor in [("TELEGRAM_TOKEN", TELEGRAM_TOKEN),
                                              ("GEMINI_API_KEY", GEMINI_API_KEY)]
                 if not valor]
    if faltantes:
        # SystemExit termina el programa mostrando el mensaje indicado.
        raise SystemExit(f"Faltan variables en el .env: {', '.join(faltantes)}")

    # Se construye la Application con el token del bot. post_init registra una
    # corrutina que la Application ejecuta al arrancar, dentro del bucle de
    # eventos, antes de procesar el primer update.
    app = Application.builder().token(TELEGRAM_TOKEN).post_init(al_iniciar).build()

    # Registro de handlers. El orden importa: se usa el primero que coincide.
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", ayuda))
    app.add_handler(CommandHandler("olvidar", olvidar))
    # Texto que no sea comando: lo atiende responder.
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, responder))
    app.add_error_handler(manejar_error)

    logger.info("CineBot v3 en línea con el modelo %s.", GEMINI_MODEL)
    # Polling: el bot consulta a Telegram, de forma continua, si hay mensajes nuevos.
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()