"""
Extracción por línea de comandos con Playwright (equivalente a instagram_json.py).

Primera vez (con Brave cerrado y la sesión de la cuenta alternativa iniciada en Brave):
    python playwright_json.py --importar-sesion

Extraer:
    python playwright_json.py nombre_de_usuario --limite 10 --fotos --videos

Los resultados se guardan en datos/<usuario>/datos.json, igual que en la interfaz web.
"""
import argparse
import os

import motor_playwright as motor
import nucleo as n


def main() -> None:
    parser = argparse.ArgumentParser(description="Extrae datos de un perfil de Instagram con Playwright.")
    parser.add_argument("usuario", nargs="?", help="Usuario de Instagram a consultar")
    parser.add_argument("--cuenta", default=os.environ.get("IG_CUENTA", ""), help="Cuenta alternativa (sesión)")
    parser.add_argument("--limite", type=int, default=10, help="Publicaciones a extraer (1-50)")
    parser.add_argument("--fotos", action="store_true", help="Descargar fotos")
    parser.add_argument("--videos", action="store_true", help="Descargar videos y reels")
    parser.add_argument("--comentarios", action="store_true", help="Obtener comentarios")
    parser.add_argument("--limite-comentarios", type=int, default=20)
    parser.add_argument("--importar-sesion", action="store_true", help="Importar la sesión desde Brave")
    args = parser.parse_args()

    try:
        if args.importar_sesion:
            cuenta = motor.importar_sesion_brave(args.cuenta)
            print(f"Sesión guardada para '{cuenta}' en {motor.ruta_estado(cuenta)}")
            return

        if not args.usuario:
            parser.error("indica el usuario a consultar (o usa --importar-sesion)")

        opciones = n.Opciones(
            limite_publicaciones=args.limite,
            fotos=args.fotos,
            videos=args.videos,
            comentarios=args.comentarios,
            limite_comentarios=args.limite_comentarios,
        )
        datos = motor.ejecutar_extraccion(
            args.usuario, args.cuenta, opciones, lambda f, msg: print(f"[{int(f * 100):3d}%] {msg}")
        )
    except (ValueError, RuntimeError) as error:
        print(f"Error: {error}")
        return

    perfil = datos["perfil"]
    print(f"\nEstado: {datos['estado']} | publicaciones guardadas: {len(datos['publicaciones'])}")
    print(f"@{perfil['usuario']} · seguidores: {perfil['seguidores']} · seguidos: {perfil['seguidos']}")
    for aviso in datos["avisos"]:
        print(f"Aviso: {aviso}")
    print(f"Resultados en: {n.carpeta_usuario(perfil['usuario'])}")


if __name__ == "__main__":
    main()
