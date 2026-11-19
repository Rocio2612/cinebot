"""Flujo guiado de reservas de cine (/reservar)."""

import logging
from datetime import date

from pydantic import BaseModel, Field, ValidationError
from telegram import ReplyKeyboardMarkup, ReplyKeyboardRemove, Update
from telegram.constants import ChatAction
from telegram.ext import (
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

import db

logger = logging.getLogger("cinebot")

PELICULA, FECHA, ENTRADAS, CONFIRMACION = range(4)

PELICULAS = [
    "El Eternauta: Parte I",
    "Dune: Parte Tres",
    "Intensamente 3",
    "Nosferatu",
]

MAX_ENTRADAS = 10
DIAS_SEMANA = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]


class FechaInterpretada(BaseModel):
    """Resultado que debe devolver el modelo al interpretar una fecha."""

    entendida: bool = Field(description="true si el texto indica una fecha concreta; false si no.")
    fecha: str = Field(description="La fecha en formato AAAA-MM-DD, o una cadena vacía si no se entendió.")


async def interpretar_fecha(cliente, modelo: str, texto: str) -> date | None:
    """Convierte una fecha escrita en lenguaje natural en un objeto date, o None."""
    hoy = date.today()
    instrucciones = (
        f"Hoy es {DIAS_SEMANA[hoy.weekday()]} {hoy.isoformat()}. "
        "Convertí la fecha que menciona el usuario al formato AAAA-MM-DD. "
        "Si el texto no indica una fecha concreta, respondé entendida=false."
    )
    interaccion = await cliente.aio.interactions.create(
        model=modelo,
        system_instruction=instrucciones,
        input=texto,
        response_format={
            "type": "text",
            "mime_type": "application/json",
            "schema": FechaInterpretada.model_json_schema(),
        },
        store=False,
    )
    resultado = FechaInterpretada.model_validate_json(interaccion.output_text)
    if not resultado.entendida:
        return None
    return date.fromisoformat(resultado.fecha)


async def inicio(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Paso 0 (/reservar): muestra las películas disponibles."""
    context.user_data["reserva"] = {}
    teclado = ReplyKeyboardMarkup([[nombre] for nombre in PELICULAS], one_time_keyboard=True)
    await update.message.reply_text(
        "¡Vamos a reservar tus entradas! ¿Para qué película querés ir?\n"
        "(Podés cancelar en cualquier momento con /cancelar)",
        reply_markup=teclado,
    )
    return PELICULA


async def elegir_pelicula(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Paso 1: valida la película elegida."""
    eleccion = update.message.text
    if eleccion not in PELICULAS:
        await update.message.reply_text("Elegí una de las películas del teclado, por favor.")
        return PELICULA

    context.user_data["reserva"]["pelicula"] = eleccion
    await update.message.reply_text(
        "¿Para qué día? Podés escribirlo como quieras: \"el sábado\", \"25 de noviembre\"…",
        reply_markup=ReplyKeyboardRemove(),
    )
    return FECHA


async def recibir_fecha(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Paso 2: interpreta la fecha con IA y la valida."""
    await update.message.chat.send_action(ChatAction.TYPING)
    cliente = context.bot_data["cliente_gemini"]
    modelo = context.bot_data["modelo"]

    try:
        fecha = await interpretar_fecha(cliente, modelo, update.message.text)
    except (ValidationError, ValueError):
        fecha = None
    except Exception:
        logger.exception("Error al interpretar la fecha")
        await update.message.reply_text("No pude procesar la fecha en este momento. Probá de nuevo.")
        return FECHA

    if fecha is None:
        await update.message.reply_text("No entendí la fecha. Probá, por ejemplo, con \"el sábado\".")
        return FECHA
    if fecha < date.today():
        await update.message.reply_text("Esa fecha ya pasó. ¿Para qué día querés reservar?")
        return FECHA

    context.user_data["reserva"]["fecha"] = fecha.isoformat()
    await update.message.reply_text(
        f"Perfecto: {DIAS_SEMANA[fecha.weekday()]} {fecha.strftime('%d/%m/%Y')}. "
        f"¿Cuántas entradas querés? (de 1 a {MAX_ENTRADAS})"
    )
    return ENTRADAS


async def recibir_entradas(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Paso 3: valida la cantidad de entradas, sin IA."""
    texto = update.message.text.strip()
    if not texto.isdigit() or not 1 <= int(texto) <= MAX_ENTRADAS:
        await update.message.reply_text(f"Escribí un número entre 1 y {MAX_ENTRADAS}.")
        return ENTRADAS

    reserva = context.user_data["reserva"]
    reserva["entradas"] = int(texto)
    fecha = date.fromisoformat(reserva["fecha"])
    teclado = ReplyKeyboardMarkup([["Confirmar", "Cancelar"]], one_time_keyboard=True)

    await update.message.reply_text(
        "Revisá los datos de tu reserva:\n\n"
        f"Película: {reserva['pelicula']}\n"
        f"Fecha: {fecha.strftime('%d/%m/%Y')}\n"
        f"Entradas: {reserva['entradas']}\n\n"
        "¿Confirmás?",
        reply_markup=teclado,
    )
    return CONFIRMACION


async def confirmar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Paso 4: guarda la reserva si el usuario confirma."""
    reserva = context.user_data.pop("reserva", {})
    if update.message.text != "Confirmar":
        await update.message.reply_text("Reserva cancelada.", reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END

    numero = await db.guardar_reserva(
        update.effective_user.id,
        reserva["pelicula"],
        reserva["fecha"],
        reserva["entradas"],
    )
    await update.message.reply_text(
        f"¡Listo! Tu reserva quedó registrada con el número {numero}. 🎟️\n"
        "(Es una reserva de prueba: no emite entradas reales.)",
        reply_markup=ReplyKeyboardRemove(),
    )
    return ConversationHandler.END


async def cancelar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """/cancelar: interrumpe la reserva en cualquier paso."""
    context.user_data.pop("reserva", None)
    await update.message.reply_text(
        "Reserva cancelada. ¿En qué más te ayudo?",
        reply_markup=ReplyKeyboardRemove(),
    )
    return ConversationHandler.END


def crear_manejador() -> ConversationHandler:
    """Arma el ConversationHandler que se registra en bot.py."""
    texto = filters.TEXT & ~filters.COMMAND
    return ConversationHandler(
        entry_points=[CommandHandler("reservar", inicio)],
        states={
            PELICULA: [MessageHandler(texto, elegir_pelicula)],
            FECHA: [MessageHandler(texto, recibir_fecha)],
            ENTRADAS: [MessageHandler(texto, recibir_entradas)],
            CONFIRMACION: [MessageHandler(texto, confirmar)],
        },
        fallbacks=[CommandHandler("cancelar", cancelar)],
    )