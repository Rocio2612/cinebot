"""
Herramientas de CineBot: funciones que el modelo puede pedir que se ejecuten.

El módulo tiene tres partes:
  1. Las funciones propiamente dichas, que consultan APIs externas (TMDB y OMDb).
  2. Las declaraciones: la descripción de cada función en JSON Schema,
     que es lo único que el modelo ve de ellas.
  3. Un despachador que ejecuta la función pedida por el modelo.
"""

import logging                          # registro de eventos en la terminal
import os
import unicodedata                      # para quitar tildes al comparar nombres

import httpx                            # cliente HTTP asíncrono
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("cinebot")   # mismo registrador que bot.py

TMDB_API_KEY = os.getenv("TMDB_API_KEY")
OMDB_API_KEY = os.getenv("OMDB_API_KEY")

# Tiempo máximo de espera de cada consulta HTTP, en segundos. Sin un límite,
# una API que no responde dejaría al usuario esperando indefinidamente.
TIEMPO_ESPERA = 10

# ---------------------------------------------------------------------------
# 1. Datos fijos
# ---------------------------------------------------------------------------

# Géneros que CineBot ofrece y su ID en TMDB. Usar una tabla fija evita que el
# modelo tenga que recordar números: elige un nombre y el código lo traduce.
GENEROS = {
    "terror": 27,
    "comedia": 35,
    "acción": 28,
    "drama": 18,
    "romance": 10749,
    "ciencia ficción": 878,
    "suspenso": 53,
    "aventura": 12,
    "fantasía": 14,
    "animación": 16,
    "misterio": 9648,
    "documental": 99,
}

# Géneros que se excluyen de las recomendaciones para que no se llenen de
# películas fuera de tono (drama, crimen, animación), salvo que el usuario
# pida justamente uno de ellos.
GENEROS_EXCLUIDOS = {18, 80, 16}


class ErrorAPI(Exception):
    """Falla al consultar una API externa, con un mensaje seguro para mostrar."""


def _normalizar(texto: str) -> str:
    """Pasa a minúsculas y quita tildes: 'Acción' y 'accion' quedan iguales."""
    sin_tildes = unicodedata.normalize("NFD", texto)
    return "".join(c for c in sin_tildes if unicodedata.category(c) != "Mn").lower().strip()


async def _pedir_json(url: str, parametros: dict) -> dict:
    """Hace un GET y devuelve el JSON. Si falla, lanza ErrorAPI sin datos sensibles."""
    try:
        # "async with" abre el cliente HTTP y lo cierra al terminar el bloque.
        async with httpx.AsyncClient(timeout=TIEMPO_ESPERA) as cliente_http:
            # Los parámetros (incluida la clave) los arma httpx a partir del diccionario.
            respuesta = await cliente_http.get(url, params=parametros)
            respuesta.raise_for_status()    # convierte un código 4xx o 5xx en excepción
            return respuesta.json()
    except httpx.HTTPStatusError as error:
        # El mensaje original de httpx incluye la URL completa, y con ella la
        # clave de la API. Por eso se lanza un error nuevo, sin esa información.
        raise ErrorAPI(f"la API respondió con el código {error.response.status_code}") from None
    except httpx.HTTPError:
        raise ErrorAPI("no se pudo conectar con la API") from None


# ---------------------------------------------------------------------------
# 2. Funciones
# ---------------------------------------------------------------------------

async def recomendar_por_genero(genero: str) -> dict:
    """Devuelve películas bien puntuadas de un género (TMDB)."""

    coincidencias = [nombre for nombre in GENEROS if _normalizar(nombre) == _normalizar(genero)]
    if not coincidencias:
        # El error se devuelve como dato, no como excepción: así el modelo
        # puede explicárselo al usuario y ofrecer los géneros disponibles.
        return {"error": f"Género desconocido: {genero}", "generos_disponibles": list(GENEROS)}
    nombre = coincidencias[0]
    genero_id = GENEROS[nombre]

    if not TMDB_API_KEY:
        return {"error": "Falta configurar la clave de TMDB."}

    parametros = {
        "api_key": TMDB_API_KEY,
        "with_genres": genero_id,
        # Se excluyen los géneros fuera de tono, salvo el que pidió el usuario.
        "without_genres": ",".join(str(g) for g in sorted(GENEROS_EXCLUIDOS - {genero_id})),
        "language": "es-AR",            # sinopsis en español
        # Por puntaje, con muchos votos y hasta 2024: clásicos conocidos y no estrenos raros.
        "sort_by": "vote_average.desc",
        "vote_count.gte": 2000,
        "primary_release_date.lte": "2024-12-31",
    }
    datos = await _pedir_json("https://api.themoviedb.org/3/discover/movie", parametros)

    peliculas = []
    for p in datos.get("results", [])[:3]:      # se toman las 3 primeras
        sinopsis = p.get("overview") or "Sin sinopsis disponible."
        # Se limpia el spam que aparece en algunas sinopsis de TMDB.
        if "Vercineo" in sinopsis or "Busca películas" in sinopsis:
            sinopsis = "Sinopsis no disponible en TMDB."
        peliculas.append({
            "titulo": p.get("title"),
            "anio": (p.get("release_date") or "")[:4],
            "sinopsis": sinopsis[:150],
        })

    if not peliculas:
        return {"error": f"No encontré películas de {nombre}."}
    return {"genero": nombre, "peliculas": peliculas}


