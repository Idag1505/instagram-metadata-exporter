"""
Núcleo de la aplicación: extracción de datos con Instaloader y análisis Big Five
con la API de Gemini. Las interfaces (app_web.py / app.py) solo llaman a estas funciones.
"""
import glob
import hashlib
import json
import os
import random
import re
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from itertools import islice
from pathlib import Path
from typing import Callable, Optional

import instaloader
from dotenv import load_dotenv
from instaloader.exceptions import (
    AbortDownloadException,
    ConnectionException,
    InstaloaderException,
    LoginRequiredException,
    PrivateProfileNotFollowedException,
    ProfileNotExistsException,
    TooManyRequestsException,
)

from analisis_bigfive import (  # noqa: F401
    MAX_CARACTERES_POR_PUBLICACION,
    MIN_PUBLICACIONES_CON_TEXTO,
    PROMPT_SISTEMA,
    RASGOS,
    anonimizar,
    api_key_configurada,
    llamar_gemini,
    modelo_configurado,
    parsear_json,
)

load_dotenv()  # carga las variables del archivo .env

# `os.environ.get(...) or "datos"` cubre el caso de CARPETA_DATOS= (vacío por error).
CARPETA_DATOS = Path(os.environ.get("CARPETA_DATOS") or "datos")

Progreso = Callable[[float, str], None]

# Fuente única para que las dos interfaces (Flask y Streamlit) coincidan.
ETIQUETAS_RASGOS = {
    "apertura": "Apertura a la experiencia",
    "responsabilidad": "Responsabilidad",
    "extraversion": "Extraversión",
    "amabilidad": "Amabilidad",
    "neuroticismo": "Neuroticismo",
}


# ------------------------------- Configuración -------------------------------
@dataclass
class Opciones:
    limite_publicaciones: int = 10
    fotos: bool = True
    videos: bool = True          # incluye reels (Instagram los trata como video)
    comentarios: bool = False
    limite_comentarios: int = 20  # por publicación
    pausa_min: float = 2.0
    pausa_max: float = 5.0

    def normalizar(self) -> "Opciones":
        self.limite_publicaciones = max(1, min(int(self.limite_publicaciones), 50))
        self.limite_comentarios = max(1, min(int(self.limite_comentarios), 100))
        return self


def validar_usuario(usuario: str) -> str:
    """Valida el formato de un usuario de Instagram (también evita rutas peligrosas)."""
    usuario = (usuario or "").strip().lstrip("@")
    if not re.fullmatch(r"[A-Za-z0-9._]{1,30}", usuario):
        raise ValueError("Nombre de usuario no válido (solo letras, números, punto y guion bajo).")
    return usuario


def seudonimo(usuario: str) -> str:
    """Identificador irreversible para no guardar los usuarios de quienes comentan."""
    sal = os.environ.get("PSEUDONIMO_SALT", "practica-academica")
    return "usuario_" + hashlib.sha256((sal + usuario).encode()).hexdigest()[:8]


def iso_utc(fecha: Optional[datetime]) -> Optional[str]:
    if fecha is None:
        return None
    if fecha.tzinfo is None:
        fecha = fecha.replace(tzinfo=timezone.utc)
    return fecha.isoformat()


# ---------------------------------- Sesión ----------------------------------
def ruta_sesion(cuenta: str) -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", "")) / "Instaloader"
    else:
        base = Path.home() / ".config" / "instaloader"
    return base / f"session-{cuenta}"


def _sesion_existe_instaloader(cuenta: str) -> bool:
    return bool(cuenta) and ruta_sesion(cuenta).exists()


def cargar_sesion(loader: instaloader.Instaloader, cuenta: str) -> str:
    if not cuenta:
        raise RuntimeError("Falta la cuenta alternativa (variable IG_CUENTA o campo de la barra lateral).")
    try:
        loader.load_session_from_file(cuenta)
    except FileNotFoundError:
        raise RuntimeError(
            f"No hay sesión guardada para '{cuenta}'. Usa el botón "
            "'Importar sesión desde Brave' de la barra lateral."
        )
    usuario = loader.test_login()
    if usuario is None:
        raise RuntimeError("La sesión no es válida o expiró. Vuelve a importarla desde Brave.")
    return usuario


