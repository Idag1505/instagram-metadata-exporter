import json
import os
import random
import re
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import nucleo as n

INSTAGRAM = "https://www.instagram.com"
DOMINIOS_CDN = (".cdninstagram.com", ".fbcdn.net")  # solo se descargan archivos de aquí

PATRON_META = re.compile(
    r"([\d.,]+\s*[KMBkmb]?)\s*(?:Followers|seguidores)\D+?"
    r"([\d.,]+\s*[KMBkmb]?)\s*(?:Following|seguidos)\D+?"
    r"([\d.,]+\s*[KMBkmb]?)\s*(?:Posts|publicaciones)",
    re.IGNORECASE,
)


# --------------------------------- Sesión ---------------------------------
def ruta_estado(cuenta: str) -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", "")) / "instagram-metadata-exporter"
    else:
        base = Path.home() / ".config" / "instagram-metadata-exporter"
    return base / f"playwright-{cuenta}.json"


def sesion_existe(cuenta: str) -> bool:
    try:
        return bool(cuenta) and ruta_estado(n.validar_usuario(cuenta)).exists()
    except ValueError:
        return False


def importar_sesion_brave(cuenta: str) -> str:
    """Convierte las cookies de Brave en un estado de sesión de Playwright (Brave debe estar cerrado)."""
    import browser_cookie3

    if not cuenta:
        raise RuntimeError("Indica primero la cuenta alternativa.")
    cuenta = n.validar_usuario(cuenta)

    cookies = list(browser_cookie3.brave(domain_name="instagram.com"))
    if not any(c.name == "sessionid" for c in cookies):
        raise RuntimeError(
            "No se encontró la cookie 'sessionid'. Inicia sesión en instagram.com "
            "desde Brave y cierra el navegador antes de importar."
        )

    estado = {
        "cookies": [
            {
                "name": c.name,
                "value": c.value,
                "domain": c.domain,
                "path": c.path or "/",
                "expires": float(c.expires) if c.expires else -1,
                "httpOnly": False,
                "secure": bool(c.secure),
                "sameSite": "Lax",
            }
            for c in cookies
        ],
        "origins": [],
    }

    ruta = ruta_estado(cuenta)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(json.dumps(estado), encoding="utf-8")
    try:
        os.chmod(ruta, 0o600)  # solo tu usuario puede leerla
    except OSError:
        pass
    return cuenta


# ------------------------- Lectura de los JSON de Instagram -------------------------
def recorrer(obj):
    """Genera todos los diccionarios anidados dentro de un JSON."""
    pila = [obj]
    while pila:
        actual = pila.pop()
        if isinstance(actual, dict):
            yield actual
            pila.extend(actual.values())
        elif isinstance(actual, list):
            pila.extend(actual)


def _conteo(d: dict, *claves: str) -> Optional[int]:
    for clave in claves:
        valor = d.get(clave)
        if isinstance(valor, dict) and isinstance(valor.get("count"), int):
            return valor["count"]
        if isinstance(valor, int) and not isinstance(valor, bool):
            return valor
    return None


def convertir_cifra(texto) -> Optional[int]:
    """'960' -> 960, '1,234' -> 1234, '12.5K' -> 12500, '1,2 M' -> 1200000."""
    limpio = str(texto).strip().replace(" ", "")
    coincidencia = re.fullmatch(r"([\d.,]+)([KkMmBb]?)", limpio)
    if not coincidencia:
        return None
    numero, sufijo = coincidencia.groups()
    if sufijo:
        factor = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}[sufijo.lower()]
        try:
            return int(round(float(numero.replace(",", ".")) * factor))
        except ValueError:
            return None
    digitos = re.sub(r"[.,]", "", numero)
    return int(digitos) if digitos else None


def _fecha(marca) -> Optional[str]:
    if isinstance(marca, (int, float)) and not isinstance(marca, bool):
        return datetime.fromtimestamp(marca, tz=timezone.utc).isoformat()
    return None


