import json                             # convierte los resultados de las funciones a texto JSON
import logging
import os
from dotenv import load_dotenv
from google import genai
from pydantic import ValidationError    # error que se produce si el JSON no cumple el esquema
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
import reservas
from analisis import analizar_resena    # análisis estructurado de reseñas
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

def armar_entrada(historial: list[tuple[str, str]], contenido_nuevo: list[dict]) -> list[dict]:
    """
    Convierte el historial guardado en la lista de pasos (steps) que espera
    la Interactions API y agrega al final el contenido nuevo del usuario.

    contenido_nuevo es una lista de bloques: texto, imagen o audio.
    """
    # Equivalencia entre el rol guardado en la base y el tipo de paso de la API.
    tipo_de_paso = {"user": "user_input", "model": "model_output"}

    # Un diccionario por cada fila del historial. En el historial, el
    # contenido es siempre un único bloque de texto.
    pasos = [
        {"type": tipo_de_paso[rol], "content": [{"type": "text", "text": texto}]}
        for rol, texto in historial
    ]

    # El último paso es lo que el usuario acaba de enviar, tal como llegó:
    # ya es una lista de bloques, así que no se envuelve en otra lista.
    pasos.append({"type": "user_input", "content": contenido_nuevo})
    return pasos


async def consultar_gemini(
    historial: list[tuple[str, str]],
    contenido_nuevo: list[dict],               # antes: mensaje_nuevo: str
    registro: list[str] | None = None,         # nuevo: lista opcional
) -> str:
    """
    Envía la conversación a Gemini y resuelve el ciclo de function calling:
    mientras el modelo pida funciones, se ejecutan y se le devuelven los
    resultados; cuando responde con texto, se devuelve ese texto.

    Si se pasa una lista en `registro`, se agrega en ella el nombre de cada
    función que el modelo pidió (lo usa evaluar.py).
    """
    # Lista de pasos que se envía al modelo. Empieza con el historial y el
    # contenido nuevo, y crece dentro de este turno con los pedidos de funciones
    # y sus resultados. Esos pasos intermedios no se guardan en la base.
    entrada = armar_entrada(historial, contenido_nuevo)

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
            if registro is not None:           # nuevo: solo si se pasó una lista
                registro.append(llamada.name)  # nuevo: anota la herramienta pedida
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
        "/olvidar - Borra nuestra conversación y empezamos de cero\n"
        "/resena <texto> - Dejá tu opinión sobre una película o serie\n"
        "/resenas - Resumen de las opiniones recibidas\n\n"
        "/reservar - Reservá entradas paso a paso\n"
        "Y, sobre todo, podés escribirme qué tenés ganas de ver: te recomiendo "
        "películas o series, te cuento de qué trata una o te comento tu reseña. "
        "Me acuerdo de lo que hablamos."
    )


async def olvidar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    borrados = await db.borrar_historial(update.effective_user.id)
    logger.info("Usuario %s borró %d mensajes", update.effective_user.id, borrados)
    await update.message.reply_text("Listo, empezamos de cero. ¿Qué tenés ganas de ver? 🍿")


async def procesar_mensaje(update: Update, contenido: list[dict], texto_para_historial: str) -> None:
    """
    Pasos comunes a cualquier mensaje (texto, foto o voz): recuperar el
    historial, consultar al modelo, guardar el intercambio y responder.
    """
    user_id = update.effective_user.id                  # identifica de quién es la conversación
    await update.message.chat.send_action(ChatAction.TYPING)

    # Paso 1: recuperar lo que ya se habló con este usuario.
    historial = await db.obtener_historial(user_id, MAX_MENSAJES_HISTORIAL)

    # Paso 2: consultar al modelo con el historial más el contenido nuevo.
    try:
        respuesta = await consultar_gemini(historial, contenido)
    except Exception as error:
        logger.exception("Error al consultar a Gemini")   # registra el error con la traza completa
        await update.message.reply_text(describir_error(error))
        return                                          # si falló, no se guarda nada

    # Paso 3: guardar el intercambio. En la base solo se guarda texto: para
    # fotos y audios se guarda una descripción breve, no el archivo.
    await db.guardar_intercambio(user_id, texto_para_historial, respuesta)

    # Paso 4: responder, en partes si supera el límite de Telegram, manejando
    # posibles timeouts de red.
    try:
        for inicio in range(0, len(respuesta), LIMITE_TELEGRAM):
            await update.message.reply_text(respuesta[inicio:inicio + LIMITE_TELEGRAM])
    except Exception as e:
        logger.warning(f"Error al enviar mensaje a Telegram (posible timeout): {e}")


