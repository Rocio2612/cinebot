import sqlite3

def inicializar_db():
    conexion = sqlite3.connect('cinebot_memoria.db')
    cursor = conexion.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS historial (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER,
            rol TEXT,
            contenido TEXT
        )
    ''')
    conexion.commit()
    conexion.close()

def guardar_mensaje(chat_id, rol, contenido):
    conexion = sqlite3.connect('cinebot_memoria.db')
    cursor = conexion.cursor()
    cursor.execute('INSERT INTO historial (chat_id, rol, contenido) VALUES (?, ?, ?)', (chat_id, rol, contenido))
    conexion.commit()
    conexion.close()

def obtener_historial_gemini(chat_id):
    conexion = sqlite3.connect('cinebot_memoria.db')
    cursor = conexion.cursor()
    cursor.execute('SELECT rol, contenido FROM historial WHERE chat_id = ? ORDER BY id DESC LIMIT 20', (chat_id,))
    filas = cursor.fetchall()
    conexion.close()

    filas.reverse()

    # Al recortar a los últimos 20 puede quedar primero una respuesta del modelo.
    # Gemini espera que el historial arranque con un mensaje del usuario.
    while filas and filas[0][0] != "user":
        filas.pop(0)

    historial = [{"role": rol, "parts": [{"text": contenido}]} for rol, contenido in filas]
    return historial
