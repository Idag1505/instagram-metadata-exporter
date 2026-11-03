
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
    llamar_gemini_multimodal,
    modelo_configurado,
    parsear_json,
)

load_dotenv()  # carga las variables del archivo .env

# `os.environ.get(...) or "datos"` cubre el caso de CARPETA_DATOS= (vacío por error).
CARPETA_DATOS = Path(os.environ.get("CARPETA_DATOS") or "datos")

Progreso = Callable[[float, str], None]

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
    videos: bool = True
    comentarios: bool = False
    limite_comentarios: int = 20
    seguidores: bool = False        # extraer lista de seguidores
    seguidos: bool = False          # extraer lista de seguidos
    limite_usuarios: int = 0        # 0 = lista completa
    stats_redes: bool = False
    limite_stats: int = 0           # 0 = todos; si es menor que la lista, muestra aleatoria
    metodo_stats: str = "api"         # máx. usuarios a consultar sus stats (aunque la lista sea mayor)
    pausa_min: float = 2.0
    pausa_max: float = 5.0

    def normalizar(self) -> "Opciones":
        self.limite_publicaciones = max(1, min(int(self.limite_publicaciones), 50))
        self.limite_comentarios = max(1, min(int(self.limite_comentarios), 100))
        self.limite_usuarios = max(0, min(int(self.limite_usuarios), 100_000))
        self.limite_stats = max(0, min(int(self.limite_stats), 100_000))
        if self.metodo_stats not in ("api", "hover"):
            self.metodo_stats = "api"
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


# --------------------------------- Extracción (Instaloader) ---------------------------------
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


# ------------------------------- Emojis -------------------------------
_RANGOS_EMOJI = (
    (0x1F300, 0x1F5FF),  # símbolos y pictogramas
    (0x1F600, 0x1F64F),  # emoticonos
    (0x1F680, 0x1F6FF),  # transporte
    (0x1F700, 0x1F77F),  # alquimia
    (0x1F900, 0x1F9FF),  # símbolos suplementarios
    (0x1FA00, 0x1FA6F),  # símbolos extendidos
    (0x2600, 0x26FF),    # misceláneos (☀, ☕...)
    (0x2700, 0x27BF),    # dingbats (✂, ✈...)
    (0x1F1E6, 0x1F1FF),  # banderas (regional indicators)
)


def _es_emoji(caracter: str) -> bool:
    punto = ord(caracter)
    return any(inicio <= punto <= fin for inicio, fin in _RANGOS_EMOJI)


def extraer_emojis(textos: list[str]) -> dict:
    """Cuenta emojis y detecta patrones simples de uso."""
    from collections import Counter
    contador = Counter()
    total_por_texto = []

    for texto in textos:
        en_texto = [c for c in texto if _es_emoji(c)]
        contador.update(en_texto)
        total_por_texto.append(len(en_texto))

    if not contador:
        return {"total": 0, "por_publicacion": 0, "unico": 0, "top": [], "densidad": "nula"}

    media = round(sum(total_por_texto) / max(len(total_por_texto), 1), 2)
    return {
        "total": sum(contador.values()),
        "por_publicacion": media,
        "unico": len(contador),
        "top": [{"emoji": e, "veces": n} for e, n in contador.most_common(10)],
        "densidad": "alta" if media >= 3 else ("media" if media >= 1 else "baja"),
    }
import math
from collections import Counter


def primer_digito(valor) -> Optional[int]:
    """300 -> 3, 4500 -> 4. None si es None, 0, negativo o inválido."""
    if valor is None or isinstance(valor, bool):
        return None
    try:
        v = int(valor)
    except (TypeError, ValueError):
        return None
    return int(str(v)[0]) if v >= 1 else None


def _p_chi2_8gl(x: float) -> float:
    """P(X >= x) para chi-cuadrado con 8 gl (forma cerrada, sin scipy)."""
    mitad = x / 2
    return math.exp(-mitad) * sum(mitad ** i / math.factorial(i) for i in range(4))


