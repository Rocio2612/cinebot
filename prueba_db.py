import asyncio                          # permite ejecutar corrutinas desde un script

import db                               # el módulo recién creado


async def main():
    await db.inicializar()                                           # crea la tabla
    await db.guardar_intercambio(1, "Hola", "¡Hola! Soy CineBot.")     # usuario de prueba: id 1
    await db.guardar_intercambio(1, "¿Y qué me recomendás?", "Depende de tu ánimo.")
    print(await db.obtener_historial(1, 20))                         # los cuatro mensajes, en orden
    print(await db.obtener_historial(1, 2))                          # solo los dos últimos
    print("Borrados:", await db.borrar_historial(1))                 # cantidad eliminada
    print(await db.obtener_historial(1, 20))                         # lista vacía

asyncio.run(main())                     # pone en marcha el bucle de eventos
