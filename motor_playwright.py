
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
DOMINIOS_CDN = (".cdninstagram.com", ".fbcdn.net")

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
        os.chmod(ruta, 0o600)
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

_JS_TARJETA = """(usuario) => {
    const dialogo = document.querySelector("div[role='dialog']");
    const u = usuario.toLowerCase();
    const candidatos = Array.from(document.querySelectorAll("div, section")).filter(el => {
        if (dialogo && el.contains(dialogo)) return false;
        const t = (el.innerText || "").trim();
        if (t.length < 15 || t.length > 450) return false;
        const bajo = t.toLowerCase();
        return bajo.includes(u)
            && /(seguidores|followers)/.test(bajo)
            && /(seguidos|following)/.test(bajo);
    });
    candidatos.sort((a, b) => a.innerText.length - b.innerText.length);
    return candidatos.length ? candidatos[0].innerText : "";
}"""

_NUM = r"(\d[\d.,]*\s*(?:millones|mill\.?|mil|[kKmMbB])?)"


def _cifra_antes_de(texto: str, etiquetas: str) -> Optional[str]:
    m = re.search(_NUM + r"\s*(?:" + etiquetas + r")\b", texto, re.IGNORECASE)
    return m.group(1) if m else None


def _cifra_a_entero(token: str) -> Optional[int]:
    """'12 mil' -> 12000, '1,2 M' -> 1200000, '1.234' -> 1234."""
    t = token.lower().replace(" ", "")
    t = re.sub(r"millones|mill\.?", "m", t)
    t = t.replace("mil", "k")
    return convertir_cifra(t)


def _conteo(d: dict, *claves: str) -> Optional[int]:
    for clave in claves:
        valor = d.get(clave)
        if isinstance(valor, dict) and isinstance(valor.get("count"), int):
            return valor["count"]
        if isinstance(valor, int) and not isinstance(valor, bool):
            return valor
    return None

def _tiene_dato(u: dict) -> bool:
    return any(
        u.get(k) is not None
        for k in ("seguidores", "seguidos", "digito_seguidores", "digito_seguidos")
    )

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

    if meta_descripcion:
        m = PATRON_META.search(meta_descripcion)
        if m:
            for clave, texto in zip(("seguidores", "seguidos", "total_publicaciones"), m.groups()):
                if perfil[clave] is None:
                    perfil[clave] = convertir_cifra(texto)

    return perfil


