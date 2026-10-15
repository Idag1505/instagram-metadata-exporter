import json
import lzma
import glob
import os
from datetime import datetime

print("--- Procesador de respaldos de Instagram (.json.xz) ---")
CARPETA = input("Ingresa la ruta de la carpeta con los archivos (ej. .): ").strip() or "."
USUARIO = input("Ingresa el nombre de usuario objetivo: ").strip()
LIMITE = 50
ARCHIVO_SALIDA = "publicaciones_instagram.json"

publicaciones = []

# Buscamos todos los archivos comprimidos .json.xz en la carpeta indicada
archivos = sorted(
    glob.glob(os.path.join(CARPETA, "*.json.xz"))
)

for archivo in archivos:
    nombre_archivo = os.path.basename(archivo)
    
    # Ignorar de forma dinámica cualquier archivo que pertenezca a la info general del perfil
    if USUARIO in nombre_archivo and ("profile" in nombre_archivo or "info" in nombre_archivo):
        continue

    try:
        with lzma.open(archivo, "rt", encoding="utf-8") as f:
            datos = json.load(f)

        # Los archivos de publicaciones suelen contener un objeto con la clave "node" o directamente los datos.
        nodo = datos.get("node", datos)

        fecha_unix = nodo.get("date", nodo.get("taken_at_timestamp"))
        fecha = None

        if isinstance(fecha_unix, (int, float)):
            fecha = datetime.fromtimestamp(fecha_unix).isoformat()

        shortcode = nodo.get("shortcode", nodo.get("code"))
        url = (
            f"https://www.instagram.com/p/{shortcode}/"
            if shortcode else None
        )

        publicacion = {
            "fecha": fecha,
            "descripcion": nodo.get("caption"),
            "likes": nodo.get("likes", nodo.get("edge_liked_by", {}).get("count")),
            "comentarios": nodo.get(
                "comments",
                nodo.get("edge_media_to_comment", {}).get("count")
            ),
            "url": url,
            "archivo_origen": nombre_archivo
        }

        publicaciones.append(publicacion)

    except (OSError, json.JSONDecodeError, TypeError) as e:
        print(f"No se pudo leer {nombre_archivo}: {e}")

publicaciones = publicaciones[:LIMITE]

with open(ARCHIVO_SALIDA, "w", encoding="utf-8") as f:
    json.dump(publicaciones, f, ensure_ascii=False, indent=2)

print(f"\nArchivo creado: {ARCHIVO_SALIDA}")
print(f"Publicaciones procesadas: {len(publicaciones)}")

if publicaciones:
    print("\nEjemplo del primer registro:")
    print(json.dumps(publicaciones[0], ensure_ascii=False, indent=2))