async def responder(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Responde un mensaje de texto, teniendo en cuenta la conversación previa."""
    texto = update.message.text
    logger.info("Mensaje de %s: %s", update.effective_user.id, texto)
    # El texto se convierte en una lista con un solo bloque. En el historial
    # se guarda el mismo texto.
    await procesar_mensaje(update, [{"type": "text", "text": texto}], texto)


async def manejar_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Error no previsto", exc_info=context.error)


async def al_iniciar(app: Application) -> None:
    await db.inicializar()
    logger.info("Base de datos lista (%s).", db.RUTA_DB)


async def resena(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Responde al comando /resena: analiza la reseña y la guarda."""
    # context.args contiene las palabras escritas después del comando,
    # separadas por espacios: "/resena Muy buena" → ["Muy", "buena"].
    texto = " ".join(context.args).strip()
    if not texto:
        await update.message.reply_text(
            "Escribí tu opinión después del comando. Por ejemplo:\n"
            "/resena Me encantó la película, los actores estuvieron geniales."
        )
        return

    await update.message.chat.send_action(ChatAction.TYPING)

    try:
        analisis = await analizar_resena(cliente_gemini, GEMINI_MODEL, texto)
    except ValidationError:
        # El modelo respondió, pero el JSON no cumple el esquema.
        logger.exception("Análisis de reseña con formato inválido")
        await update.message.reply_text("No pude analizar tu reseña. ¿Probás escribirla de nuevo? 🙏")
        return
    except Exception as error:
        logger.exception("Error al analizar la reseña")
        await update.message.reply_text(describir_error(error))
        return

    await db.guardar_resena(update.effective_user.id, texto, analisis)

    # El análisis es un objeto: sus campos se usan directamente, sin interpretar texto.
    emoji = {"positivo": "😊", "neutral": "😐", "negativo": "😞"}[analisis.sentimiento]
    await update.message.reply_text(
        f"¡Gracias por tu opinión! {emoji}\n\n"
        f"Sentimiento: {analisis.sentimiento}\n"
        f"Puntaje: {'⭐' * analisis.puntaje} ({analisis.puntaje}/5)\n"
        f"Aspectos: {', '.join(analisis.aspectos)}\n"
        f"Resumen: {analisis.resumen}"
    )


async def resenas(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Responde al comando /resenas: muestra el resumen de todas las reseñas."""
    datos = await db.resumen_resenas()
    if datos["cantidad"] == 0:
        await update.message.reply_text("Todavía no recibí reseñas. Podés dejar la tuya con /resena.")
        return

    conteo = datos["por_sentimiento"]
    await update.message.reply_text(
        f"Reseñas recibidas: {datos['cantidad']}\n"
        f"Puntaje promedio: {datos['promedio']:.1f}/5\n"     # :.1f muestra un decimal
        f"Positivas: {conteo.get('positivo', 0)} · "
        f"Neutrales: {conteo.get('neutral', 0)} · "
        f"Negativas: {conteo.get('negativo', 0)}"
    )


def main() -> None:
    faltantes = [nombre for nombre, valor in [("TELEGRAM_TOKEN", TELEGRAM_TOKEN),
                                              ("GEMINI_API_KEY", GEMINI_API_KEY)]
                 if not valor]
    if faltantes:
        raise SystemExit(f"Faltan variables en el .env: {', '.join(faltantes)}")

    app = Application.builder().token(TELEGRAM_TOKEN).post_init(al_iniciar).build()

    app.bot_data["cliente_gemini"] = cliente_gemini
    app.bot_data["modelo"] = GEMINI_MODEL

    app.add_handler(reservas.crear_manejador())
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", ayuda))
    app.add_handler(CommandHandler("olvidar", olvidar))
    app.add_handler(CommandHandler("resena", resena))
    app.add_handler(CommandHandler("resenas", resenas))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, responder))
    app.add_error_handler(manejar_error)

    logger.info("CineBot v6 en línea con el modelo %s.", GEMINI_MODEL)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()