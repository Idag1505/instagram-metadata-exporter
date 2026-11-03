import base64
import json
import os
import re
from typing import Optional

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

MODELO_POR_DEFECTO = "gemini-2.5-flash"

MIN_PUBLICACIONES_CON_TEXTO = 8
MAX_CARACTERES_POR_PUBLICACION = 600

RASGOS = ["apertura", "responsabilidad", "extraversion", "amabilidad", "neuroticismo"]

PROMPT_SISTEMA = """Eres un asistente experto en lingüística computacional, análisis multimodal y psicometría orientada al análisis de contenido digital.
Recibirás un corpus estructurado con descripciones de publicaciones (anonimizadas), emojis, métricas agregadas de comportamiento, comentarios de terceros seudonimizados, datos básicos del perfil y, opcionalmente, imágenes asociadas.
Debes realizar una estimación EXPLORATORIA rigurosa de los cinco grandes rasgos de personalidad (Big Five / OCEAN), triangulando evidencias de todas las fuentes disponibles.

Marco de Referencia por Rasgo (señales textuales, para-textuales y visuales):
- Apertura a la experiencia: curiosidad intelectual, creatividad, interés estético, tolerancia a la novedad, vocabulario variado, lenguaje abstracto o simbólico, diversidad temática, mezcla de registros, estética visual no convencional.
- Responsabilidad (Conciencia): orientación al logro, orden, disciplina, lenguaje estructurado, planificación, coherencia temática, persistencia de proyectos, composición cuidada de las imágenes.
- Extraversión: sociabilidad, asertividad, tono positivo, referencias a eventos sociales, interacción con terceros, alta densidad de emojis expresivos, frecuencia elevada de publicación, presencia de personas o actividades grupales en las imágenes.
- Amabilidad: empatía, cooperación, lenguaje afable, tono conciliador, gratitud, afecto, baja agresividad discursiva, comentarios positivos de terceros, imágenes cálidas o afectuosas.
- Neuroticismo (Inestabilidad emocional): emociones negativas, reactividad al estrés, lenguaje absoluto o catastrofista, cambios bruscos de tono, longitud variable de captions, estética visual sombría o desaturada.

Fuentes de evidencia disponibles (úsalas TODAS cuando existan):
1. Texto de las publicaciones: léxico, sintaxis, temas, coherencia entre publicaciones.
2. Emojis y símbolos: cantidad, densidad, variedad, tipo (positivos, negativos, neutros, irónicos).
3. Métricas agregadas: frecuencia de publicación, proporción de tipos de contenido, longitud media de caption, hashtags, menciones, interacción, comentarios medios.
4. Comentarios de terceros seudonimizados: tono predominante, reciprocidad social.
5. Datos del perfil: biografía, verificación, seguidores/seguidos, total de publicaciones.
6. Imágenes (si se aportan): personas, actividades, entornos, estética, autorretratos, composición visual.

Reglas Metodológicas de Evaluación:
1. Triangulación obligatoria: cada puntuación debe apoyarse en al menos DOS fuentes distintas cuando estén disponibles. Cita en la evidencia qué fuentes convergen. Si solo hay texto, dilo explícitamente.
2. Anclaje en evidencia: no inventes señales. Si una fuente no está presente en el payload, no la menciones ni la infieras.
3. Criterio de Confianza:
   - "baja": una sola fuente con 1–2 indicadores aislados o ambiguos.
   - "media": dos o más fuentes con 2–3 indicadores coherentes entre sí.
   - "alta": tres o más fuentes con 4 o más indicadores convergentes.
4. Asignación de Puntuación (Likert 1–5):
   - 1: evidencia persistente y opuesta al rasgo.
   - 2: tendencia discreta opuesta.
   - 3: neutro, indeterminado o contradictorio.
   - 4: tendencia manifiesta hacia el rasgo.
   - 5: evidencia prototípica, constante y acentuada.
5. Si la confianza es "baja", la puntuación DEBE ser obligatoriamente 3.
6. Sesgo de autorrepresentación: los textos e imágenes de redes sociales reflejan la imagen deseada, no la personalidad latente. Refléjalo en las limitaciones.
7. Los comentarios de terceros miden la audiencia del autor, no su personalidad. Úsalos solo como señal de reciprocidad social o de tono del entorno.
8. Las métricas de interacción (likes, comentarios, seguidores) dependen de la audiencia y del algoritmo. Úsalas como señal débil y nunca como evidencia directa.
9. Restricciones éticas estrictas: PROHIBIDO inferir o mencionar diagnósticos de salud mental o física, orientación sexual, creencias religiosas, ideología política, origen étnico, clase o situación socioeconómica.
10. Anonimato: no intentes identificar a la persona ni deducir datos de identificación personal (PII).

Requisitos de Formato de Salida:
- Responde ÚNICAMENTE con un objeto JSON válido, sin delimitadores de código ni texto adicional.
- La "evidencia" debe sintetizar temas, estilos, patrones y convergencias entre fuentes, evitando la copia literal de frases.
- Si una fuente no aportó señal para un rasgo, indícalo brevemente (p. ej. "sin señal en comentarios").

Estructura JSON:
{
  "rasgos": {
    "apertura":        {"puntuacion": 1-5, "confianza": "baja|media|alta", "evidencia": "..."},
    "responsabilidad": {"puntuacion": 1-5, "confianza": "baja|media|alta", "evidencia": "..."},
    "extraversion":    {"puntuacion": 1-5, "confianza": "baja|media|alta", "evidencia": "..."},
    "amabilidad":      {"puntuacion": 1-5, "confianza": "baja|media|alta", "evidencia": "..."},
    "neuroticismo":    {"puntuacion": 1-5, "confianza": "baja|media|alta", "evidencia": "..."}
  },
  "resumen": "Máximo 4 frases, en tono descriptivo, condicional y estrictamente no concluyente.",
  "limitaciones": "Máximo 3 frases, enfatizando el sesgo de autorrepresentación y las limitaciones del corpus.",
  "fuentes_usadas": ["texto", "emojis", "metricas", "comentarios", "perfil", "imagenes"]
}"""