def parsear_perfil(payloads: list, meta_descripcion: Optional[str], usuario: str) -> dict:
    candidato = None
    for payload in payloads:
        for d in recorrer(payload):
            if str(d.get("username", "")).lower() == usuario.lower() and any(
                k in d for k in ("edge_followed_by", "follower_count", "biography")
            ):
                if candidato is None or len(d) > len(candidato):
                    candidato = d

    # Inicialización FUERA del bucle: si payloads está vacío, `candidato` sigue
    # siendo None pero `perfil` existe con valores por defecto. Sin esto, la
    # primera llamada desde extraer() (parsear_perfil([], None, usuario))
    # lanza UnboundLocalError.
    perfil = {
        "usuario": usuario,
        "nombre_completo": "",
        "biografia": "",
        "seguidores": None,
        "seguidos": None,
        "total_publicaciones": None,
        "url_externa": None,
        "es_privado": False,
        "es_verificado": False,
        "id_usuario": None,
    }

    if candidato:
        perfil.update({
            "usuario": candidato.get("username", usuario),
            "nombre_completo": candidato.get("full_name") or "",
            "biografia": candidato.get("biography") or "",
            "seguidores": _conteo(candidato, "edge_followed_by", "follower_count"),
            "seguidos": _conteo(candidato, "edge_follow", "following_count"),
            "total_publicaciones": _conteo(candidato, "edge_owner_to_timeline_media", "media_count"),
            "url_externa": candidato.get("external_url"),
            "es_privado": bool(candidato.get("is_private")),
            "es_verificado": bool(candidato.get("is_verified")),
            "id_usuario": str(
                candidato.get("id") or candidato.get("pk") or candidato.get("userid") or ""
            ) or None,
        })

    # Respaldo: la etiqueta <meta> de la página trae los tres conteos
    if meta_descripcion:
        m = PATRON_META.search(meta_descripcion)
        if m:
            for clave, texto in zip(("seguidores", "seguidos", "total_publicaciones"), m.groups()):
                if perfil[clave] is None:
                    perfil[clave] = convertir_cifra(texto)

    return perfil


def _es_nodo_post(d: dict) -> bool:
    """
    Un post REAL del timeline SIEMPRE tiene `taken_at` (Unix timestamp).
    Los hijos de carrusel tienen `code` + `image_versions2` pero NO fecha.

    Filtro:
      1. Debe tener shortcode/code.
      2. Debe tener `taken_at_timestamp` o `taken_at` (o, en formatos legacy,
         `edge_media_to_caption` no vacío).
    """
    tiene_codigo = isinstance(d.get("shortcode"), str) or isinstance(d.get("code"), str)
    if not tiene_codigo:
        return False

    if d.get("taken_at_timestamp") is not None or d.get("taken_at") is not None:
        return True

    caption_edges = (d.get("edge_media_to_caption") or {}).get("edges")
    if isinstance(caption_edges, list) and caption_edges:
        return True

    return False


def _descripcion(d: dict) -> str:
    caption = d.get("caption")
    if isinstance(caption, dict):
        return caption.get("text") or ""
    if isinstance(caption, str):
        return caption
    edges = (d.get("edge_media_to_caption") or {}).get("edges") or []
    return (edges[0].get("node", {}).get("text") or "") if edges else ""


def _url_imagen(d: dict) -> Optional[str]:
    if isinstance(d.get("display_url"), str):
        return d["display_url"]
    candidatos = (d.get("image_versions2") or {}).get("candidates") or []
    return candidatos[0].get("url") if candidatos and isinstance(candidatos[0], dict) else None


def _url_video(d: dict) -> Optional[str]:
    if isinstance(d.get("video_url"), str):
        return d["video_url"]
    versiones = d.get("video_versions") or []
    return versiones[0].get("url") if versiones and isinstance(versiones[0], dict) else None