def _es_nodo_post(d: dict) -> bool:
    """
    Un post REAL del timeline SIEMPRE tiene fecha (`taken_at`).
    Los hijos de carrusel tienen `code` + `image_versions2` pero NO fecha.
    Los ads sí tienen fecha → se filtran después por propietario.
    """
    tiene_codigo = isinstance(d.get("shortcode"), str) or isinstance(d.get("code"), str)
    if not tiene_codigo:
        return False
    return d.get("taken_at_timestamp") is not None or d.get("taken_at") is not None


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
    Devuelve (username_del_dueño, id_numerico_del_dueño).
    """
    for clave in ("owner", "user"):
        obj = d.get(clave)
        if isinstance(obj, dict):
            username = obj.get("username")
            idnum = obj.get("pk") or obj.get("id")
            if username or idnum:
                return (username, str(idnum) if idnum else None)

    media_id = d.get("id") or d.get("pk")
    if isinstance(media_id, str) and "_" in media_id:
        partes = media_id.rsplit("_", 1)
        if len(partes) == 2 and partes[1].isdigit():
            return (None, partes[1])

    return (None, None)


def normalizar_nodo(d: dict) -> dict:
    """Convierte un nodo de publicación al formato interno del proyecto."""
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
                continue
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

    IG_APP_ID = "936619743392459"

    def __init__(self, contexto):
        self.avisos_red = []
        self.contexto = contexto
        self.pagina = contexto.new_page()
        self._respuestas = []
        self.pagina.on("response", self._registrar)

    def _registrar(self, respuesta):
        try:
            if respuesta.request.resource_type in ("xhr", "fetch") and (
                "graphql" in respuesta.url or "/api/v1/" in respuesta.url
            ):
                self._respuestas.append(respuesta)
        except Exception:
            pass

    def _vaciar(self) -> list:
        pendientes, self._respuestas = self._respuestas, []
        payloads = []
        for respuesta in pendientes:
            try:
                payloads.append(respuesta.json())
            except Exception:
                continue
        return payloads

    def _scripts_json(self) -> list:
        try:
            textos = self.pagina.eval_on_selector_all(
                'script[type="application/json"]', "els => els.map(e => e.textContent)"
            )
        except Exception:
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
        except Exception:
            return []

    def _verificar_pagina(self, respuesta) -> None:
        url = self.pagina.url
        if "accounts/login" in url:
            raise RuntimeError(
                "Instagram redirigió al inicio de sesión: la sesión expiró o no es válida."
            )
        if any(palabra in url for palabra in ("challenge", "checkpoint", "suspended")):
            raise RuntimeError(
                "Instagram pide una verificación de seguridad. Resuélvela en Brave."
            )
        estado = getattr(respuesta, "status", None)
        if estado == 404:
            raise RuntimeError("El perfil no existe.")
        if estado == 429:
            raise RuntimeError("Instagram respondió 429 (demasiadas peticiones). Espera.")
        try:
            inicio = self.pagina.inner_text("body")[:2000].lower()
        except Exception:
            inicio = ""
        if "esta página no está disponible" in inicio or "isn't available" in inicio:
            raise RuntimeError("El perfil no existe o no está disponible.")

    def abrir_perfil(self, usuario: str) -> dict:
        try:
            respuesta = self.pagina.goto(f"{INSTAGRAM}/{usuario}/", wait_until="domcontentloaded", timeout=45_000)
            self.pagina.wait_for_timeout(random.uniform(2500, 4500))
        except Exception as error:
            raise RuntimeError(f"No se pudo abrir el perfil: {error}")
        self._verificar_pagina(respuesta)

        try:
            meta = self.pagina.eval_on_selector('meta[property="og:description"]', "e => e.content")
        except Exception:
            meta = None
        return {"payloads": self._scripts_json() + self._vaciar(), "meta": meta, "enlaces": self._enlaces()}

    def cargar_mas(self) -> dict:
        try:
            self.pagina.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            self.pagina.wait_for_timeout(random.uniform(2500, 4500))
        except Exception as error:
            raise RuntimeError(f"No se pudo desplazar la página: {error}")
        return {"payloads": self._vaciar(), "enlaces": self._enlaces()}

    def descargar(self, url: str, destino: Path) -> bool:
        host = urlparse(url).hostname or ""
        if not host.endswith(DOMINIOS_CDN):
            return False
        try:
            respuesta = self.contexto.request.get(url, timeout=60_000)
            if not respuesta.ok:
                return False
            destino.write_bytes(respuesta.body())
        except Exception:
            return False
        return True

    def comentarios(self, shortcode: str, limite: int, descripcion: str, autor: str) -> list:
        self._vaciar()
        try:
            self.pagina.goto(f"{INSTAGRAM}/p/{shortcode}/", wait_until="domcontentloaded", timeout=45_000)
            self.pagina.wait_for_timeout(random.uniform(3000, 5000))
        except Exception as error:
            raise RuntimeError(f"No se pudo abrir la publicación: {error}")
        self._verificar_pagina(None)
        payloads = self._scripts_json() + self._vaciar()
        return parsear_comentarios(payloads, descripcion, autor)[:limite]

    def obtener_perfil_api(self, usuario: str) -> dict:
        """Obtiene los datos reales del perfil usando la API interna."""
        cookies = self.contexto.cookies()
        csrf = next((c["value"] for c in cookies if c["name"] == "csrftoken"), "")
        try:
            r = self.contexto.request.get(
                f"https://www.instagram.com/api/v1/users/web_profile_info/?username={usuario}",
                headers={
                    "x-ig-app-id": self.IG_APP_ID,
                    "x-csrftoken": csrf,
                    "Referer": f"https://www.instagram.com/{usuario}/",
                },
                timeout=20_000,
            )
            if r.ok:
                return (r.json().get("data") or {}).get("user") or {}
        except Exception:
            pass
        return {}

    def pausa(self, minimo: float, maximo: float) -> None:
        self.pagina.wait_for_timeout(random.uniform(minimo, maximo) * 1000)

    # ---------------------- Seguidores / seguidos ----------------------
    def _registrar_aviso(self, mensaje: str) -> None:
        self.avisos_red.append(mensaje)
        print(f"[red] {mensaje}", flush=True)

    def _get_json(self, url: str, reintentos: int = 3) -> Optional[dict]:
        """GET con reintentos. Con reintentos=1 no espera: falla rápido."""
        for intento in range(reintentos):
            csrf = next((c["value"] for c in self.contexto.cookies() if c["name"] == "csrftoken"), "")
            try:
                r = self.contexto.request.get(
                    url,
                    headers={"x-ig-app-id": self.IG_APP_ID, "x-csrftoken": csrf, "Referer": f"{INSTAGRAM}/"},
                    timeout=20_000,
                )
            except Exception as error:
                self._registrar_aviso(f"Error de red en {url.split('?')[0]}: {error}")
                continue
            if r.ok:
                try:
                    return r.json()
                except Exception:
                    return None
            if r.status == 429:
                self._registrar_aviso(f"HTTP 429 en {url.split('?')[0]} (intento {intento + 1}/{reintentos})")
                if intento < reintentos - 1:
                    self.pagina.wait_for_timeout(60_000 * (intento + 1))
                continue
            self._registrar_aviso(f"HTTP {r.status} en {url.split('?')[0]}")
            return None
        return None

    def stats_usuario(self, user_id: str) -> dict:
        """Seguidores y seguidos de un usuario (API interna). Falla rápido, sin esperas largas."""
        if not user_id:
            return {"seguidores": None, "seguidos": None}
        data = self._get_json(f"{INSTAGRAM}/api/v1/users/{user_id}/info/", reintentos=1)
        user = (data or {}).get("user") or {}
        return {"seguidores": user.get("follower_count"), "seguidos": user.get("following_count")}

    def _leer_tarjeta(self, nombre: str) -> Optional[dict]:
        """Busca en las respuestas capturadas tras el hover los datos del usuario."""
        for payload in self._vaciar():
            for d in recorrer(payload):
                if str(d.get("username", "")).lower() != nombre:
                    continue
                seg = _conteo(d, "follower_count", "edge_followed_by")
                sig = _conteo(d, "following_count", "edge_follow")
                if seg is not None or sig is not None:
                    return {"seguidores": seg, "seguidos": sig}
        return None

    def _descartar_popups(self) -> None:
        for texto in ("Ahora no", "Not Now", "Aceptar todo", "Permitir todas las cookies",
                      "Decline optional cookies", "Rechazar cookies opcionales"):
            try:
                boton = self.pagina.get_by_role("button", name=texto).first
                if boton.is_visible(timeout=800):
                    boton.click()
                    self.pagina.wait_for_timeout(500)
            except Exception:
                pass

    def _captura_depuracion(self, nombre: str) -> None:
        if os.environ.get("DEBUG") != "1":
            return
        try:
            n.CARPETA_DATOS.mkdir(parents=True, exist_ok=True)
            ruta = n.CARPETA_DATOS / f"debug_{nombre}.png"
            self.pagina.screenshot(path=str(ruta))
            self._registrar_aviso(f"Captura de depuración guardada en {ruta}")
        except Exception:
            pass

    def _leer_tarjeta_dom(self, nombre: str, intentos: int = 5) -> Optional[dict]:
        """Lee el texto de la tarjeta emergente (no depende del formato de red)."""
        for _ in range(intentos):
            self.pagina.wait_for_timeout(400)
            try:
                texto = self.pagina.evaluate(_JS_TARJETA, nombre)
            except Exception:
                texto = ""
            if not texto:
                continue
            seg = _cifra_antes_de(texto, "seguidores|followers")
            sig = _cifra_antes_de(texto, "seguidos|following")
            pub = _cifra_antes_de(texto, "publicaciones|posts")
            datos = {
                "seguidores": _cifra_a_entero(seg) if seg else None,
                "seguidos": _cifra_a_entero(sig) if sig else None,
                "publicaciones": _cifra_a_entero(pub) if pub else None,
            }
            if datos["seguidores"] is not None or datos["seguidos"] is not None:
                return datos
        return None

    def stats_por_hover(self, perfil: str, tipo: str, usuarios: list,
                        vacias_max: int = 3, al_avanzar=None) -> dict:
        """Abre el modal, pone el cursor sobre cada usuario y lee su tarjeta.
        Prueba primero el enlace con texto y luego el del avatar. Devuelve {usuario: {...}}."""
        pendientes = {u["usuario"].lower(): u["usuario"] for u in usuarios if u.get("usuario")}
        total, procesados, leidos = len(pendientes), 0, {}
        try:
            self.pagina.goto(f"{INSTAGRAM}/{perfil}/", wait_until="domcontentloaded", timeout=45_000)
            self.pagina.wait_for_timeout(random.uniform(2500, 4000))
            self._descartar_popups()
            enlace = self.pagina.locator(f'a[href*="/{tipo}/"]')
            if enlace.count() == 0:
                patron = r"seguidores|followers" if tipo == "followers" else r"seguidos|following"
                enlace = self.pagina.get_by_role("link", name=re.compile(patron, re.I))
            enlace = enlace.first
            enlace.wait_for(state="visible", timeout=15_000)
            enlace.click(timeout=10_000)
            dialogo = self.pagina.locator('div[role="dialog"]').first
            dialogo.wait_for(timeout=15_000)
            self.pagina.wait_for_timeout(1500)
        except Exception as error:
            self._registrar_aviso(f"No se pudo abrir el modal de {tipo}: {error}")
            self._captura_depuracion(f"modal_{tipo}")
            return leidos

        vacias, rondas = 0, 0
        while pendientes and vacias < vacias_max and rondas < 300:
            rondas += 1
            enlaces = dialogo.locator('a[href^="/"]')
            try:
                filas = enlaces.evaluate_all(
                    "els => els.map(e => [e.getAttribute('href'), (e.innerText || '').trim()])"
                )
            except Exception:
                break

            por_usuario = {}
            for i, (href, texto) in enumerate(filas):
                nombre = (href or "").strip("/").lower()
                if nombre in pendientes and "/" not in nombre:
                    por_usuario.setdefault(nombre, []).append((0 if texto else 1, i))

            hubo_hover = False
            for nombre, opciones in por_usuario.items():
                original = pendientes.pop(nombre)            # se intenta una sola vez
                procesados += 1
                if al_avanzar and procesados % 5 == 0:
                    al_avanzar(f"Cursor sobre {tipo}: {procesados}/{total}")
                datos = None
                for _, i in sorted(opciones):                # primero el enlace con texto, luego el avatar
                    self._vaciar()
                    try:
                        objetivo = enlaces.nth(i)
                        objetivo.scroll_into_view_if_needed(timeout=3_000)
                        caja = objetivo.bounding_box()
                        if caja:
                            self.pagina.mouse.move(caja["x"] + caja["width"] / 2,
                                                   caja["y"] + caja["height"] / 2, steps=10)
                        objetivo.hover(timeout=5_000)
                    except Exception:
                        continue
                    hubo_hover = True
                    datos = self._leer_tarjeta_dom(nombre) or self._leer_tarjeta(nombre)
                    self.pagina.mouse.move(0, 0)             # cierra la tarjeta
                    if datos:
                        break
                if datos:
                    leidos[original] = datos
                self.pagina.wait_for_timeout(random.uniform(500, 1200))

            vacias = 0 if hubo_hover else vacias + 1
            try:
                enlaces.last.scroll_into_view_if_needed(timeout=5_000)
                self.pagina.wait_for_timeout(random.uniform(1500, 2500))
            except Exception:
                break
        return leidos

    def _consultar_cifras(self, encontrados: list, tipo: str, perfil: str,
                          limite_stats: int, metodo: str,
                          previos: Optional[dict] = None, al_avanzar=None) -> None:
        """Rellena followers/following de cada usuario de la lista, por tandas."""
        previos = previos or {}
        for u in encontrados:                       # reutiliza lo consultado en tandas anteriores
            p = previos.get(u["id_usuario"])
            if p:
                for clave in ("seguidores", "seguidos", "digito_seguidores", "digito_seguidos"):
                    u[clave] = p.get(clave)

        pendientes = [u for u in encontrados if not _tiene_dato(u)]
        muestra = pendientes
        if limite_stats and limite_stats < len(pendientes):
            muestra = random.sample(pendientes, limite_stats)

        leidos = (
            self.stats_por_hover(perfil, tipo, muestra, al_avanzar=al_avanzar)
            if metodo == "hover" and muestra else {}
        )
        fallos = 0
        for i, u in enumerate(muestra, start=1):
            if al_avanzar and i % 5 == 0:
                al_avanzar(f"Consultando cifras de {tipo}: {i}/{len(muestra)}")
            s = leidos.get(u["usuario"])
            if not s:
                if fallos >= 5:
                    continue  # Instagram está limitando: no insistir
                s = self.stats_usuario(u["id_usuario"])
                if s["seguidores"] is None and s["seguidos"] is None:
                    fallos += 1
                    if fallos == 5:
                        self._registrar_aviso(
                            "5 consultas seguidas sin datos: se detienen las consultas por usuario "
                            "(probable límite de Instagram)."
                        )
                else:
                    fallos = 0
                self.pausa(6, 12)
            u["seguidores"] = s.get("seguidores")
            u["seguidos"] = s.get("seguidos")
            u["digito_seguidores"] = n.primer_digito(u["seguidores"])
            u["digito_seguidos"] = n.primer_digito(u["seguidos"])

        con_datos = sum(1 for u in encontrados if _tiene_dato(u))
        self._registrar_aviso(
            f"{tipo}: {con_datos} de {len(encontrados)} usuarios con datos acumulados. "
            + ("Repite la extracción otro día para completar el resto."
               if con_datos < len(encontrados) else "Lista completa.")
        )
   
    def seguidores(
        self,
        usuario_id: str,
        limite: int,
        tipo: str,                      # "followers" | "following"
        con_stats: bool = False,
        limite_stats: int = 0,          # 0 = todos; si es menor, muestra aleatoria
        metodo: str = "api",            # "api" | "hover"
        perfil: str = "",
        al_avanzar=None,
        previos: Optional[dict] = None,
    ) -> list[dict]:
        if not usuario_id:
            self._registrar_aviso("No se conoce el id del perfil: no se puede leer la lista.")
            return []

        encontrados, vistos, next_max_id = [], set(), None
        paginas = 0
        while not limite or len(encontrados) < limite:
            paginas += 1
            if paginas > 400:
                self._registrar_aviso(f"Se alcanzó el máximo de páginas en '{tipo}'.")
                break
            url = f"{INSTAGRAM}/api/v1/friendships/{usuario_id}/{tipo}/?count=50"
            if next_max_id:
                url += f"&max_id={next_max_id}"
            data = self._get_json(url)
            if data is None:
                self._registrar_aviso(f"La lista '{tipo}' se cortó con {len(encontrados)} usuarios.")
                break
            usuarios = data.get("users", [])
            nuevos = 0
            for u in usuarios:
                id_u = str(u.get("pk") or u.get("id"))
                if id_u in vistos:
                    continue
                vistos.add(id_u)
                nuevos += 1
                encontrados.append({
                    "usuario": u.get("username"),
                    "nombre_completo": u.get("full_name") or "",
                    "es_verificado": bool(u.get("is_verified")),
                    "es_privado": bool(u.get("is_private")),
                    "id_usuario": id_u,
                    "digito_seguidores": None,
                    "digito_seguidos": None,
                })
                if limite and len(encontrados) >= limite:
                    break
            anterior = next_max_id
            next_max_id = data.get("next_max_id")
            if al_avanzar:
                al_avanzar(f"Leyendo {tipo}: {len(encontrados)} usuarios...")
            if not usuarios or not next_max_id or next_max_id == anterior or nuevos == 0:
                break
            self.pausa(2, 4)

        if con_stats and encontrados:
            try:
                self._consultar_cifras(
                    encontrados, tipo, perfil, limite_stats, metodo, previos, al_avanzar
                )
            except Exception as error:  # noqa: BLE001
                self._registrar_aviso(
                    f"Falló la consulta de cifras de {tipo}: {error}. Se conserva la lista."
                )

        return encontrados

# ----------------------------------- Extracción -----------------------------------
def _nombre_base(pub: dict) -> str:
    if pub.get("fecha_publicacion"):
        fecha = datetime.fromisoformat(pub["fecha_publicacion"].replace("Z", "+00:00"))
        return fecha.astimezone(timezone.utc).strftime("%Y-%m-%d_%H-%M-%S_UTC")
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
    """Último recurso: solo URL, sin métricas."""
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

        if datos["perfil"]["seguidores"] is None or not datos["perfil"].get("id_usuario"):
            api_perfil = sitio.obtener_perfil_api(usuario)
            if api_perfil:
                datos["perfil"]["id_usuario"] = str(api_perfil.get("id") or api_perfil.get("pk") or "")
                datos["perfil"]["seguidores"] = (api_perfil.get("edge_followed_by") or {}).get("count")
                datos["perfil"]["seguidos"] = (api_perfil.get("edge_follow") or {}).get("count")
                datos["perfil"]["total_publicaciones"] = (api_perfil.get("edge_owner_to_timeline_media") or {}).get("count")
                datos["perfil"]["nombre_completo"] = api_perfil.get("full_name") or datos["perfil"]["nombre_completo"]
                datos["perfil"]["biografia"] = api_perfil.get("biography") or datos["perfil"]["biografia"]

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

        # --- Seguidores / seguidos ---
        redes = {}
        if opciones.seguidores or opciones.seguidos:
            for activo, clave, tipo, texto, fraccion in (
                (opciones.seguidores, "seguidores", "followers", "seguidores", 0.08),
                (opciones.seguidos, "seguidos", "following", "seguidos", 0.13),
            ):
                if not activo:
                    continue
                progreso(fraccion, f"Leyendo {texto}...")
                anteriores = n.cargar_datos_guardados(usuario) or {}
                previos = {
                    u["id_usuario"]: u
                    for u in (anteriores.get("redes") or {}).get(clave, [])
                    if u.get("id_usuario")
                }
                try:
                    redes[clave] = sitio.seguidores(
                        datos["perfil"].get("id_usuario", ""),
                        opciones.limite_usuarios,
                        tipo,
                        con_stats=opciones.stats_redes,
                        limite_stats=opciones.limite_stats,
                        metodo=opciones.metodo_stats,
                        perfil=datos["perfil"]["usuario"],
                        previos=previos,
                        al_avanzar=lambda m, f=fraccion: progreso(f, m),
                    )
                    declarados = datos["perfil"].get(clave) or 0
                    obtenidos = len(redes[clave])
                    if declarados and not opciones.limite_usuarios and obtenidos < 0.95 * declarados:
                        datos["avisos"].append(
                            f"Lista de {texto} incompleta: {obtenidos} de {declarados} "
                            "(Instagram suele cortar listas largas)."
                        )
                    if not redes[clave] and declarados > 0:
                        datos["avisos"].append(
                            f"El perfil declara {declarados} {texto} pero Instagram "
                            "devolvió la lista vacía: restricción temporal probable."
                        )
                except Exception as error:  # noqa: BLE001 - se registra y se sigue con lo demás
                    datos["avisos"].append(f"No se pudieron leer los {texto}: {error}")
                datos["avisos"].extend(sitio.avisos_red)
                sitio.avisos_red.clear()
                sitio.pausa(8, 15)

            datos["redes"] = redes
            datos["benford"] = n.calcular_benford_redes(redes)

            # Volver al perfil tras navegar a followers/following
            try:
                sitio.pagina.goto(f"{INSTAGRAM}/{usuario}/", wait_until="domcontentloaded", timeout=45_000)
                sitio.pagina.wait_for_timeout(random.uniform(2000, 3500))
            except Exception as error:
                datos["avisos"].append(f"No se pudo volver al perfil tras redes: {error}")

        # --- Desplazamiento para cargar más publicaciones ---
        rondas, vacias = 0, 0
        max_rondas = max(20, limite // 2 + 5)
        while len(encontradas) < limite and rondas < max_rondas and vacias < 2:
            progreso(0.20 + 0.15 * rondas / max_rondas, f"Cargando publicaciones ({len(encontradas)} encontradas)...")
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
            print(
                f"[extraer] payloads={len(captura.get('payloads', []))} "
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
            progreso(0.35 + 0.64 * (numero - 1) / len(pubs), f"Publicación {numero} de {len(pubs)}...")
            pub["numero_post"] = numero
            medios = pub.pop("_medios", [])
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
        # Si ya hay publicaciones o listas de seguidores/seguidos, se conserva lo obtenido.
        if not datos["publicaciones"] and not datos.get("redes"):
            raise
        datos["estado"] = "incompleto"
        datos["avisos"].append(str(error))
    finally:
        if datos["publicaciones"] or datos.get("redes"):
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
    except Exception as error:
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
    except ImportError as error:
        import sys
        raise RuntimeError(
            f"Falta el paquete 'playwright' en el intérprete activo ({sys.executable}).\n"
            f"Instálalo con: {sys.executable} -m pip install playwright\n"
            f"y el binario con: {sys.executable} -m playwright install chromium"
        ) from error

    with sync_playwright() as p:
        navegador = _lanzar_navegador(p)
        try:
            contexto = navegador.new_context(
                storage_state=str(estado), locale="es-ES", viewport={"width": 1280, "height": 900}
            )
            return extraer(SitioPlaywright(contexto), usuario, opciones, n.CARPETA_DATOS / usuario, progreso)
        finally:
            navegador.close()