def _importar_sesion_instaloader(cuenta: str) -> str:
    """Convierte las cookies de Brave en una sesión de Instaloader (Brave debe estar cerrado)."""
    import browser_cookie3

    if not cuenta:
        raise RuntimeError("Indica primero la cuenta alternativa.")

    cookies = browser_cookie3.brave(domain_name="instagram.com")
    datos = {c.name: c.value for c in cookies}
    if "sessionid" not in datos:
        raise RuntimeError(
            "No se encontró la cookie 'sessionid'. Inicia sesión en instagram.com "
            "desde Brave y cierra el navegador antes de importar."
        )

    loader = instaloader.Instaloader(quiet=True)
    loader.load_session(cuenta, datos)
    usuario = loader.test_login()
    if usuario is None:
        raise RuntimeError("Las cookies importadas no iniciaron sesión (¿expiradas?).")

    loader.context.username = usuario
    loader.save_session_to_file()
    return usuario


# --------------------------------- Extracción ---------------------------------
def crear_loader(carpeta: Path, opciones: Opciones) -> instaloader.Instaloader:
    return instaloader.Instaloader(
        dirname_pattern=str(carpeta),
        download_pictures=opciones.fotos,
        download_videos=opciones.videos,
        download_video_thumbnails=False,
        download_geotags=False,
        download_comments=False,      # los comentarios se piden aparte y se seudonimizan
        save_metadata=False,
        compress_json=False,
        post_metadata_txt_pattern="",  # no crear .txt con la descripción
        quiet=True,
    )


def perfil_a_dict(perfil: instaloader.Profile) -> dict:
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


def _archivos(carpeta: Path, base: str, extension: str) -> list[str]:
    escapado = glob.escape(str(carpeta / base))
    encontrados = glob.glob(f"{escapado}.{extension}") + glob.glob(f"{escapado}_[0-9]*.{extension}")
    return sorted(os.path.basename(r) for r in set(encontrados))


def buscar_archivos(carpeta: Path, post) -> dict:
    """Instaloader nombra los archivos con la fecha UTC del post (+ _1, _2... en carruseles)."""
    base = post.date_utc.strftime("%Y-%m-%d_%H-%M-%S_UTC")
    return {
        "archivos_imagen": _archivos(carpeta, base, "jpg"),
        "archivos_video": _archivos(carpeta, base, "mp4"),
    }


def publicacion_a_dict(numero: int, post) -> dict:
    likes = post.likes  # puede ser -1 si el autor oculta los likes
    comentarios = post.comments
    if post.is_video:
        tipo = "video/reel"
    elif post.typename == "GraphSidecar":
        tipo = "carrusel"
    else:
        tipo = "foto"

    return {
        "numero_post": numero,
        "id_post": str(post.mediaid),
        "shortcode": post.shortcode,
        "url": f"https://www.instagram.com/p/{post.shortcode}/",
        "tipo": tipo,
        "cantidad_medios": post.mediacount,
        "descripcion": post.caption or "",
        "hashtags": list(post.caption_hashtags),
        "cantidad_menciones": len(post.caption_mentions),
        "likes": likes,
        "comentarios": comentarios,
        "interacciones": max(likes, 0) + comentarios,
        "vistas_video": post.video_view_count if post.is_video else None,
        "fecha_publicacion": iso_utc(post.date_utc),
        "comentarios_lista": [],
        "archivos_imagen": [],
        "archivos_video": [],
    }


