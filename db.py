import aiosqlite

RUTA_DB = "cinebot.db"

ESQUEMA = """
CREATE TABLE IF NOT EXISTS mensajes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    rol TEXT NOT NULL CHECK (rol IN ('user', 'model')),
    texto TEXT NOT NULL,
    fecha TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_mensajes_usuario ON mensajes (user_id, id);
"""

async def inicializar() -> None:
    async with aiosqlite.connect(RUTA_DB) as db:
        await db.executescript(ESQUEMA)
        await db.commit()

async def guardar_intercambio(user_id: int, pregunta: str, respuesta: str) -> None:
    async with aiosqlite.connect(RUTA_DB) as db:
        await db.executemany(
            "INSERT INTO mensajes (user_id, rol, texto) VALUES (?, ?, ?)",
            [(user_id, "user", pregunta), (user_id, "model", respuesta)],
        )
        await db.commit()

async def obtener_historial(user_id: int, limite: int) -> list[tuple[str, str]]:
    async with aiosqlite.connect(RUTA_DB) as db:
        cursor = await db.execute(
            "SELECT rol, texto FROM mensajes WHERE user_id = ? ORDER BY id DESC LIMIT ?",
            (user_id, limite),
        )
        filas = await cursor.fetchall()
        return list(reversed(filas))

async def borrar_historial(user_id: int) -> int:
    async with aiosqlite.connect(RUTA_DB) as db:
        cursor = await db.execute("DELETE FROM mensajes WHERE user_id = ?", (user_id,))
        await db.commit()
        return cursor.rowcount