def analisis_benford_digitos(digitos: list) -> dict:
    """Compara la distribución de primeros dígitos con Benford (chi² + MAD)."""
    validos = [d for d in digitos if isinstance(d, int) and 1 <= d <= 9]
    total = len(validos)
    if total == 0:
        return {"n": 0, "filas": []}

    conteo = Counter(validos)
    filas, chi, suma_abs = [], 0.0, 0.0
    for d in range(1, 10):
        p_esp = math.log10(1 + 1 / d)
        esp_n = total * p_esp
        obs_n = conteo.get(d, 0)
        contrib = (obs_n - esp_n) ** 2 / esp_n
        chi += contrib
        suma_abs += abs(obs_n / total - p_esp)
        filas.append({
            "digito": d,
            "observado_n": obs_n,
            "observado": round(obs_n / total, 4),
            "esperado_n": round(esp_n, 2),
            "esperado": round(p_esp, 4),
            "contribucion_chi2": round(contrib, 3),
        })

    p_valor = _p_chi2_8gl(chi)
    mad = suma_abs / 9
    if mad <= 0.006:
        conformidad = "conformidad alta"
    elif mad <= 0.012:
        conformidad = "conformidad aceptable"
    elif mad <= 0.015:
        conformidad = "conformidad marginal"
    else:
        conformidad = "no conforme"

    return {
        "n": total,
        "filas": filas,
        "chi_cuadrado": round(chi, 3),
        "gl": 8,
        "valor_critico_5pct": 15.507,
        "p_valor": round(p_valor, 6),
        "rechaza_benford": p_valor < 0.05,
        "mad": round(mad, 5),
        "conformidad_mad": conformidad,
        "advertencia": (
            "Con menos de ~110 valores el chi² no es fiable (frecuencias esperadas < 5)."
            if total < 110 else None
        ),
    }


def analisis_benford(valores: list) -> dict:
    return analisis_benford_digitos([primer_digito(v) for v in valores])
def veredicto_benford(a: dict, lista: str = "seguidores") -> dict:
    """Conclusión orientativa a partir de χ², p-valor y MAD."""
    if not a.get("n"):
        return {"nivel": "sin_datos", "titulo": "Sin datos para analizar", "notas": []}

    sujeto = (
        "cuentas falsas o infladas entre sus seguidores" if lista == "seguidores"
        else "un patrón de seguimiento automatizado (cuenta tipo bot o follow masivo)"
    )
    p, mad = a["p_valor"], a["mad"]
    if p < 0.01 and mad > 0.015:
        nivel, titulo = "alta", f"Sospecha alta de {sujeto}"
    elif p < 0.05:
        nivel, titulo = "moderada", f"Sospecha moderada de {sujeto}"
    else:
        nivel, titulo = "baja", "Sin indicios en este test: la distribución es compatible con Benford"

    notas = []
    if a["n"] < 110:
        notas.append("Muestra pequeña (n < 110): el resultado es solo orientativo.")
    orden = a.get("ordenes_magnitud")
    if orden is not None and orden < 2:
        notas.append(
            f"Las cifras abarcan solo {orden} órdenes de magnitud: Benford pierde validez "
            "y las cuentas orgánicas también pueden desviarse (falsos positivos)."
        )
    notas.append(
        "Benford es un indicio, no una prueba de que haya bots. Contrástalo con otras señales "
        "(cuentas sin foto, sin publicaciones, nombres aleatorios)."
    )
    return {"nivel": nivel, "titulo": titulo, "notas": notas}


def analizar_lista(usuarios: list, metrica: str, lista: str = "seguidores") -> dict:
    """Benford sobre la cifra `metrica` ('seguidores' o 'seguidos') de cada usuario de la lista."""
    digitos, valores = [], []
    for u in usuarios:
        d = primer_digito(u.get(metrica))
        if d is None:
            d = u.get(f"digito_{metrica}")      # compatibilidad con datos antiguos
        digitos.append(d)
        valores.append(u.get(metrica))

    analisis = analisis_benford_digitos(digitos)
    positivos = [v for v in valores if isinstance(v, int) and v >= 1]
    analisis["ordenes_magnitud"] = (
        round(math.log10(max(positivos) / min(positivos)), 2) if len(positivos) > 1 else None
    )
    analisis["veredicto"] = veredicto_benford(analisis, lista)
    return analisis


def calcular_benford_redes(redes: Optional[dict]) -> dict:
    """4 combinaciones: lista (seguidores/seguidos) x cifra de cada usuario."""
    salida = {}
    for lista in ("seguidores", "seguidos"):
        usuarios = (redes or {}).get(lista) or []
        for metrica in ("seguidores", "seguidos"):
            salida[f"{lista}__{metrica}"] = analizar_lista(usuarios, metrica, lista)
    return salida


