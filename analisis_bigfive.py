"""
Análisis exploratorio de rasgos Big Five (OCEAN) a partir de las descripciones
de publicaciones, usando la API de Gemini (Google).

Este módulo solo expone las piezas que consume `nucleo.py`: configuración del
modelo, prompt, anonimización, parseo y la llamada HTTP. La orquestación (qué
datos enviar, cuándo analizar) vive en `nucleo.py`.

Requisitos:
    pip install requests python-dotenv

Configuración (archivo .env):
    GEMINI_API_KEY=...
    GEMINI_MODEL=gemini-2.0-flash   (opcional; por defecto se usa este)

Esto NO es una evaluación psicológica. Estimar la personalidad a partir de unos
pocos textos de redes sociales es poco fiable. Úsalo solo con cuentas propias o
de personas que hayan dado su consentimiento.
"""
import json
import os
import re
from typing import Optional

try:  # carga el archivo .env si python-dotenv está instalado
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Modelo por defecto; se cambia con GEMINI_MODEL en el archivo .env
MODELO_POR_DEFECTO = "gemini-2.5-flash"

# Fuente única: `nucleo.py` los importa desde aquí.
MIN_PUBLICACIONES_CON_TEXTO = 8
MAX_CARACTERES_POR_PUBLICACION = 600

RASGOS = ["apertura", "responsabilidad", "extraversion", "amabilidad", "neuroticismo"]

PROMPT_SISTEMA = """Eres un asistente de apoyo a una práctica académica de análisis de texto.
Recibirás descripciones de publicaciones de redes sociales, ya anonimizadas, y debes
hacer una estimación EXPLORATORIA de los cinco grandes rasgos de personalidad (Big Five).

Reglas:
- Basa cada puntuación únicamente en evidencia presente en los textos. Si no hay
  evidencia suficiente para un rasgo, di que es insuficiente y usa confianza "baja".
- Puntuación de 1 (muy bajo) a 5 (muy alto). Usa 3 cuando no haya señal clara.
- Los textos de redes sociales muestran cómo alguien se presenta, no cómo es. Dilo
  en las limitaciones.
- NO infieras ni menciones salud física o mental, diagnósticos, orientación sexual,
  religión, ideología política, origen étnico ni situación económica.
- No intentes identificar a la persona ni deduzcas datos personales.
- Responde SOLO con un objeto JSON válido, sin texto adicional ni bloques de código.

Formato exacto:
{
  "rasgos": {
    "apertura":        {"puntuacion": 1-5, "confianza": "baja|media|alta", "evidencia": "..."},
    "responsabilidad": {"puntuacion": 1-5, "confianza": "baja|media|alta", "evidencia": "..."},
    "extraversion":    {"puntuacion": 1-5, "confianza": "baja|media|alta", "evidencia": "..."},
    "amabilidad":      {"puntuacion": 1-5, "confianza": "baja|media|alta", "evidencia": "..."},
    "neuroticismo":    {"puntuacion": 1-5, "confianza": "baja|media|alta", "evidencia": "..."}
  },
  "resumen": "máximo 4 frases, en tono descriptivo y no concluyente",
  "limitaciones": "máximo 3 frases"
}
La evidencia debe describir temas o patrones, sin copiar frases literales."""


# ------------------------------- Anonimización -------------------------------
def anonimizar(texto: str, usuario: Optional[str] = None) -> str:
    """Quita datos que identifican a personas antes de enviar nada a la API."""
    texto = re.sub(r"https?://\S+|www\.\S+", "[URL]", texto)
    texto = re.sub(r"[\w.+-]+@[\w-]+\.[\w.-]+", "[EMAIL]", texto)
    texto = re.sub(r"@[\w.]+", "[USUARIO]", texto)
    texto = re.sub(r"\+?\d[\d\s().-]{7,}\d", "[TELEFONO]", texto)
    if usuario:
        texto = re.sub(re.escape(usuario), "[AUTOR]", texto, flags=re.IGNORECASE)
    return texto.strip()


# ---------------------------------- API de IA ----------------------------------
def api_key_configurada() -> bool:
    return bool(os.environ.get("GEMINI_API_KEY"))


def modelo_configurado() -> str:
    return os.environ.get("GEMINI_MODEL", MODELO_POR_DEFECTO)


URL_GEMINI = "https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent"


def parsear_json(texto: str) -> dict:
    """Extrae el objeto JSON aunque el modelo lo envuelva en ``` o añada texto."""
    texto = re.sub(r"```(?:json)?", "", texto).strip()
    inicio, fin = texto.find("{"), texto.rfind("}")
    if inicio == -1 or fin == -1:
        raise ValueError("La respuesta del modelo no contiene un objeto JSON.")
    try:
        return json.loads(texto[inicio : fin + 1])
    except json.JSONDecodeError as error:
        raise ValueError(
            f"El modelo devolvió un JSON malformado ({error.msg} en la posición "
            f"{error.pos}). Reintenta el análisis; si persiste, prueba otro modelo "
            "en GEMINI_MODEL."
        ) from error


def llamar_gemini(sistema: str, mensaje: str) -> str:
    """
    Llama a la API REST de Gemini con `requests` (sin el SDK de Google, que arrastra
    dependencias difíciles de instalar en algunos Mac Intel). La clave se envía en una
    cabecera, nunca en la URL.
    """
    try:
        import requests
    except ImportError:
        raise RuntimeError("Falta instalar la librería 'requests' (pip install requests).")

    if not api_key_configurada():
        raise RuntimeError(
            "No se encontró GEMINI_API_KEY. Créala en el archivo .env "
            "(copia .env.example) y reinicia la aplicación."
        )

    modelo = modelo_configurado().removeprefix("models/")
    cuerpo = {
        "system_instruction": {"parts": [{"text": sistema}]},
        "contents": [{"role": "user", "parts": [{"text": mensaje}]}],
        "generationConfig": {"responseMimeType": "application/json"},
    }

    try:
        respuesta = requests.post(
            URL_GEMINI.format(modelo=modelo),
            headers={
                "x-goog-api-key": os.environ["GEMINI_API_KEY"],
                "Content-Type": "application/json",
            },
            json=cuerpo,
            timeout=120,
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
                f"El modelo '{modelo}' no está disponible. Cambia GEMINI_MODEL en el "
                "archivo .env por uno que aparezca en Google AI Studio."
            )
        if codigo == 429:
            raise RuntimeError("Se alcanzó el límite o la cuota de la API de Gemini. Espera un momento.")
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
            + (f" (bloqueado: {motivo})" if motivo else " (puede que un filtro de seguridad bloqueara la respuesta)")
            + ". Reintenta o prueba con otro modelo en GEMINI_MODEL."
        )
    return texto