def _propietario_post(d: dict) -> tuple:
    """
    Devuelve (username_del_dueño, id_numerico_del_dueño) de un nodo de post.
    Instagram usa varios formatos según la versión:
      - {"owner": {"username": "...", "id": "..."}}    (GraphQL moderno)
      - {"user":  {"username": "...", "pk": "..."}}    (API v1)
      - {"id": "<media_id>_<user_id>"}                 (formato legacy)
    """
    for clave in ("owner", "user"):
        obj = d.get(clave)
        if isinstance(obj, dict):
            username = obj.get("username")
            idnum = obj.get("pk") or obj.get("id")
            if username or idnum:
                return (username, str(idnum) if idnum else None)

    # Fallback: parsear el sufijo de "id" con formato "<media>_<user>"
    media_id = d.get("id") or d.get("pk")
    if isinstance(media_id, str) and "_" in media_id:
        partes = media_id.rsplit("_", 1)
        if len(partes) == 2 and partes[1].isdigit():
            return (None, partes[1])

    return (None, None)


def normalizar_nodo(d: dict) -> dict:
    """Convierte un nodo de publicación (formato antiguo o nuevo) al formato interno del proyecto."""
    shortcode = d.get("shortcode") if isinstance(d.get("shortcode"), str) else d.get("code")
    descripcion = _descripcion(d)

    hijos = [h.get("node", {}) for h in (d.get("edge_sidecar_to_children") or {}).get("edges", [])]
    hijos += [h for h in (d.get("carousel_media") or []) if isinstance(h, dict)]

    url_video = _url_video(d)
    es_video = bool(d.get("is_video")) or d.get("media_type") == 2 or bool(url_video)

    medios = []
    if hijos:
        for h in hijos:
            v, i = _url_video(h), _url_imagen(h)
            if v:
                medios.append(("video", v))
            elif i:
                medios.append(("imagen", i))
        tipo = "carrusel"
    elif es_video:
        if url_video:
            medios.append(("video", url_video))
        tipo = "video/reel"
    else:
        imagen = _url_imagen(d)
        if imagen:
            medios.append(("imagen", imagen))
        tipo = "foto"

    likes = _conteo(d, "like_count", "edge_media_preview_like", "edge_liked_by")
    if likes is None or d.get("like_and_view_counts_disabled"):
        likes = -1
    comentarios = _conteo(
        d, "comment_count", "edge_media_to_comment", "edge_media_preview_comment",
        "edge_media_to_parent_comment",
    ) or 0

    propietario_usuario, propietario_id = _propietario_post(d)

    return {
        "id_post": str(d.get("id") or d.get("pk") or ""),
        "shortcode": shortcode,
        "url": f"{INSTAGRAM}/p/{shortcode}/",
        "tipo": tipo,
        "cantidad_medios": max(len(hijos), 1),
        "descripcion": descripcion,
        "hashtags": re.findall(r"#(\w+)", descripcion),
        "cantidad_menciones": len(re.findall(r"@[\w.]+", descripcion)),
        "likes": likes,
        "comentarios": comentarios,
        "interacciones": max(likes, 0) + comentarios,
        "vistas_video": _conteo(d, "video_view_count", "play_count", "view_count", "video_play_count") if es_video else None,
        "fecha_publicacion": _fecha(d.get("taken_at_timestamp", d.get("taken_at"))),
        "comentarios_lista": [],
        "archivos_imagen": [],
        "archivos_video": [],
        "_medios": medios,
        "_propietario_usuario": propietario_usuario,
        "_propietario_id": propietario_id,
    }


def parsear_publicaciones(payloads: list, usuario=None, usuario_id=None) -> list[dict]:
    """
    Extrae posts del timeline del perfil, descartando:
      - hijos de carrusel (sin `taken_at`)
      - posts sugeridos / ads (con `taken_at` pero de OTRO dueño)
    """
    resultado = {}
    descartados_ajenos = 0

    for payload in payloads:
        for d in recorrer(payload):
            if not _es_nodo_post(d):
                continue

            pub = normalizar_nodo(d)

            if usuario or usuario_id:
                coincide = False
                if usuario and pub.get("_propietario_usuario"):
                    coincide = pub["_propietario_usuario"].lower() == usuario.lower()
                if not coincide and usuario_id and pub.get("_propietario_id"):
                    coincide = str(pub["_propietario_id"]) == str(usuario_id)
                if not coincide:
                    descartados_ajenos += 1
                    continue

            previa = resultado.get(pub["shortcode"])
            if previa is None or len(pub["_medios"]) > len(previa["_medios"]):
                resultado[pub["shortcode"]] = pub

    if os.environ.get("DEBUG") == "1" and descartados_ajenos:
        print(
            f"[parsear_publicaciones] descartados {descartados_ajenos} posts ajenos "
            f"(ads / sugeridos) | aceptados {len(resultado)} del perfil"
        )

    return list(resultado.values())


