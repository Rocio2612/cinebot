"""
Análisis de reseñas con salida estructurada.

En lugar de pedirle al modelo un texto libre, se le exige una respuesta en
JSON que cumpla un esquema definido con Pydantic. Así el programa puede
guardar, contar y promediar los resultados sin interpretar texto.
"""

from typing import Literal              # restringe un campo a un conjunto fijo de valores

from pydantic import BaseModel, Field   # definición y validación de estructuras de datos


class AnalisisResena(BaseModel):
    """Estructura que debe tener el análisis de una reseña."""

    # Literal: el modelo solo puede responder uno de estos tres valores.
    sentimiento: Literal["positivo", "neutral", "negativo"] = Field(
        description="Sentimiento general de la reseña."
    )
    # ge y le (greater or equal, less or equal) acotan el valor entre 1 y 5.
    puntaje: int = Field(
        ge=1, le=5,
        description="Opinión del usuario sobre la película, de 1 (muy mala) a 5 (excelente).",
    )
    aspectos: list[str] = Field(
        description="Aspectos mencionados, en una o dos palabras cada uno (por ejemplo: guion, actuaciones, ritmo, final, efectos).",
    )
    resumen: str = Field(
        description="Resumen de la reseña en una sola oración breve.",
    )


# Instrucciones para esta tarea puntual. Son distintas del prompt de sistema
# de la conversación: aquí el modelo no conversa, solo clasifica.
PROMPT_ANALISIS = """
Analizás reseñas que escriben los usuarios sobre películas y series.
Respondé únicamente con el análisis pedido, en español.
Si la reseña mezcla elogios y críticas, el sentimiento es "neutral",
salvo que uno de los dos predomine con claridad.
""".strip()


async def analizar_resena(cliente, modelo: str, texto: str) -> AnalisisResena:
    """Envía la reseña a Gemini y devuelve el análisis ya validado."""

    interaccion = await cliente.aio.interactions.create(
        model=modelo,
        system_instruction=PROMPT_ANALISIS,
        input=texto,
        # response_format exige una respuesta en JSON que respete el esquema.
        # model_json_schema() traduce la clase de Pydantic a JSON Schema.
        response_format={
            "type": "text",
            "mime_type": "application/json",
            "schema": AnalisisResena.model_json_schema(),
        },
        store=False,
    )

    # model_validate_json convierte el texto JSON en un objeto AnalisisResena
    # y verifica cada campo. Si el modelo devolviera un puntaje de 9 o un
    # sentimiento no permitido, se produce un ValidationError.
    return AnalisisResena.model_validate_json(interaccion.output_text)
