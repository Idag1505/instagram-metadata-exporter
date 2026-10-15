import json
import random
import time
from itertools import islice

import instaloader
from instaloader.exceptions import (
    ConnectionException,
    InstaloaderException,
    LoginRequiredException,
    PrivateProfileNotFollowedException,
    ProfileNotExistsException,
    TooManyRequestsException,
)

# ----------------------------- Configuración -----------------------------
PERFIL_OBJETIVO = "sausage_player"
CUENTA_ALTERNATIVA = "mish.2335"
LIMITE_PUBLICACIONES = 50
ARCHIVO_SALIDA = "publicaciones_instagram.json"

# Ponlo en True SOLO la primera vez (con Brave cerrado) para importar las
# cookies del navegador y guardar la sesión de Instaloader. Luego vuelve a False.
IMPORTAR_DESDE_BRAVE = False

# Pausa aleatoria entre publicaciones (segundos), para no parecer un bot agresivo
PAUSA_MIN, PAUSA_MAX = 1.0, 3.0


# ------------------------------- Sesión ----------------------------------
def crear_loader() -> instaloader.Instaloader:
    return instaloader.Instaloader(
        download_pictures=False,
        download_videos=False,
        download_video_thumbnails=False,
        download_geotags=False,
        download_comments=False,
        save_metadata=False,
        compress_json=False,
        quiet=False,
    )


def importar_sesion_brave(loader: instaloader.Instaloader) -> None:
    """Convierte las cookies de Brave en una sesión de Instaloader."""
    import browser_cookie3  # pip install browser-cookie3

    cookies = browser_cookie3.brave(domain_name="instagram.com")
    datos = {c.name: c.value for c in cookies}

    if "sessionid" not in datos:
        raise RuntimeError(
            "No se encontró la cookie 'sessionid'. Inicia sesión en "
            "instagram.com desde Brave y vuelve a intentarlo."
        )

    loader.load_session(CUENTA_ALTERNATIVA, datos)
    usuario = loader.test_login()

    if usuario is None:
        raise RuntimeError("Las cookies importadas no iniciaron sesión (¿expiradas?).")

    if usuario != CUENTA_ALTERNATIVA:
        print(
            f"Aviso: las cookies pertenecen a '{usuario}', "
            f"no a '{CUENTA_ALTERNATIVA}'. Se usará '{usuario}'."
        )

    loader.context.username = usuario
    loader.save_session_to_file()
    print(f"Sesión de '{usuario}' guardada. Ya puedes poner IMPORTAR_DESDE_BRAVE = False.")


def obtener_loader() -> instaloader.Instaloader:
    loader = crear_loader()

    if IMPORTAR_DESDE_BRAVE:
        importar_sesion_brave(loader)
    else:
        try:
            loader.load_session_from_file(CUENTA_ALTERNATIVA)
        except FileNotFoundError:
            raise RuntimeError(
                "No hay sesión guardada. Ejecuta una vez con "
                "IMPORTAR_DESDE_BRAVE = True."
            )

    usuario = loader.test_login()
    if usuario is None:
        raise RuntimeError("La sesión no es válida. Vuelve a importar las cookies.")

    print(f"Sesión activa como: {usuario}")
    return loader


# ------------------------------ Extracción -------------------------------
def post_a_diccionario(numero: int, post: instaloader.Post) -> dict:
    return {
        "numero_post": numero,
        "id_post": str(post.mediaid),
        "shortcode": post.shortcode,
        "url": f"https://www.instagram.com/p/{post.shortcode}/",
        "usuario": post.owner_username,
        "tipo": post.typename,  # GraphImage, GraphVideo, GraphSidecar
        "cantidad_medios": post.mediacount,
        "descripcion": post.caption or "",
        "hashtags": list(post.caption_hashtags),
        "menciones": list(post.caption_mentions),
        "likes": post.likes,  # puede ser -1 si el autor oculta los likes
        "comentarios": post.comments,
        "vistas_video": post.video_view_count if post.is_video else None,
        "fecha_publicacion": post.date_utc.isoformat(),
        "es_video": post.is_video,
    }


def perfil_a_diccionario(perfil: instaloader.Profile) -> dict:
    return {
        "usuario": perfil.username,
        "nombre_completo": perfil.full_name,
        "biografia": perfil.biography or "",
        "seguidores": perfil.followers,
        "seguidos": perfil.followees,
        "total_publicaciones": perfil.mediacount,
        "url_externa": perfil.external_url,
        "es_privado": perfil.is_private,
        "es_verificado": perfil.is_verified,
    }


def obtener_publicaciones(perfil: instaloader.Profile) -> list[dict]:
    publicaciones = []
    posts = islice(perfil.get_posts(), LIMITE_PUBLICACIONES)

    for numero, post in enumerate(posts, start=1):
        try:
            publicaciones.append(post_a_diccionario(numero, post))
            print(f"Publicación {numero}: {post.date_utc.isoformat()}")
        except InstaloaderException as error:
            # Un post fallido no debe tirar todo el proceso
            print(f"Se omitió la publicación {numero}: {error}")

        time.sleep(random.uniform(PAUSA_MIN, PAUSA_MAX))

    return publicaciones


def guardar_json(datos_perfil: dict, publicaciones: list[dict]) -> None:
    resultado = {
        "perfil": datos_perfil,
        "publicaciones": publicaciones,
    }
    with open(ARCHIVO_SALIDA, "w", encoding="utf-8") as archivo:
        json.dump(resultado, archivo, ensure_ascii=False, indent=2)
    print(f"\nSe guardaron los datos del perfil y {len(publicaciones)} publicaciones en {ARCHIVO_SALIDA}")


def main() -> None:
    loader = obtener_loader()

    perfil = instaloader.Profile.from_username(loader.context, PERFIL_OBJETIVO)
    datos_perfil = perfil_a_diccionario(perfil)
    print(
        f"Perfil: {datos_perfil['usuario']} | "
        f"seguidores: {datos_perfil['seguidores']} | "
        f"seguidos: {datos_perfil['seguidos']}"
    )
    print(f"Biografía: {datos_perfil['biografia']}\n")

    publicaciones = obtener_publicaciones(perfil)
    guardar_json(datos_perfil, publicaciones)


if __name__ == "__main__":
    try:
        main()
    except ProfileNotExistsException:
        print(f"El perfil '{PERFIL_OBJETIVO}' no existe.")
    except PrivateProfileNotFollowedException:
        print(f"'{PERFIL_OBJETIVO}' es privado y tu cuenta no lo sigue.")
    except TooManyRequestsException:
        print("Instagram limitó las peticiones. Espera un buen rato antes de reintentar.")
    except LoginRequiredException:
        print("Instagram exige iniciar sesión. Reimporta las cookies.")
    except ConnectionException as error:
        print(f"Problema de conexión con Instagram: {error}")
    except InstaloaderException as error:
        print(f"Error de Instaloader: {error}")
    except RuntimeError as error:
        print(f"Error: {error}")