def parsear_comentarios(payloads: list, descripcion: str = "", autor: str = "") -> list[dict]:
    vistos, resultado = set(), []
    for payload in payloads:
        for d in recorrer(payload):
            usuario = d.get("user") or d.get("owner")
            nombre = usuario.get("username") if isinstance(usuario, dict) else None
            texto = d.get("text")
            if not (nombre and isinstance(texto, str) and ("created_at" in d or "created_at_utc" in d)):
                continue
            if texto == descripcion and nombre.lower() == autor.lower():
                continue  # es la descripción de la publicación, no un comentario
            clave = d.get("pk") or d.get("id") or (nombre, texto)
            if clave in vistos:
                continue
            vistos.add(clave)
            resultado.append({
                "usuario": nombre,
                "texto": texto,
                "fecha": _fecha(d.get("created_at", d.get("created_at_utc"))),
                "likes": _conteo(d, "comment_like_count", "like_count") or 0,
            })
    return resultado


# ----------------------------------- Navegador -----------------------------------
class SitioPlaywright:
    """Operaciones sobre Instagram con un navegador real controlado por Playwright."""

    def __init__(self, contexto):
        self.contexto = contexto
        self.pagina = contexto.new_page()
        self._respuestas = []
        self.pagina.on("response", self._registrar)

    # -- captura de respuestas JSON --
    def _registrar(self, respuesta):
        try:
            if respuesta.request.resource_type in ("xhr", "fetch") and (
                "graphql" in respuesta.url or "/api/v1/" in respuesta.url
            ):
                self._respuestas.append(respuesta)
        except Exception:  # noqa: BLE001
            pass

    def _vaciar(self) -> list:
        pendientes, self._respuestas = self._respuestas, []
        payloads = []
        for respuesta in pendientes:
            try:
                payloads.append(respuesta.json())
            except Exception:  # noqa: BLE001 - cuerpo no disponible o no es JSON
                continue
        return payloads

    def _scripts_json(self) -> list:
        """Instagram incrusta datos precargados en etiquetas <script type="application/json">."""
        try:
            textos = self.pagina.eval_on_selector_all(
                'script[type="application/json"]', "els => els.map(e => e.textContent)"
            )
        except Exception:  # noqa: BLE001
            return []
        resultado = []
        for texto in textos:
            if texto and len(texto) < 20_000_000:
                try:
                    resultado.append(json.loads(texto))
                except ValueError:
                    continue
        return resultado

    def _enlaces(self) -> list:
        try:
            return self.pagina.eval_on_selector_all(
                'a[href*="/p/"], a[href*="/reel/"]', "els => els.map(e => e.getAttribute('href'))"
            )
        except Exception:  # noqa: BLE001
            return []

    # -- comprobaciones --
    def _verificar_pagina(self, respuesta) -> None:
        url = self.pagina.url
        if "accounts/login" in url:
            raise RuntimeError(
                "Instagram redirigió al inicio de sesión: la sesión expiró o no es válida. "
                "Vuelve a importarla desde Brave."
            )
        if any(palabra in url for palabra in ("challenge", "checkpoint", "suspended")):
            raise RuntimeError(
                "Instagram pide una verificación de seguridad. Resuélvela tú mismo en Brave "
                "y espera varios días antes de volver a extraer."
            )
        estado = getattr(respuesta, "status", None)
        if estado == 404:
            raise RuntimeError("El perfil no existe.")
        if estado == 429:
            raise RuntimeError("Instagram respondió 429 (demasiadas peticiones). Espera al menos un día.")
        try:
            inicio = self.pagina.inner_text("body")[:2000].lower()
        except Exception:  # noqa: BLE001
            inicio = ""
        if "esta página no está disponible" in inicio or "isn't available" in inicio:
            raise RuntimeError("El perfil no existe o no está disponible para esta cuenta.")

    # -- interfaz usada por extraer() --
    def abrir_perfil(self, usuario: str) -> dict:
        try:
            respuesta = self.pagina.goto(f"{INSTAGRAM}/{usuario}/", wait_until="domcontentloaded", timeout=45_000)
            self.pagina.wait_for_timeout(random.uniform(2500, 4500))
        except Exception as error:  # noqa: BLE001
            raise RuntimeError(f"No se pudo abrir el perfil: {error}")
        self._verificar_pagina(respuesta)

        try:
            meta = self.pagina.eval_on_selector('meta[property="og:description"]', "e => e.content")
        except Exception:  # noqa: BLE001
            meta = None
        return {"payloads": self._scripts_json() + self._vaciar(), "meta": meta, "enlaces": self._enlaces()}

    def cargar_mas(self) -> dict:
        try:
            self.pagina.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            self.pagina.wait_for_timeout(random.uniform(2500, 4500))
        except Exception as error:  # noqa: BLE001
            raise RuntimeError(f"No se pudo desplazar la página: {error}")
        return {"payloads": self._vaciar(), "enlaces": self._enlaces()}

    def descargar(self, url: str, destino: Path) -> bool:
        host = urlparse(url).hostname or ""
        if not host.endswith(DOMINIOS_CDN):
            return False  # nunca se descarga desde dominios que no sean de Instagram
        try:
            respuesta = self.contexto.request.get(url, timeout=60_000)
            if not respuesta.ok:
                return False
            destino.write_bytes(respuesta.body())
        except Exception:  # noqa: BLE001 - un archivo que falla no debe detener la extracción
            return False
        return True

    def comentarios(self, shortcode: str, limite: int, descripcion: str, autor: str) -> list:
        self._vaciar()
        try:
            self.pagina.goto(f"{INSTAGRAM}/p/{shortcode}/", wait_until="domcontentloaded", timeout=45_000)
            self.pagina.wait_for_timeout(random.uniform(3000, 5000))
        except Exception as error:  # noqa: BLE001
            raise RuntimeError(f"No se pudo abrir la publicación: {error}")
        self._verificar_pagina(None)
        payloads = self._scripts_json() + self._vaciar()
        return parsear_comentarios(payloads, descripcion, autor)[:limite]

    def pausa(self, minimo: float, maximo: float) -> None:
        self.pagina.wait_for_timeout(random.uniform(minimo, maximo) * 1000)


