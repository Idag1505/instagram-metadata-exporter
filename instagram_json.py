import json
import getpass
import instaloader
from instaloader.exceptions import (
    InstaloaderException,
    TwoFactorAuthRequiredException,
)


print("--- Extractor seguro de Instagram ---")
PERFIL_OBJETIVO = input("Ingresa el perfil objetivo a consultar: ").strip()


CUENTA_ALTERNATIVA = input("Ingresa tu usuario de cuenta alterna para el login: ").strip()

loader = instaloader.Instaloader()

try:
    loader.load_session_from_file(CUENTA_ALTERNATIVA)
    print("Sesión local cargada.")

except FileNotFoundError:
    print(
        "No existe una sesión guardada. "
        "Inicia sesión primero y completa cualquier verificación."
    )

    try:
        loader.interactive_login(CUENTA_ALTERNATIVA)
        loader.save_session_to_file(CUENTA_ALTERNATIVA)
        print("Sesión guardada correctamente.")

    except TwoFactorAuthRequiredException:
        print(
            "Instagram solicita autenticación en dos pasos. "
            "Completa el proceso de verificación."
        )

    except InstaloaderException as error:
        print(f"No se pudo iniciar sesión: {error}")


LIMITE_PUBLICACIONES = 50
ARCHIVO_SALIDA = "publicaciones_instagram.json"


def main():
    loader = instaloader.Instaloader(
        download_pictures=False,
        download_videos=False,
        download_video_thumbnails=False,
        save_metadata=False,
        quiet=False,
    )

    # Consultar el perfil objetivo introducido por teclado
    perfil = instaloader.Profile.from_username(
        loader.context,
        PERFIL_OBJETIVO,
    )

    publicaciones = []

    for numero, post in enumerate(perfil.get_posts(), start=1):
        if numero > LIMITE_PUBLICACIONES:
            break

        publicaciones.append({
            "numero_post": numero,
            "id_post": str(post.mediaid),
            "shortcode": post.shortcode,
            "url": f"https://www.instagram.com/p/{post.shortcode}/",
            "descripcion": post.caption or "",
            "likes": post.likes,
            "comentarios": post.comments,
            "fecha_publicacion": post.date_utc.isoformat(),
            "es_video": post.is_video,
        })

        print(
            f"Publicación {numero}: "
            f"{post.date_utc.isoformat()}"
        )

    with open(
        ARCHIVO_SALIDA,
        "w",
        encoding="utf-8",
    ) as archivo:
        json.dump(
            publicaciones,
            archivo,
            ensure_ascii=False,
            indent=2,
        )

    print(
        f"\nSe guardaron {len(publicaciones)} publicaciones "
        f"en {ARCHIVO_SALIDA}"
    )


if __name__ == "__main__":
    try:
        main()
    except instaloader.exceptions.InstaloaderException as error:
        print(f"Error de Instagram o Instaloader: {error}")
    except Exception as error:
        print(f"Error: {error}")
