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
from api import recomendar_por_genero, obtener_rating
from prompts import PROMPT_SISTEMA

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")

MAX_MENSAJES_HISTORIAL = 20

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpx2").setLevel(logging.WARNING)
logger = logging.getLogger("cinebot")

LIMITE_TELEGRAM = 4096

cliente_gemini = genai.Client(api_key=GEMINI_API_KEY)

# Herramientas como diccionarios (formato que espera la Interactions API)
herramientas = [
    {
        "type": "function",
        "name": "recomendar_por_genero",
        "description": "Busca películas populares según el ID de un género. Usa 27 para terror, 35 para comedia, 28 para acción, 18 para drama, 10749 para romance.",
        "parameters": {
            "type": "object",
            "properties": {
                "genero_id": {
                    "type": "integer",
                    "description": "El ID numérico del género."
                }
            },
            "required": ["genero_id"]
        }
    },
    {
        "type": "function",
        "name": "obtener_rating",
        "description": "Obtiene el rating de IMDb y Rotten Tomatoes de una película específica.",
        "parameters": {
            "type": "object",
            "properties": {
                "titulo": {
                    "type": "string",
                    "description": "El título exacto de la película."
                }
            },
            "required": ["titulo"]
        }
    }
]


def armar_entrada(historial: list[tuple[str, str]], mensaje_nuevo: str) -> list[dict]:
    tipo_de_paso = {"user": "user_input", "model": "model_output"}
    pasos = [
        {"type": tipo_de_paso[rol], "content": [{"type": "text", "text": texto}]}
        for rol, texto in historial
    ]
    pasos.append({"type": "user_input", "content": [{"type": "text", "text": mensaje_nuevo}]})
    return pasos


async def consultar_gemini(historial: list, mensaje_nuevo: str) -> str:
    try:
        entrada = armar_entrada(historial, mensaje_nuevo)

        # 1. Primera llamada: Gemini decide si usar una herramienta
        interaccion = await cliente_gemini.aio.interactions.create(
            model=GEMINI_MODEL,
            system_instruction=PROMPT_SISTEMA,
            input=entrada,
            tools=herramientas,
            store=False,
        )

        # 2. Buscar si pidió ejecutar una función
        tool_call = None
        for step in interaccion.steps:
            if step.type == "function_call":
                tool_call = step
                break

        if not tool_call:
            return interaccion.output_text or "No se me ocurrió nada para responder."

        nombre_funcion = tool_call.name
        argumentos = tool_call.arguments
        logger.info(f"El modelo pidió ejecutar: {nombre_funcion} con {argumentos}")

        # 3. Ejecutar la función real en Python
        if nombre_funcion == "recomendar_por_genero":
            resultado_funcion = await recomendar_por_genero(int(argumentos.get("genero_id", 27)))
        elif nombre_funcion == "obtener_rating":
            resultado_funcion = await obtener_rating(str(argumentos.get("titulo", "")))
        else:
            resultado_funcion = "Función no reconocida."

        logger.info(f"Resultado de la función: {resultado_funcion}")

                # 4. Devolver el resultado a Gemini
        paso_resultado = {
            "type": "function_result",
            "name": nombre_funcion,
            "call_id": tool_call.id,
            "result": [{"type": "text", "text": str(resultado_funcion)}]
        }

        # CLAVE: Volvemos a mandar el historial + el resultado al final
        entrada_con_resultado = entrada + [paso_resultado]

        interaccion_final = await cliente_gemini.aio.interactions.create(
            model=GEMINI_MODEL,
            system_instruction=PROMPT_SISTEMA,
            input=entrada_con_resultado,  # <--- Historial + resultado
            tools=herramientas,
            store=False,
            previous_interaction_id=interaccion.id
        )

        return interaccion_final.output_text or "No se me ocurrió nada para responder."
    except Exception as e:
        logger.exception("Error detallado en consultar_gemini")
        return f"Error interno: {str(e)}"


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
    user_id = update.effective_user.id
    texto = update.message.text
    logger.info("Mensaje de %s: %s", user_id, texto)

    await update.message.chat.send_action(ChatAction.TYPING)

    historial = await db.obtener_historial(user_id, MAX_MENSAJES_HISTORIAL)

    try:
        respuesta = await consultar_gemini(historial, texto)
    except Exception as error:
        logger.error("Error al consultar a Gemini: %s", error)
        await update.message.reply_text(describir_error(error))
        return

    await db.guardar_intercambio(user_id, texto, respuesta)

    for inicio in range(0, len(respuesta), LIMITE_TELEGRAM):
        await update.message.reply_text(respuesta[inicio:inicio + LIMITE_TELEGRAM])


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

    logger.info("CineBot v3 en línea con el modelo %s.", GEMINI_MODEL)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()