def grafico_benford_png(analisis: dict, titulo: str) -> bytes:
    """Barras azules = real, barras rojas (contorno) + línea = esperado por Benford."""
    import io
    from matplotlib.figure import Figure

    filas = analisis["filas"]
    x = [f["digito"] for f in filas]
    esperado = [f["esperado"] * 100 for f in filas]
    observado = [f["observado"] * 100 for f in filas]

    fig = Figure(figsize=(8.5, 4.8), dpi=120)
    ax = fig.subplots()
    ax.bar(x, observado, color="#1f77b4", alpha=0.75, label="Real (observado)")
    ax.bar(x, esperado, fill=False, edgecolor="#d62728", linewidth=2.2, label="Esperado (Benford)")
    ax.plot(x, esperado, "o-", color="#d62728", linewidth=1.5)
    for xi, f, o, e in zip(x, filas, observado, esperado):
        ax.text(xi, max(o, e) + 0.8, str(f["observado_n"]), ha="center", fontsize=8)
    ax.set_xticks(x)
    ax.set_xlabel("Primer dígito")
    ax.set_ylabel("Frecuencia (%)")
    ax.set_ylim(0, max(observado + esperado) + 5)
    ax.set_title(
        f"{titulo}\nn={analisis['n']}  χ²={analisis['chi_cuadrado']} (8 gl)  "
        f"p={analisis['p_valor']}  MAD={analisis['mad']}",
        fontsize=10,
    )
    ax.grid(axis="y", alpha=0.3)
    ax.legend()
    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    return buf.getvalue()


def exportar_excel(usuario: str, lista: str, metrica: str, usuarios: list,
                   analisis: dict, ruta: Path) -> Path:
    """Excel con Resumen, Datos y Conteo (con gráfica nativa azul/roja)."""
    from openpyxl import Workbook
    from openpyxl.chart import BarChart, Reference
    from openpyxl.styles import Font

    negrita = Font(bold=True)
    wb = Workbook()

    # ---- Resumen ----
    wr = wb.active
    wr.title = "Resumen"
    v = analisis.get("veredicto") or {}
    filas = [
        ("Perfil analizado", f"@{usuario}"),
        ("Lista", "Seguidores" if lista == "seguidores" else "Seguidos"),
        ("Cifra analizada de cada usuario",
         "Sus seguidores (followers)" if metrica == "seguidores" else "Sus seguidos (following)"),
        ("Usuarios en la lista", len(usuarios)),
        ("Usuarios con dato (n)", analisis["n"]),
        ("χ² (8 gl)", analisis["chi_cuadrado"]),
        ("Valor crítico al 5 %", analisis["valor_critico_5pct"]),
        ("p-valor", analisis["p_valor"]),
        ("MAD", analisis["mad"]),
        ("Conformidad (MAD)", analisis["conformidad_mad"]),
        ("Conclusión", v.get("titulo", "")),
    ] + [("Nota", nota) for nota in v.get("notas", [])]
    for fila in filas:
        wr.append(fila)
    for celda in wr["A"]:
        celda.font = negrita
    wr.column_dimensions["A"].width = 34
    wr.column_dimensions["B"].width = 100

    # ---- Datos ----
    wd = wb.create_sheet("Datos")
    wd.append(["usuario", "followers", "following",
               f"primer dígito ({'followers' if metrica == 'seguidores' else 'following'})"])
    for u in usuarios:
        d = primer_digito(u.get(metrica))
        if d is None:
            d = u.get(f"digito_{metrica}")
        wd.append([u.get("usuario"), u.get("seguidores"), u.get("seguidos"), d])
    for celda in wd[1]:
        celda.font = negrita
    for col, ancho in zip("ABCD", (30, 14, 14, 26)):
        wd.column_dimensions[col].width = ancho

    # ---- Conteo + gráfica ----
    wc = wb.create_sheet("Conteo")
    wc.append(["Dígito", "Conteo real", "% real (observado)", "% esperado (Benford)",
               "Conteo esperado", "Diferencia (real − esperado)", "Aporte al χ²"])
    for f in analisis["filas"]:
        wc.append([f["digito"], f["observado_n"], f["observado"], f["esperado"],
                   f["esperado_n"], round(f["observado_n"] - f["esperado_n"], 2),
                   f["contribucion_chi2"]])
    wc.append(["Total", analisis["n"], None, None, None, None, analisis["chi_cuadrado"]])
    for celda in wc[1]:
        celda.font = negrita
    for fila in wc.iter_rows(min_row=2, max_row=10, min_col=3, max_col=4):
        for celda in fila:
            celda.number_format = "0.0%"
    for col in "ABCDEFG":
        wc.column_dimensions[col].width = 22

    graf = BarChart()
    graf.type = "col"
    graf.overlap = 100
    graf.gapWidth = 60
    graf.title = f"Ley de Benford: real vs esperado (χ² = {analisis['chi_cuadrado']})"
    graf.x_axis.title = "Primer dígito"
    graf.y_axis.title = "Frecuencia"
    graf.x_axis.delete = False
    graf.y_axis.delete = False
    graf.add_data(Reference(wc, min_col=3, min_row=1, max_row=10), titles_from_data=True)
    graf.add_data(Reference(wc, min_col=4, min_row=1, max_row=10), titles_from_data=True)
    graf.set_categories(Reference(wc, min_col=1, min_row=2, max_row=10))
    real, esperado = graf.series
    real.graphicalProperties.solidFill = "1F77B4"                 # azul
    esperado.graphicalProperties.noFill = True                    # rojo: solo contorno
    esperado.graphicalProperties.line.solidFill = "D62728"
    esperado.graphicalProperties.line.width = 28575
    graf.height, graf.width = 9, 17
    wc.add_chart(graf, "I2")

    ruta.parent.mkdir(parents=True, exist_ok=True)
    wb.save(ruta)
    return ruta