def obtener_comentarios(post, autor: str, limite: int) -> list[dict]:
    """Devuelve los comentarios con los usuarios seudonimizados y el texto anonimizado."""
    resultado = []
    try:
        for c in islice(post.get_comments(), limite):
            propietario = getattr(getattr(c, "owner", None), "username", "") or ""
            es_autor = propietario.lower() == autor.lower()
            resultado.append({
                "autor": "AUTOR_DEL_PERFIL" if es_autor else seudonimo(propietario),
                "es_autor_del_perfil": es_autor,
                "texto": anonimizar(c.text or "", autor),
                "fecha": iso_utc(getattr(c, "created_at_utc", None)),
                "likes": getattr(c, "likes_count", 0),
            })
    except (TooManyRequestsException, ConnectionException):
        raise  # se maneja arriba: hay que detener la extracción
    except InstaloaderException:
        pass   # un fallo puntual de comentarios no debe tumbar todo
    return resultado


def calcular_metricas(perfil: dict, publicaciones: list[dict]) -> dict:
    """Métricas agregadas de comportamiento (no contienen texto de terceros)."""
    n = len(publicaciones)
    if n == 0:
        return {}

    def media(valores):
        valores = [v for v in valores if v is not None]
        return round(sum(valores) / len(valores), 2) if valores else None

    fechas = sorted(
        datetime.fromisoformat(p["fecha_publicacion"]) for p in publicaciones if p.get("fecha_publicacion")
    )
    dias = (fechas[-1] - fechas[0]).days if len(fechas) >= 2 else 0
    por_semana = round(n / max(dias / 7, 1), 2) if dias else None

    tipos = [p["tipo"] for p in publicaciones]
    likes_visibles = [p["likes"] for p in publicaciones if p["likes"] >= 0]
    seguidores = perfil.get("seguidores") or 0
    interaccion_media = media([p["interacciones"] for p in publicaciones])

    metricas = {
        "publicaciones_analizadas": n,
        "dias_cubiertos": dias,
        "publicaciones_por_semana": por_semana,
        "porcentaje_fotos": round(100 * tipos.count("foto") / n, 1),
        "porcentaje_carruseles": round(100 * tipos.count("carrusel") / n, 1),
        "porcentaje_videos_reels": round(100 * tipos.count("video/reel") / n, 1),
        "porcentaje_con_descripcion": round(100 * sum(1 for p in publicaciones if p["descripcion"].strip()) / n, 1),
        "longitud_media_descripcion": media([len(p["descripcion"]) for p in publicaciones]),
        "hashtags_por_publicacion": media([len(p["hashtags"]) for p in publicaciones]),
        "menciones_por_publicacion": media([p["cantidad_menciones"] for p in publicaciones]),
        "likes_medios": media(likes_visibles),
        "comentarios_medios": media([p["comentarios"] for p in publicaciones]),
        "tasa_interaccion_porcentaje": (
            round(100 * interaccion_media / seguidores, 2) if seguidores and interaccion_media is not None else None
        ),
    }

    con_comentarios = [p for p in publicaciones if p.get("comentarios_lista")]
    if con_comentarios:
        respuestas = sum(1 for p in con_comentarios for c in p["comentarios_lista"] if c["es_autor_del_perfil"])
        metricas["comentarios_analizados"] = sum(len(p["comentarios_lista"]) for p in con_comentarios)
        metricas["respuestas_del_autor_en_comentarios"] = respuestas
        metricas["porcentaje_publicaciones_donde_el_autor_responde"] = round(
            100 * sum(1 for p in con_comentarios if any(c["es_autor_del_perfil"] for c in p["comentarios_lista"]))
            / len(con_comentarios), 1
        )
    return metricas


def guardar_datos(carpeta: Path, datos: dict) -> None:
    carpeta.mkdir(parents=True, exist_ok=True)
    with open(carpeta / "datos.json", "w", encoding="utf-8") as f:
        json.dump(datos, f, ensure_ascii=False, indent=2)