# ----------------------------------- Extracción -----------------------------------
def _nombre_base(pub: dict) -> str:
    if pub.get("fecha_publicacion"):
        fecha = datetime.fromisoformat(pub["fecha_publicacion"]).astimezone(timezone.utc)
        return fecha.strftime("%Y-%m-%d_%H-%M-%S_UTC")
    return f"post_{pub['shortcode']}"


def _descargar_medios(sitio, pub: dict, medios: list, opciones, carpeta: Path, avisos: list) -> None:
    base = _nombre_base(pub)
    varios = len(medios) > 1
    for indice, (tipo, url) in enumerate(medios, start=1):
        if (tipo == "imagen" and not opciones.fotos) or (tipo == "video" and not opciones.videos):
            continue
        extension = "jpg" if tipo == "imagen" else "mp4"
        nombre = f"{base}_{indice}.{extension}" if varios else f"{base}.{extension}"
        if sitio.descargar(url, carpeta / nombre):
            (pub["archivos_imagen"] if tipo == "imagen" else pub["archivos_video"]).append(nombre)
        else:
            avisos.append(f"Publicación {pub['numero_post']}: no se pudo descargar un archivo ({extension}).")
        sitio.pausa(opciones.pausa_min, opciones.pausa_max)


def _publicaciones_desde_enlaces(enlaces: list) -> list[dict]:
    """Último recurso: solo se conocen las URL de las publicaciones, sin métricas."""
    resultado, vistos = [], set()
    for enlace in enlaces or []:
        m = re.search(r"/(p|reel)/([\w-]+)", enlace or "")
        if not m or m.group(2) in vistos:
            continue
        vistos.add(m.group(2))
        resultado.append({
            "id_post": "", "shortcode": m.group(2), "url": f"{INSTAGRAM}/p/{m.group(2)}/",
            "tipo": "video/reel" if m.group(1) == "reel" else "foto", "cantidad_medios": 1,
            "descripcion": "", "hashtags": [], "cantidad_menciones": 0, "likes": -1,
            "comentarios": 0, "interacciones": 0, "vistas_video": None, "fecha_publicacion": None,
            "comentarios_lista": [], "archivos_imagen": [], "archivos_video": [],
            "_medios": [],
            "_propietario_usuario": None,
            "_propietario_id": None,
        })
    return resultado


