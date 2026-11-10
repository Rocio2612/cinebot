import os
from dotenv import load_dotenv
from google import genai
from google.genai import types
from database import obtener_historial_gemini

# Cargamos variables de entorno y configuramos Gemini
load_dotenv()
cliente = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

instrucciones_cinebot = """Sos CineBot, un asistente experto en cine y series.
Tu personalidad es cercana, entusiasta y muy cinéfila. Hablás de manera informal,
con claridad, un toque de humor y usando voseo argentino.

Tus funciones y comportamientos principales son:
1. Recomendar por estado de ánimo: Proponé 2 o 3 opciones con año, director y
rating de IMDb.
2. Análisis de reseñas: Identificá si el sentimiento es positivo, negativo o neutro
y responde con humor.
3. Comparaciones: Armá un contraste breve resaltando año, género y estilo.

Reglas clave:
- Respuestas cortas.
- Títulos de las películas en **negrita**.
- Cerrá siempre tus mensajes con una pregunta corta."""

MODELO = "gemini-3.5-flash"

def generar_respuesta_bot(chat_id, mensaje_nuevo):
    """Devuelve la respuesta de Gemini, o None si no se pudo generar."""
    historial_previo = obtener_historial_gemini(chat_id)
    try:
        chat = cliente.chats.create(
            model=MODELO,
            config=types.GenerateContentConfig(system_instruction=instrucciones_cinebot),
            history=historial_previo
        )
        respuesta = chat.send_message(mensaje_nuevo)
        return respuesta.text or None
    except Exception as error:
        print(f"Error al consultar Gemini: {type(error).__name__}: {error}")
        return None