def extraer(loader, perfil, opciones: Opciones, carpeta: Path, progreso: Progreso) -> dict:
    """Recorre las publicaciones y guarda SIEMPRE lo obtenido, aunque Instagram corte a mitad."""
    opciones = opciones.normalizar()
    datos = {
        "perfil": perfil_a_dict(perfil),
        "opciones": asdict(opciones),
        "fecha_extraccion": iso_utc(datetime.now(timezone.utc)),
        "estado": "en_curso",
        "avisos": [],
        "publicaciones": [],
    }
    total = opciones.limite_publicaciones
    descargar_medios = opciones.fotos or opciones.videos

    try:
        for numero, post in enumerate(islice(perfil.get_posts(), total), start=1):
            progreso((numero - 1) / total, f"Publicación {numero} de {total}...")
            try:
                pub = publicacion_a_dict(numero, post)

                if descargar_medios:
                    loader.download_post(post, target=perfil.username)
                    pub.update(buscar_archivos(carpeta, post))

                if opciones.comentarios and pub["comentarios"] > 0:
                    pub["comentarios_lista"] = obtener_comentarios(
                        post, perfil.username, opciones.limite_comentarios
                    )

                datos["publicaciones"].append(pub)
            except (TooManyRequestsException, ConnectionException):
                raise
            except InstaloaderException as error:
                datos["avisos"].append(f"Se omitió la publicación {numero}: {error}")

            time.sleep(random.uniform(opciones.pausa_min, opciones.pausa_max))

        datos["estado"] = "completo"

    except AbortDownloadException as error:
        datos["estado"] = "incompleto"
        datos["avisos"].append(
            f"Instagram abortó la descarga ({error}). La cuenta parece restringida "
            "temporalmente: no reintentes hoy."
        )
    except (TooManyRequestsException, ConnectionException) as error:
        datos["estado"] = "incompleto"
        datos["avisos"].append(f"Instagram limitó las peticiones ({error}). Espera varias horas.")
    finally:
        datos["metricas"] = calcular_metricas(datos["perfil"], datos["publicaciones"])
        guardar_datos(carpeta, datos)

    progreso(1.0, "Extracción finalizada")
    return datos


def _ejecutar_extraccion_instaloader(usuario: str, cuenta: str, opciones: Opciones, progreso: Progreso) -> dict:
    usuario = validar_usuario(usuario)
    opciones = opciones.normalizar()
    carpeta = CARPETA_DATOS / usuario
    carpeta.mkdir(parents=True, exist_ok=True)

    loader = crear_loader(carpeta, opciones)
    cargar_sesion(loader, cuenta)

    try:
        perfil = instaloader.Profile.from_username(loader.context, usuario)
        if perfil.is_private and not perfil.followed_by_viewer:
            raise RuntimeError(f"'{usuario}' es privado y la cuenta alternativa no lo sigue.")
    except ProfileNotExistsException:
        raise RuntimeError(f"El perfil '{usuario}' no existe.")
    except LoginRequiredException:
        raise RuntimeError("Instagram exige iniciar sesión. Reimporta la sesión.")
    except PrivateProfileNotFollowedException:
        raise RuntimeError(f"'{usuario}' es privado y la cuenta alternativa no lo sigue.")
    except (TooManyRequestsException, ConnectionException) as error:
        raise RuntimeError(f"Instagram limitó o rechazó la conexión: {error}")
    except AbortDownloadException as error:
        raise RuntimeError(f"Instagram abortó la consulta del perfil: {error}")

    return extraer(loader, perfil, opciones, carpeta, progreso)


# ------------------------------- Selección de motor -------------------------------
def motor_actual() -> str:
    """Motor de extracción elegido en el .env (MOTOR=playwright o instaloader)."""
    motor = os.environ.get("MOTOR", "playwright").strip().lower()
    return motor if motor in ("playwright", "instaloader") else "playwright"


def sesion_existe(cuenta: str) -> bool:
    if motor_actual() == "playwright":
        import motor_playwright
        return motor_playwright.sesion_existe(cuenta)
    return _sesion_existe_instaloader(cuenta)


def importar_sesion_brave(cuenta: str) -> str:
    if motor_actual() == "playwright":
        import motor_playwright
        return motor_playwright.importar_sesion_brave(cuenta)
    return _importar_sesion_instaloader(cuenta)


def ejecutar_extraccion(usuario: str, cuenta: str, opciones: Opciones, progreso: Progreso) -> dict:
    if motor_actual() == "playwright":
        import motor_playwright
        return motor_playwright.ejecutar_extraccion(usuario, cuenta, opciones, progreso)
    return _ejecutar_extraccion_instaloader(usuario, cuenta, opciones, progreso)