def extraer(sitio, usuario: str, opciones: n.Opciones, carpeta: Path, progreso) -> dict:
    opciones = opciones.normalizar()
    limite = opciones.limite_publicaciones
    carpeta.mkdir(parents=True, exist_ok=True)

    datos = {
        "perfil": parsear_perfil([], None, usuario),
        "opciones": asdict(opciones),
        "fecha_extraccion": n.iso_utc(datetime.now(timezone.utc)),
        "motor": "playwright",
        "estado": "en_curso",
        "avisos": [],
        "publicaciones": [],
    }

    try:
        progreso(0.02, "Abriendo el perfil...")
        captura = sitio.abrir_perfil(usuario)
        datos["perfil"] = parsear_perfil(captura["payloads"], captura.get("meta"), usuario)
        if datos["perfil"]["seguidores"] is None:
            datos["avisos"].append("No se pudo leer el número de seguidores ni de seguidos.")

        encontradas = {
            p["shortcode"]: p for p in parsear_publicaciones(
                captura["payloads"],
                usuario=datos["perfil"]["usuario"],
                usuario_id=datos["perfil"].get("id_usuario"),
            )
        }
        enlaces = list(captura.get("enlaces", []))

        # Desplazamiento para cargar más publicaciones
        rondas, vacias = 0, 0
        max_rondas = max(20, limite // 2 + 5)
        while len(encontradas) < limite and rondas < max_rondas and vacias < 2:
            progreso(0.05 + 0.15 * rondas / max_rondas, f"Cargando publicaciones ({len(encontradas)} encontradas)...")
            antes = len(encontradas)
            mas = sitio.cargar_mas()
            enlaces += mas.get("enlaces", [])
            for pub in parsear_publicaciones(
                mas["payloads"],
                usuario=datos["perfil"]["usuario"],
                usuario_id=datos["perfil"].get("id_usuario"),
            ):
                previa = encontradas.get(pub["shortcode"])
                if previa is None or len(pub["_medios"]) > len(previa["_medios"]):
                    encontradas[pub["shortcode"]] = pub
            rondas += 1
            vacias = vacias + 1 if len(encontradas) == antes else 0

        if os.environ.get("DEBUG") == "1":
            total_payloads = len(captura.get("payloads", []))
            print(
                f"[extraer] payloads={total_payloads} "
                f"posts_reales={len(encontradas)} "
                f"enlaces={len(enlaces)} "
                f"a_descargar={min(len(encontradas), limite)}"
            )

        pubs = sorted(encontradas.values(), key=lambda p: p["fecha_publicacion"] or "", reverse=True)[:limite]

        if not pubs:
            if datos["perfil"]["es_privado"]:
                raise RuntimeError(f"'{usuario}' es privado y la cuenta alternativa no lo sigue.")
            pubs = _publicaciones_desde_enlaces(enlaces)[:limite]
            if not pubs:
                raise RuntimeError(
                    "Instagram no entregó ninguna publicación. Puede que la cuenta esté restringida "
                    "temporalmente o que la estructura de la página haya cambiado."
                )
            datos["avisos"].append(
                "Modo fallback por enlaces activado: no se pudo verificar el dueño "
                "de cada post, así que puede incluir ads o posts sugeridos."
            )

        fallos_seguidos = 0
        for numero, pub in enumerate(pubs, start=1):
            progreso(0.2 + 0.78 * (numero - 1) / len(pubs), f"Publicación {numero} de {len(pubs)}...")
            pub["numero_post"] = numero
            medios = pub.pop("_medios", [])
            # Limpiar metadatos internos del JSON final:
            pub.pop("_propietario_usuario", None)
            pub.pop("_propietario_id", None)
            try:
                if opciones.fotos or opciones.videos:
                    _descargar_medios(sitio, pub, medios, opciones, carpeta, datos["avisos"])
                if opciones.comentarios and pub["comentarios"] > 0:
                    brutos = sitio.comentarios(pub["shortcode"], opciones.limite_comentarios,
                                               pub["descripcion"], datos["perfil"]["usuario"])
                    autor = datos["perfil"]["usuario"].lower()
                    pub["comentarios_lista"] = [
                        {
                            "autor": "AUTOR_DEL_PERFIL" if c["usuario"].lower() == autor else n.seudonimo(c["usuario"]),
                            "es_autor_del_perfil": c["usuario"].lower() == autor,
                            "texto": n.anonimizar(c["texto"], datos["perfil"]["usuario"]),
                            "fecha": c["fecha"],
                            "likes": c["likes"],
                        }
                        for c in brutos
                    ]
                    if not brutos:
                        datos["avisos"].append(f"Publicación {numero}: no se pudieron leer los comentarios.")
                fallos_seguidos = 0
            except RuntimeError as error:
                fallos_seguidos += 1
                datos["avisos"].append(f"Publicación {numero}: {error}")
                if fallos_seguidos >= 3:
                    raise RuntimeError("Demasiados errores seguidos; se detiene la extracción.")
            datos["publicaciones"].append(pub)
            sitio.pausa(opciones.pausa_min, opciones.pausa_max)

        datos["estado"] = "completo"

    except RuntimeError as error:
        if not datos["publicaciones"]:
            raise  # error fatal sin nada que guardar: se informa a la interfaz
        datos["estado"] = "incompleto"
        datos["avisos"].append(str(error))
    finally:
        if datos["publicaciones"]:
            datos["metricas"] = n.calcular_metricas(datos["perfil"], datos["publicaciones"])
            n.guardar_datos(carpeta, datos)

    progreso(1.0, "Extracción finalizada")
    return datos


# -------------------------------- Punto de entrada --------------------------------
def _lanzar_navegador(p):
    opciones = {"headless": os.environ.get("HEADLESS", "0") == "1"}
    ruta = os.environ.get("NAVEGADOR_RUTA", "").strip()
    if ruta:
        opciones["executable_path"] = ruta
    try:
        return p.chromium.launch(**opciones)
    except Exception as error:  # noqa: BLE001
        if "Executable doesn't exist" in str(error) or "playwright install" in str(error):
            raise RuntimeError(
                "Playwright no tiene un navegador instalado. Ejecuta `playwright install chromium` "
                "o define NAVEGADOR_RUTA en el .env con la ruta de Brave o Chrome."
            )
        raise RuntimeError(f"No se pudo iniciar el navegador: {error}")


def ejecutar_extraccion(usuario: str, cuenta: str, opciones: n.Opciones, progreso) -> dict:
    usuario = n.validar_usuario(usuario)
    if not cuenta:
        raise RuntimeError("Falta la cuenta alternativa (variable IG_CUENTA o campo de la interfaz).")
    cuenta = n.validar_usuario(cuenta)

    estado = ruta_estado(cuenta)
    if not estado.exists():
        raise RuntimeError(
            f"No hay sesión guardada para '{cuenta}'. Usa 'Importar sesión desde Brave' primero."
        )

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise RuntimeError("Falta instalar Playwright: pip install playwright && playwright install chromium")

    with sync_playwright() as p:
        navegador = _lanzar_navegador(p)
        try:
            contexto = navegador.new_context(
                storage_state=str(estado), locale="es-ES", viewport={"width": 1280, "height": 900}
            )
            return extraer(SitioPlaywright(contexto), usuario, opciones, n.CARPETA_DATOS / usuario, progreso)
        finally:
            navegador.close()