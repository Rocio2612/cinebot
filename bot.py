import json                             # convierte los resultados de las funciones a texto JSON
import logging
import os
from dotenv import load_dotenv
from google import genai
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import db
from api import DECLARACIONES, ejecutar_funcion   # herramientas que el modelo puede pedir
from prompts import PROMPT_SISTEMA

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")

MAX_MENSAJES_HISTORIAL = 20

# Cantidad máxima de vueltas del ciclo de function calling en un mismo mensaje.
# Evita un bucle infinito si el modelo pidiera funciones una y otra vez.
MAX_VUELTAS_HERRAMIENTAS = 5

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpx2").setLevel(logging.WARNING)
logger = logging.getLogger("cinebot")

LIMITE_TELEGRAM = 4096

cliente_gemini = genai.Client(api_key=GEMINI_API_KEY)

def armar_entrada(historial: list[tuple[str, str]], mensaje_nuevo: str) -> list[dict]:
    tipo_de_paso = {"user": "user_input", "model": "model_output"}
    pasos = [
        {"type": tipo_de_paso[rol], "content": [{"type": "text", "text": texto}]}
        for rol, texto in historial
    ]
    pasos.append({"type": "user_input", "content": [{"type": "text", "text": mensaje_nuevo}]})
    return pasos


async def consultar_gemini(historial: list, mensaje_nuevo: str) -> str:
    """
    Envía la conversación a Gemini y resuelve el ciclo de function calling:
    mientras el modelo pida funciones, se ejecutan y se le devuelven los
    resultados; cuando responde con texto, se devuelve ese texto.
    """
    # Lista de pasos que se envía al modelo. Empieza con el historial y el
    # mensaje nuevo, y crece dentro de este turno con los pedidos de funciones
    # y sus resultados. Esos pasos intermedios no se guardan en la base.
    entrada = armar_entrada(historial, mensaje_nuevo)

    for vuelta in range(MAX_VUELTAS_HERRAMIENTAS):
        interaccion = await cliente_gemini.aio.interactions.create(
            model=GEMINI_MODEL,
            system_instruction=PROMPT_SISTEMA,
            input=entrada,
            tools=DECLARACIONES,        # funciones que el modelo puede pedir
            store=False,
        )

        # Pasos de tipo function_call: el modelo pide ejecutar una función.
        # Puede pedir más de una en la misma respuesta.
        llamadas = [paso for paso in interaccion.steps if paso.type == "function_call"]

        if not llamadas:
            # Sin pedidos de funciones: el modelo ya redactó la respuesta final.
            return interaccion.output_text or "No se me ocurrió nada para responder 🤔"

        # Se agregan a la entrada TODOS los pasos que devolvió el modelo, sin
        # modificarlos (incluidos los de razonamiento interno). La API los
        # necesita para relacionar cada resultado con su pedido.
        for paso in interaccion.steps:
            entrada.append(paso.model_dump())

        # Se ejecuta cada función pedida y se agrega su resultado como un paso
        # function_result. call_id debe coincidir con el id del pedido.
        for llamada in llamadas:
            resultado = await ejecutar_funcion(llamada.name, llamada.arguments)
            entrada.append({
                "type": "function_result",
                "call_id": llamada.id,
                "name": llamada.name,
                # ensure_ascii=False conserva tildes y ñ en lugar de códigos \u
                "result": [{"type": "text", "text": json.dumps(resultado, ensure_ascii=False)}],
            })
        # Se repite el ciclo: el modelo recibe los resultados y decide si
        # necesita otra función o si ya puede responder.

    # Se agotaron las vueltas sin respuesta final.
    logger.warning("Se alcanzó el máximo de %d vueltas de herramientas", MAX_VUELTAS_HERRAMIENTAS)
    return "No pude completar la consulta. ¿Podés reformular la pregunta? 🙏"