# ------------------------------- Persistencia -------------------------------
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
    except InstaloaderException as error:      # red de seguridad
        raise RuntimeError(f"Error de Instaloader al consultar el perfil: {error}")

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

Contexto de esta aplicación:
- Los textos y comentarios ya están anonimizados (sin @menciones, URLs, emails, teléfonos ni el nombre del autor).
- Los usuarios que comentan aparecen como seudónimos (usuario_XXXXXXXX) o como AUTOR_DEL_PERFIL si es el propio autor.
- Las métricas son agregados calculados sobre el corpus extraído; no reflejan el perfil completo.
- Las imágenes (si se aportan) son fotos públicas del perfil, enviadas como adjunto al modelo. No describas detalles identificativos (caras reconocibles, matrículas, direcciones). Centra la evidencia visual en composición, ambiente, actividades y estética.
- Si no se adjuntan imágenes, no las menciones ni las infieras: trabaja solo con las fuentes que sí estén presentes.
- Si el corpus es escaso (menos de 8 publicaciones con texto sustantivo), reduce la confianza conforme a las reglas metodológicas."""
)


def _llamar_modelo(sistema: str, mensaje: str) -> str:
    return llamar_gemini(sistema, mensaje)


def _cargar_imagenes(datos: dict, carpeta: Path, limite: int) -> list[tuple[str, bytes]]:
    """
    Devuelve [(mime_type, bytes), ...] de hasta `limite` imágenes del corpus.
    Selecciona primero las de los posts con más interacciones (proxy de contenido
    representativo) y descarta las que superen 4 MB.
    """
    candidatas = []
    for pub in datos["publicaciones"]:
        for nombre in pub.get("archivos_imagen", []):
            ruta = carpeta / nombre
            if not ruta.exists():
                continue
            try:
                if ruta.stat().st_size >= 4 * 1024 * 1024:  # > 4 MB → se omite
                    continue
            except OSError:
                continue
            candidatas.append((pub.get("interacciones", 0), ruta))

    candidatas.sort(key=lambda par: par[0], reverse=True)

    seleccionadas = []
    for _, ruta in candidatas[:limite]:
        try:
            seleccionadas.append(("image/jpeg", ruta.read_bytes()))
        except OSError:
            continue
    return seleccionadas


def _aplicar_reglas_metodologicas(resultado: dict, imagenes_activas: bool = False) -> dict:
    """
    Fuerza desde el código reglas que el prompt pide pero el modelo podría ignorar:
      - confianza "baja" → puntuación forzada a 3
      - puntuación recortada a 1-5
      - confianza normalizada a baja/media/alta
      - fuentes_usadas filtradas a las realmente permitidas
      - `imagenes_activas` controla si 'imagenes' es una fuente válida
    """
    rasgos = resultado.get("rasgos") or {}
    for info in rasgos.values():
        if not isinstance(info, dict):
            continue

        confianza = str(info.get("confianza", "")).lower().strip()
        if confianza not in ("baja", "media", "alta"):
            confianza = "baja"
        info["confianza"] = confianza

        try:
            puntuacion = int(info.get("puntuacion", 3))
        except (TypeError, ValueError):
            puntuacion = 3
        puntuacion = max(1, min(5, puntuacion))

        if confianza == "baja":
            puntuacion = 3
            info["evidencia"] = (
                (info.get("evidencia") or "").strip()
                + " [Puntuación fijada a 3 por confianza baja.]"
            ).strip()

        info["puntuacion"] = puntuacion

    permitidas = {"texto", "emojis", "metricas", "comentarios", "perfil"}
    if imagenes_activas:
        permitidas.add("imagenes")

    fuentes = resultado.get("fuentes_usadas")
    if not isinstance(fuentes, list):
        resultado["fuentes_usadas"] = ["texto"]
    else:
        resultado["fuentes_usadas"] = [f for f in fuentes if f in permitidas] or ["texto"]

    return resultado


def analizar_bigfive(usuario: str) -> dict:
    """Analiza los datos YA extraídos; no hace ninguna petición a Instagram."""
    datos = cargar_datos_guardados(usuario)
    if not datos:
        raise RuntimeError("No hay datos extraídos para este usuario. Extrae primero.")

    autor = datos["perfil"]["usuario"]

    # --- 1) Textos anonimizados ---
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

    # --- 2) Emojis ---
    emojis = extraer_emojis(textos)

    # --- 3) Comentarios seudonimizados (máx 5 por post, 40 total) ---
    comentarios_anon = []
    for pub in datos["publicaciones"]:
        for c in (pub.get("comentarios_lista") or [])[:5]:
            comentarios_anon.append({
                "autor": c["autor"],
                "texto": c["texto"][:200],
                "es_autor_del_perfil": c["es_autor_del_perfil"],
            })
    comentarios_anon = comentarios_anon[:40]

    # --- 4) Métricas ---
    metricas = datos.get("metricas", {})

    # --- 5) Imágenes (opcional) ---
    analizar_imagenes = os.environ.get("ANALIZAR_IMAGENES", "0") == "1"
    imagenes: list[tuple[str, bytes]] = []
    if analizar_imagenes:
        try:
            limite_img = int(os.environ.get("MAX_IMAGENES_ANALISIS", "5") or 5)
        except ValueError:
            limite_img = 5
        imagenes = _cargar_imagenes(datos, carpeta_usuario(usuario), limite_img)

    # --- 6) Payload ---
    numeradas = "\n\n".join(f"[{i}] {t}" for i, t in enumerate(textos, start=1))

    partes = [
        f"Descripciones de {len(textos)} publicaciones (anonimizadas):\n\n{numeradas}",
        f"\n\nEmojis detectados:\n{json.dumps(emojis, ensure_ascii=False, indent=2)}",
        f"\n\nMétricas agregadas de comportamiento:\n{json.dumps(metricas, ensure_ascii=False, indent=2)}",
    ]

    if comentarios_anon:
        partes.append(
            "\n\nComentarios seudonimizados (máx 40):\n"
            + json.dumps(comentarios_anon, ensure_ascii=False, indent=2)
        )

    partes.append(
        "\n\nPerfil:\n"
        + json.dumps({
            "biografia": anonimizar(datos["perfil"].get("biografia") or "", autor),
            "seguidores": datos["perfil"].get("seguidores"),
            "seguidos": datos["perfil"].get("seguidos"),
            "total_publicaciones": datos["perfil"].get("total_publicaciones"),
            "es_verificado": datos["perfil"].get("es_verificado"),
        }, ensure_ascii=False, indent=2)
    )

    if imagenes:
        partes.append(
            f"\n\nSe adjuntan {len(imagenes)} imágenes representativas del perfil "
            "(seleccionadas por número de interacciones)."
        )

    partes.append(
        "\n\nHaz la estimación Big Five siguiendo el formato y las reglas metodológicas indicadas."
    )

    mensaje = "".join(partes)

    # --- 7) Llamada (una u otra según imágenes) ---
    if imagenes:
        bruto = llamar_gemini_multimodal(PROMPT_APP, mensaje, imagenes)
    else:
        bruto = _llamar_modelo(PROMPT_APP, mensaje)

    resultado = _aplicar_reglas_metodologicas(
        parsear_json(bruto),
        imagenes_activas=bool(imagenes),
    )

    fuentes_enviadas = (
        ["texto", "emojis", "metricas"]
        + (["comentarios"] if comentarios_anon else [])
        + ["perfil"]
        + (["imagenes"] if imagenes else [])
    )

    salida = {
        "aviso": (
            "Estimación exploratoria generada por un modelo de lenguaje a partir de "
            "textos, emojis, métricas, comentarios seudonimizados"
            + (" e imágenes" if imagenes else "")
            + " de una red social. No es una evaluación psicológica."
        ),
        "modelo": modelo_configurado(),
        "publicaciones_analizadas": len(textos),
        "emojis_detectados": emojis["total"],
        "comentarios_incluidos": len(comentarios_anon),
        "imagenes_analizadas": len(imagenes),
        "fuentes_enviadas": fuentes_enviadas,
        "fecha_analisis": iso_utc(datetime.now(timezone.utc)),
        "resultado": resultado,
    }
    ruta = carpeta_usuario(usuario) / "analisis_bigfive.json"
    with open(ruta, "w", encoding="utf-8") as f:
        json.dump(salida, f, ensure_ascii=False, indent=2)
    return salida
