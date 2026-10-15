"""
Convierte los respaldos .json.xz que genera Instaloader (un archivo por
publicación) en un único archivo JSON.

Uso:
    python convertidor_publicaciones_mejorado.py sausage_player --limite 50
"""
import argparse
import glob
import json
import lzma
import os
from datetime import datetime, timezone


def extraer_descripcion(nodo: dict) -> str:
    """Instaloader suele guardar el texto dentro de edge_media_to_caption."""
    caption = nodo.get("caption")
    if isinstance(caption, str):
        return caption

    edges = nodo.get("edge_media_to_caption", {}).get("edges", [])
    if edges:
        return edges[0].get("node", {}).get("text", "") or ""
    return ""


def extraer_conteo(nodo: dict, *claves: str):
    """Prueba varias claves posibles y devuelve el primer conteo que encuentre."""
    for clave in claves:
        valor = nodo.get(clave)
        if isinstance(valor, dict) and "count" in valor:
            return valor["count"]
        if isinstance(valor, (int, float)):
            return int(valor)
    return None


def extraer_fecha(nodo: dict):
    marca = nodo.get("date", nodo.get("taken_at_timestamp"))
    if isinstance(marca, (int, float)):
        # UTC, igual que post.date_utc en el otro script
        return datetime.fromtimestamp(marca, tz=timezone.utc).isoformat()
    return None


def leer_publicacion(ruta: str):
    """Devuelve un dict con la publicación, o None si el archivo no es un post."""
    with lzma.open(ruta, "rt", encoding="utf-8") as f:
        datos = json.load(f)

    if not isinstance(datos, dict):
        return None

    nodo = datos.get("node", datos)
    shortcode = nodo.get("shortcode", nodo.get("code"))

    # Sin shortcode no es una publicación (puede ser info de perfil, comentarios, etc.)
    if not shortcode:
        return None

    return {
        "id_post": str(nodo.get("id", "")) or None,
        "shortcode": shortcode,
        "url": f"https://www.instagram.com/p/{shortcode}/",
        "descripcion": extraer_descripcion(nodo),
        "likes": extraer_conteo(
            nodo, "likes", "edge_media_preview_like", "edge_liked_by"
        ),
        "comentarios": extraer_conteo(
            nodo,
            "comments",
            "edge_media_to_comment",
            "edge_media_preview_comment",
            "edge_media_to_parent_comment",
        ),
        "fecha_publicacion": extraer_fecha(nodo),
        "es_video": nodo.get("is_video"),
        "archivo_origen": os.path.basename(ruta),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Convierte respaldos .json.xz de Instaloader a JSON.")
    parser.add_argument("carpeta", nargs="?", default=".", help="Carpeta con los .json.xz")
    parser.add_argument("--limite", type=int, default=50, help="Máximo de publicaciones")
    parser.add_argument(
        "--salida",
        default="publicaciones_desde_respaldo.json",
        help="Archivo JSON de salida",
    )
    args = parser.parse_args()

    archivos = glob.glob(os.path.join(args.carpeta, "*.json.xz"))
    if not archivos:
        print(f"No se encontraron archivos .json.xz en '{args.carpeta}'.")
        return

    publicaciones = []
    omitidos = 0

    for ruta in archivos:
        nombre = os.path.basename(ruta)

        # Los comentarios se guardan en archivos aparte
        if nombre.endswith("_comments.json.xz"):
            omitidos += 1
            continue

        try:
            publicacion = leer_publicacion(ruta)
        except (OSError, lzma.LZMAError, json.JSONDecodeError, AttributeError, TypeError) as error:
            print(f"No se pudo leer {nombre}: {error}")
            omitidos += 1
            continue

        if publicacion is None:
            omitidos += 1
            continue

        publicaciones.append(publicacion)

    # Más recientes primero (igual que el otro script), y luego se aplica el límite
    publicaciones.sort(key=lambda p: p["fecha_publicacion"] or "", reverse=True)
    publicaciones = publicaciones[: args.limite]

    for numero, publicacion in enumerate(publicaciones, start=1):
        publicacion["numero_post"] = numero

    resultado = {
        "perfil": None,  # estos respaldos no incluyen seguidores ni biografía
        "publicaciones": publicaciones,
    }

    with open(args.salida, "w", encoding="utf-8") as f:
        json.dump(resultado, f, ensure_ascii=False, indent=2)

    print(f"Archivo creado: {args.salida}")
    print(f"Publicaciones procesadas: {len(publicaciones)} | archivos omitidos: {omitidos}")

    if publicaciones:
        print("\nEjemplo del primer registro:")
        print(json.dumps(publicaciones[0], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
