import os
import httpx
from dotenv import load_dotenv

load_dotenv()

TMDB_API_KEY = os.getenv("TMDB_API_KEY")
OMDB_API_KEY = os.getenv("OMDB_API_KEY")

# --- Función 1: Recomendar por género (TMDB) ---
async def recomendar_por_genero(genero_id: int) -> str:
    """Busca películas populares de un género específico en TMDB."""
    # Usamos vote_average para que traiga clásicos conocidos y no estrenos raros
    url = f"https://api.themoviedb.org/3/discover/movie?api_key={TMDB_API_KEY}&with_genres={genero_id}&without_genres=18,80,16&language=es-AR&sort_by=vote_average.desc&vote_count.gte=2000&primary_release_date.lte=2024-12-31"
    async with httpx.AsyncClient() as client:
        try:
            respuesta = await client.get(url)
            respuesta.raise_for_status()  # Lanza error si la respuesta no es 200
            datos = respuesta.json()
            
            peliculas = datos.get("results", [])[:3]  # Tomamos las 3 primeras
            
            if not peliculas:
                return "No encontré películas de ese género."
                
            resultado = []
            for p in peliculas:
                sinopsis = p.get('overview', 'Sin sinopsis disponible.')
                
                # Limpiamos el spam de Vercineo
                if "Vercineo" in sinopsis or "Busca películas" in sinopsis:
                    sinopsis = "Sinopsis no disponible en TMDB."
                else:
                    sinopsis = sinopsis[:150]
                    
                resultado.append(f"- {p['title']} ({p.get('release_date', 'N/A')[:4]}): {sinopsis}...")
            
            return "\n".join(resultado)
                
        except Exception as e:
            return f"Error al consultar TMDB: {str(e)}"
# --- Función 2: Obtener rating real (OMDb) ---
async def obtener_rating(titulo: str) -> str:
    """Busca el rating de IMDb y Rotten Tomatoes de una película en OMDb."""
    # t= busca por título exacto
    url = f"https://www.omdbapi.com/?t={titulo}&apikey={OMDB_API_KEY}"
    
    async with httpx.AsyncClient() as client:
        try:
            respuesta = await client.get(url)
            respuesta.raise_for_status()
            datos = respuesta.json()
            
            if datos.get("Response") == "False":
                return f"No encontré datos para la película '{titulo}'."
                
            rating_imdb = datos.get("imdbRating", "N/A")
            
            # Buscamos el rating de Rotten Tomatoes en la lista de Ratings
            rating_rt = "N/A"
            for rating in datos.get("Ratings", []):
                if rating["Source"] == "Rotten Tomatoes":
                    rating_rt = rating["Value"]
                    break
            
            return f"La película '{titulo}' tiene un rating de {rating_imdb} en IMDb y {rating_rt} en Rotten Tomatoes."
        except Exception as e:
            return f"Error al consultar OMDb: {str(e)}"