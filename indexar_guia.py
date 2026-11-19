import os
import json
import asyncio
from google import genai
from dotenv import load_dotenv
from rag import RUTA_GUIA, RUTA_INDICE

load_dotenv()
cliente = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

async def main():
    print("Leyendo la guía...")
    with open(RUTA_GUIA, "r", encoding="utf-8") as f:
        contenido = f.read()

    # Dividimos por secciones (##)
    secciones_raw = contenido.split("## ")
    fragmentos = []
    
    print("Generando embeddings (esto puede tardar unos segundos)...")
    for sec in secciones_raw:
        if not sec.strip():
            continue
        
        # Formato recomendado por Google para documentos
        titulo = sec.split("\n")[0].strip()
        texto = sec.strip()
        
        # Pedir el embedding a Gemini (Modelo actualizado)
        respuesta = await cliente.aio.models.embed_content(
            model="gemini-embedding-001",
            contents=[{"parts": [{"text": f"title: {titulo} | text: {texto}"}]}]
        )
        vector = respuesta.embeddings[0].values
        
        fragmentos.append({
            "texto": texto,
            "vector": vector
        })
    
    # Guardar en JSON
    os.makedirs("guia", exist_ok=True)
    with open(RUTA_INDICE, "w", encoding="utf-8") as f:
        json.dump({"fragmentos": fragmentos, "vectores": [f["vector"] for f in fragmentos]}, f)
    
    print(f"Índice generado con {len(fragmentos)} fragmentos.")

if __name__ == "__main__":
    asyncio.run(main())