def anonimizar(texto: str, usuario: Optional[str] = None) -> str:
    texto = re.sub(r"https?://\S+|www\.\S+", "[URL]", texto)
    texto = re.sub(r"[\w.+-]+@[\w-]+\.[\w.-]+", "[EMAIL]", texto)
    texto = re.sub(r"@[\w.]+", "[USUARIO]", texto)
    texto = re.sub(r"\+?\d[\d\s().-]{7,}\d", "[TELEFONO]", texto)
    if usuario:
        texto = re.sub(re.escape(usuario), "[AUTOR]", texto, flags=re.IGNORECASE)
    return texto.strip()


def api_key_configurada() -> bool:
    return bool(os.environ.get("GEMINI_API_KEY"))


def modelo_configurado() -> str:
    return os.environ.get("GEMINI_MODEL", MODELO_POR_DEFECTO)


URL_GEMINI = "https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent"

_SAFETY = [
    {"category": "HARM_CATEGORY_HARASSMENT",        "threshold": "BLOCK_ONLY_HIGH"},
    {"category": "HARM_CATEGORY_HATE_SPEECH",       "threshold": "BLOCK_ONLY_HIGH"},
    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_ONLY_HIGH"},
    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_ONLY_HIGH"},
]


def parsear_json(texto: str) -> dict:
    texto = re.sub(r"```(?:json)?", "", texto).strip()
    inicio, fin = texto.find("{"), texto.rfind("}")
    if inicio == -1 or fin == -1:
        raise ValueError("La respuesta del modelo no contiene un objeto JSON.")
    try:
        return json.loads(texto[inicio : fin + 1])
    except json.JSONDecodeError as error:
        raise ValueError(
            f"El modelo devolvió un JSON malformado ({error.msg} en la posición {error.pos})."
        ) from error


