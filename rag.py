import os
import json
import numpy as np
from google import genai

RUTA_GUIA = "guia/guia_peliculas.md"
RUTA_INDICE = "guia/indice.json"
UMBRAL_SIMILITUD = 0.60

# Variables globales para guardar el índice en memoria
_fragmentos = []
_matriz_vectores = None
_cliente = None

async def inicializar(cliente_gemini):
    """Carga el índice en memoria al arrancar el bot."""
    global _cliente, _fragmentos, _matriz_vectores
    _cliente = cliente_gemini
    
    if not os.path.exists(RUTA_INDICE):
        print("Error: No se encontró el índice. Ejecutá indexar_guia.py primero.")
        return

    with open(RUTA_INDICE, "r", encoding="utf-8") as f:
        datos = json.load(f)
        _fragmentos = datos["fragmentos"]
        _matriz_vectores = np.array(datos["vectores"])
    
    print(f"Guía cargada: {len(_fragmentos)} fragmentos.")

async def buscar_en_guia(consulta: str) -> str:
    """Busca en la guía el fragmento más similar a la consulta."""
    if _matriz_vectores is None:
        return "La guía no está indexada."

    # 1. Convertir la consulta a vector usando Gemini (Modelo actualizado)
    respuesta_embedding = await _cliente.aio.models.embed_content(
        model="gemini-embedding-001", 
        contents=[{"parts": [{"text": f"task: search result | query: {consulta}"}]}]
    )
    vector_consulta = np.array(respuesta_embedding.embeddings[0].values)

    # 2. Calcular similitud coseno con todos los fragmentos
    similitudes = np.dot(_matriz_vectores, vector_consulta)
    
    # 3. Buscar el índice del fragmento más parecido
    mejor_indice = np.argmax(similitudes)
    mejor_similitud = similitudes[mejor_indice]

    print(f"Búsqueda en guía: '{consulta}' -> Similitud: {mejor_similitud:.2f}")

    # 4. Verificar si supera el umbral
    if mejor_similitud >= UMBRAL_SIMILITUD:
        return _fragmentos[mejor_indice]["texto"]
    else:
        return "No encontré información sobre eso en mi guía de cine."