async def obtener_rating(titulo: str) -> dict:
    """Devuelve el rating de IMDb y de Rotten Tomatoes de una película (OMDb)."""

    if not OMDB_API_KEY:
        return {"error": "Falta configurar la clave de OMDb."}

    # t= busca por título.
    datos = await _pedir_json("https://www.omdbapi.com/", {"t": titulo, "apikey": OMDB_API_KEY})

    if datos.get("Response") == "False":
        return {"error": f"No encontré datos para la película '{titulo}'."}

    # El rating de Rotten Tomatoes viene dentro de una lista de ratings.
    rating_rt = "N/A"
    for rating in datos.get("Ratings", []):
        if rating.get("Source") == "Rotten Tomatoes":
            rating_rt = rating.get("Value")
            break

    return {
        "titulo": datos.get("Title", titulo),
        "anio": datos.get("Year"),
        "imdb": datos.get("imdbRating", "N/A"),
        "rotten_tomatoes": rating_rt,
    }


# ---------------------------------------------------------------------------
# 3. Declaraciones para el modelo
# ---------------------------------------------------------------------------

# Esto es lo único que Gemini conoce de cada función: su nombre, para qué sirve
# y qué parámetros recibe, descriptos con JSON Schema. La calidad de las
# descripciones determina si el modelo elige bien la función y sus argumentos.
DECLARACIONES = [
    {
        "type": "function",
        "name": "recomendar_por_genero",
        "description": (
            "Obtiene tres películas muy bien puntuadas de un género, con año y sinopsis. "
            "Usala cuando el usuario pida una recomendación de un género, o cuando "
            "sepas qué género le gusta y quieras proponerle algo para ver."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "genero": {
                    "type": "string",
                    "enum": list(GENEROS),      # el modelo solo puede elegir uno de estos nombres
                    "description": "Género. Si el usuario pide otro, elegí el más parecido de la lista.",
                },
            },
            "required": ["genero"],
        },
    },
    {
        "type": "function",
        "name": "obtener_rating",
        "description": (
            "Obtiene el rating de IMDb (sobre 10) y de Rotten Tomatoes (porcentaje) de una película. "
            "Usala siempre que el usuario pregunte por el puntaje, el rating o qué tan buena es una película."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "titulo": {
                    "type": "string",
                    "description": "Título de la película, preferentemente el original (en inglés si corresponde).",
                },
            },
            "required": ["titulo"],
        },
    },
]


# ---------------------------------------------------------------------------
# 4. Despachador
# ---------------------------------------------------------------------------

# Relaciona el nombre que usa el modelo con la función de Python que se ejecuta.
FUNCIONES = {
    "recomendar_por_genero": recomendar_por_genero,
    "obtener_rating": obtener_rating,
}


async def ejecutar_funcion(nombre: str, argumentos: dict) -> dict:
    """Ejecuta la función pedida por el modelo y devuelve siempre un diccionario."""
    logger.info("El modelo pidió ejecutar: %s(%s)", nombre, argumentos)

    funcion = FUNCIONES.get(nombre)
    if funcion is None:                 # el modelo pidió una función que no existe
        return {"error": f"Función desconocida: {nombre}"}

    try:
        # **argumentos desempaqueta el diccionario como parámetros con nombre:
        # {"genero": "terror"} se convierte en recomendar_por_genero(genero="terror").
        return await funcion(**argumentos)
    except ErrorAPI as error:
        logger.warning("Falló la herramienta %s: %s", nombre, error)
        return {"error": f"No se pudo obtener el dato: {error}"}
    except Exception as error:
        # Cualquier otra falla (argumentos inválidos, etc.) se informa al modelo
        # como dato, para que pueda responder al usuario.
        logger.exception("Falló la herramienta %s", nombre)
        return {"error": f"No se pudo obtener el dato: {type(error).__name__}"}