def _enviar(cuerpo: dict, timeout: int = 120) -> str:
    """POST a Gemini + manejo común de errores. Devuelve el texto de la respuesta."""
    try:
        import requests
    except ImportError:
        raise RuntimeError("Falta instalar la librería 'requests' (pip install requests).")

    if not api_key_configurada():
        raise RuntimeError(
            "No se encontró GEMINI_API_KEY. Créala en el archivo .env (copia .env.example)."
        )

    modelo = modelo_configurado().removeprefix("models/")

    try:
        respuesta = requests.post(
            URL_GEMINI.format(modelo=modelo),
            headers={
                "x-goog-api-key": os.environ["GEMINI_API_KEY"],
                "Content-Type": "application/json",
            },
            json=cuerpo,
            timeout=timeout,
        )
    except requests.RequestException as error:
        raise RuntimeError(f"No se pudo conectar con la API de Gemini: {error}")

    if respuesta.status_code != 200:
        try:
            detalle = respuesta.json().get("error", {}).get("message", respuesta.text)
        except ValueError:
            detalle = respuesta.text
        codigo = respuesta.status_code
        if codigo in (401, 403) or "API key" in detalle:
            raise RuntimeError("La clave de API de Gemini no es válida o no tiene permisos.")
        if codigo == 404:
            raise RuntimeError(
                f"El modelo '{modelo}' no está disponible. Cambia GEMINI_MODEL en el .env."
            )
        if codigo == 429:
            raise RuntimeError("Se alcanzó el límite o la cuota de la API de Gemini.")
        raise RuntimeError(f"Error de la API de Gemini ({codigo}): {detalle[:200]}")

    try:
        datos = respuesta.json()
    except ValueError:
        raise RuntimeError("La respuesta de Gemini no tiene un formato válido.")

    candidatos = datos.get("candidates") or []
    partes = candidatos[0].get("content", {}).get("parts", []) if candidatos else []
    texto = "".join(p.get("text", "") for p in partes if not p.get("thought"))
    if not texto:
        motivo = (datos.get("promptFeedback") or {}).get("blockReason")
        raise RuntimeError(
            "Gemini no devolvió texto"
            + (f" (bloqueado: {motivo})" if motivo else " (posible filtro de seguridad)")
            + ". Reintenta o cambia GEMINI_MODEL."
        )
    return texto


def llamar_gemini(sistema: str, mensaje: str) -> str:
    """Variante solo texto."""
    cuerpo = {
        "system_instruction": {"parts": [{"text": sistema}]},
        "contents": [{"role": "user", "parts": [{"text": mensaje}]}],
        # safetySettings es hermano de generationConfig, NO hijo.
        "safetySettings": _SAFETY,
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": 0.2,
        },
    }
    return _enviar(cuerpo, timeout=120)


def llamar_gemini_multimodal(
    sistema: str,
    mensaje: str,
    imagenes: list[tuple[str, bytes]],
) -> str:
    """
    Variante multimodal: añade las imágenes inline (base64) al mensaje.
    `imagenes` es una lista de (mime_type, bytes).
    """
    partes_usuario: list[dict] = [{"text": mensaje}]
    for mime, contenido in imagenes:
        partes_usuario.append({
            "inline_data": {
                "mime_type": mime,
                "data": base64.b64encode(contenido).decode("ascii"),
            }
        })

    cuerpo = {
        "system_instruction": {"parts": [{"text": sistema}]},
        "contents": [{"role": "user", "parts": partes_usuario}],
        # safetySettings es hermano de generationConfig, NO hijo.
        "safetySettings": _SAFETY,
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": 0.2,
        },
    }
    return _enviar(cuerpo, timeout=180)