# ------------------------------ Datos guardados ------------------------------
def carpeta_usuario(usuario: str) -> Path:
    return CARPETA_DATOS / validar_usuario(usuario)


def listar_usuarios_guardados() -> list[str]:
    """Nombres de carpeta válidos como usuario (evita que un 'datos/foo bar/' rompa la UI)."""
    if not CARPETA_DATOS.exists():
        return []
    return sorted(
        p.parent.name
        for p in CARPETA_DATOS.glob("*/datos.json")
        if re.fullmatch(r"[A-Za-z0-9._]{1,30}", p.parent.name)
    )


def cargar_datos_guardados(usuario: str) -> Optional[dict]:
    ruta = carpeta_usuario(usuario) / "datos.json"
    if not ruta.exists():
        return None
    with open(ruta, "r", encoding="utf-8") as f:
        return json.load(f)


def cargar_analisis_guardado(usuario: str) -> Optional[dict]:
    ruta = carpeta_usuario(usuario) / "analisis_bigfive.json"
    if not ruta.exists():
        return None
    with open(ruta, "r", encoding="utf-8") as f:
        return json.load(f)


# -------------------------------- Análisis Big Five --------------------------------
PROMPT_APP = (
    PROMPT_SISTEMA
    + """

Además de los textos recibirás métricas agregadas de comportamiento (frecuencia de
publicación, tipo de contenido, uso de hashtags y menciones, interacción recibida y,
si existen, participación del autor en los comentarios). Úsalas solo como señales de
apoyo e indica en la evidencia qué tema o métrica motiva cada puntuación.
La interacción que recibe una cuenta depende de su audiencia, no de la personalidad:
no la uses como evidencia directa de ningún rasgo.
No recibes los comentarios de otras personas ni debes pedirlos."""
)


def _llamar_modelo(sistema: str, mensaje: str) -> str:
    return llamar_gemini(sistema, mensaje)


def analizar_bigfive(usuario: str) -> dict:
    """Analiza los datos YA extraídos; no hace ninguna petición a Instagram."""
    datos = cargar_datos_guardados(usuario)
    if not datos:
        raise RuntimeError("No hay datos extraídos para este usuario. Extrae primero.")

    autor = datos["perfil"]["usuario"]
    textos = []
    for pub in datos["publicaciones"]:
        limpio = anonimizar(pub.get("descripcion") or "", autor)
        if len(limpio) >= 20:
            textos.append(limpio[:MAX_CARACTERES_POR_PUBLICACION])

    if len(textos) < MIN_PUBLICACIONES_CON_TEXTO:
        raise ValueError(
            f"Solo hay {len(textos)} publicaciones con texto suficiente; se necesitan al "
            f"menos {MIN_PUBLICACIONES_CON_TEXTO}. Con tan poco texto el análisis no sería significativo."
        )

    numeradas = "\n\n".join(f"[{i}] {t}" for i, t in enumerate(textos, start=1))
    mensaje = (
        f"Descripciones de {len(textos)} publicaciones (anonimizadas):\n\n{numeradas}\n\n"
        f"Métricas agregadas de comportamiento:\n"
        f"{json.dumps(datos.get('metricas', {}), ensure_ascii=False, indent=2)}\n\n"
        "Haz la estimación Big Five siguiendo el formato indicado."
    )

    resultado = parsear_json(_llamar_modelo(PROMPT_APP, mensaje))

    salida = {
        "aviso": (
            "Estimación exploratoria generada por un modelo de lenguaje a partir de textos y "
            "métricas de una red social. No es una evaluación psicológica."
        ),
        "modelo": modelo_configurado(),
        "publicaciones_analizadas": len(textos),
        "fecha_analisis": iso_utc(datetime.now(timezone.utc)),
        "resultado": resultado,
    }
    ruta = carpeta_usuario(usuario) / "analisis_bigfive.json"
    with open(ruta, "w", encoding="utf-8") as f:
        json.dump(salida, f, ensure_ascii=False, indent=2)
    return salida