def describir_error(error: Exception) -> str:
    codigo = getattr(error, "status_code", None)
    if codigo == 429:
        return "Estoy recibiendo demasiadas consultas. Probá de nuevo en un minuto, por favor. ⏳"
    if codigo in (401, 403):
        return "Tengo un problema de configuración. Avisale a quien me administra. 🔧"
    return "Tuve un problema para pensar la respuesta. ¿Me lo preguntás de nuevo? 🙏"


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    nombre = update.effective_user.first_name
    await update.message.reply_text(f"¡Hola {nombre}! Soy CineBot. 🎬")


async def ayuda(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
    borrados = await db.borrar_historial(update.effective_user.id)
    logger.info("Usuario %s borró %d mensajes", update.effective_user.id, borrados)
    await update.message.reply_text("Listo, empezamos de cero. ¿Qué tenés ganas de ver? 🍿")


async def responder(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Responde con Gemini, teniendo en cuenta la conversación previa."""
    user_id = update.effective_user.id
    texto = update.message.text
    logger.info("Mensaje de %s: %s", user_id, texto)

    await update.message.chat.send_action(ChatAction.TYPING)

    historial = await db.obtener_historial(user_id, MAX_MENSAJES_HISTORIAL)

    try:
        respuesta = await consultar_gemini(historial, texto)
    except Exception as error:
        logger.exception("Error al consultar a Gemini")
        await update.message.reply_text(describir_error(error))
        return

    # Guardamos en la base de datos
    await db.guardar_intercambio(user_id, texto, respuesta)

    # Enviamos a Telegram manejando posibles timeouts de red
    try:
        for inicio in range(0, len(respuesta), LIMITE_TELEGRAM):
            await update.message.reply_text(respuesta[inicio:inicio + LIMITE_TELEGRAM])
    except Exception as e:
        logger.warning(f"Error al enviar mensaje a Telegram (posible timeout): {e}")


async def manejar_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Error no previsto", exc_info=context.error)


async def al_iniciar(app: Application) -> None:
    await db.inicializar()
    logger.info("Base de datos lista (%s).", db.RUTA_DB)


async def resena(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Analiza el sentimiento de una reseña usando salida estructurada."""
    # Agarramos el texto que el usuario escribió después del comando /resena
    texto_resena = " ".join(context.args)
    
    if not texto_resena:
        await update.message.reply_text("Por favor, escribime tu reseña después del comando. Ejemplo: /resena Me encantó la película, los actores estuvieron geniales.")
        return

    await update.message.chat.send_action(ChatAction.TYPING)
    
    # Le pedimos a Gemini que analice el texto (usando las instrucciones del prompt)
    prompt_analisis = f"Analizá la siguiente reseña y devolvé solo el JSON estructurado: '{texto_resena}'"
    
    try:
        # Usamos la función consultar_gemini, pero sin herramientas para que no se confunda
        # (En un caso ideal, esto iría a una función aparte, pero así es más fácil)
        interaccion = await cliente_gemini.aio.interactions.create(
            model=GEMINI_MODEL,
            system_instruction=PROMPT_SISTEMA,
            input=prompt_analisis,
            store=False,
        )
        respuesta_json = interaccion.output_text
        
        # Le mostramos el JSON crudo al usuario (así se ve la salida estructurada)
        await update.message.reply_text(f"Análisis de la reseña:\n\n{respuesta_json}")
        
    except Exception as e:
        logger.error("Error al analizar reseña: %s", e)
        await update.message.reply_text("Hubo un error al analizar la reseña.")


def main() -> None:
    faltantes = [nombre for nombre, valor in [("TELEGRAM_TOKEN", TELEGRAM_TOKEN),
                                              ("GEMINI_API_KEY", GEMINI_API_KEY)]
                 if not valor]
    if faltantes:
        raise SystemExit(f"Faltan variables en el .env: {', '.join(faltantes)}")

    app = Application.builder().token(TELEGRAM_TOKEN).post_init(al_iniciar).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", ayuda))
    app.add_handler(CommandHandler("olvidar", olvidar))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, responder))
    app.add_error_handler(manejar_error)
    app.add_handler(CommandHandler("resena", resena))

    logger.info("CineBot v4 en línea con el modelo %s.", GEMINI_MODEL)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()