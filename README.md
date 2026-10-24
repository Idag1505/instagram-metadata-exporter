# Instagram Metadata Exporter

Proyecto académico en Python que obtiene metadatos públicos de un perfil de Instagram usando [Instaloader](https://instaloader.github.io/), los exporta a JSON y ofrece una **interfaz web** para extraer datos, ver la multimedia y generar un análisis exploratorio de personalidad (Big Five) con la API de un modelo de IA.

> **Uso exclusivamente educativo.** Consulta la sección [Aviso ético y legal](#aviso-ético-y-legal) antes de ejecutar el código.

---

## Qué hace

- Obtiene los **datos del perfil**: nombre de usuario, nombre completo, biografía, número de seguidores, número de seguidos, total de publicaciones, enlace externo, si es privado y si está verificado.
- Obtiene las **últimas publicaciones**: identificador, enlace, descripción, hashtags, menciones, likes, comentarios, interacciones, fecha en UTC y tipo de contenido (foto, carrusel o video/reel).
- **Descarga fotos, videos y reels** (opcional) y los enlaza con cada publicación.
- Obtiene **comentarios** (opcional), con los usuarios **seudonimizados** y los textos **anonimizados**.
- Calcula **métricas agregadas** de comportamiento (frecuencia de publicación, tipo de contenido, uso de hashtags, interacción media, etc.).
- Genera un **análisis Big Five exploratorio** a partir de las descripciones y las métricas, usando una API de IA cuya clave se guarda en un archivo `.env`.
- Permite reutilizar una sesión iniciada en el navegador **Brave** mediante sus cookies, sin escribir la contraseña en el código.
- Incluye un flujo alternativo que convierte respaldos `.json.xz` de Instaloader a un único JSON.

## Estructura del repositorio

| Archivo | Descripción |
|---|---|
| `app_web.py` + `templates/` | **Interfaz web** (Flask): extracción, multimedia, comentarios y análisis Big Five. No requiere compilar nada. |
| `app.py` | Interfaz alternativa en Streamlit (opcional, ver `requirements-streamlit.txt`). |
| `nucleo.py` | Lógica de la aplicación: extracción con Instaloader y llamada a la API de IA. |
| `analisis_bigfive.py` | Anonimización, instrucciones del modelo y versión por línea de comandos del análisis. |
| `instagram_json.py` | **Flujo A.** Script que consulta Instagram y genera un JSON directamente (perfil + publicaciones). |
| `convertidor_publicaciones.py` | **Flujo B.** Convierte respaldos `.json.xz` de Instaloader en un JSON. |
| `diagnostico_seguidores.py` | Script de prueba para comprobar si Instagram devuelve las listas de seguidores y seguidos. |
| `.env.example` | Plantilla de configuración. Se copia como `.env` (que no se sube al repositorio). |
| `requirements.txt` | Dependencias del proyecto. |
| `requirements-streamlit.txt` | Dependencias extra, solo para la interfaz Streamlit opcional. |
| `resultados/` | Muestra reducida de resultados de ejemplo. |
| `.gitignore` | Evita subir claves, sesiones, cookies, datos de terceros y multimedia. |

## Requisitos

- Python 3.10 o superior
- Una cuenta de Instagram **alternativa** (no uses tu cuenta personal principal)
- Navegador Brave con la sesión de esa cuenta iniciada (solo para la primera configuración)
- Una clave de API de Gemini, creada en [Google AI Studio](https://aistudio.google.com/) (solo para el análisis Big Five)

## Instalación

```bash
git clone https://github.com/Idag1505/instagram-metadata-exporter.git
cd instagram-metadata-exporter

python3 -m venv venv
source venv/bin/activate        # En Windows: venv\Scripts\activate

pip install -r requirements.txt
```

## Configuración del archivo `.env`

Las claves y datos privados **nunca van en el código**. Se guardan en un archivo `.env` que está excluido por `.gitignore`.

```bash
cp .env.example .env
```

Después edita `.env` (es un archivo oculto: usa `ls -a` en la terminal o `Cmd + Shift + .` en el Finder):

```
GEMINI_API_KEY=tu_clave_de_gemini
GEMINI_MODEL=gemini-3.5-flash
IG_CUENTA=tu_cuenta_alternativa
PSEUDONIMO_SALT=un-texto-aleatorio
CARPETA_DATOS=datos
```

| Variable | Descripción |
|---|---|
| `GEMINI_API_KEY` | Clave de la API de Gemini (Google AI Studio) usada en el análisis Big Five. |
| `GEMINI_MODEL` | Modelo de Gemini a utilizar (se puede cambiar sin tocar el código). |
| `IG_CUENTA` | Cuenta alternativa cuya sesión usa Instaloader. |
| `PSEUDONIMO_SALT` | Texto aleatorio para seudonimizar a quienes comentan. |
| `CARPETA_DATOS` | Carpeta donde se guardan los datos y la multimedia. |

---

## Uso

### Interfaz web (recomendado)

```bash
python app_web.py
```

Se abre en `http://127.0.0.1:8000` y solo es accesible desde tu propio equipo.

1. **Estado de la configuración:** comprueba que hay sesión guardada y que la clave de API fue detectada. Si no hay sesión, usa *Importar sesión desde Brave* (inicia sesión en instagram.com con la cuenta alternativa y **cierra Brave por completo** antes de pulsar el botón).
2. **Extraer datos:** escribe el usuario, elige cuántas publicaciones y si descargar fotos, videos/reels y comentarios.
3. **Explora los resultados** en las pestañas *Perfil y métricas*, *Multimedia*, *Comentarios* y *Big Five*.

> Alternativa: `pip install -r requirements-streamlit.txt` y `python -m streamlit run app.py`.
4. **Análisis Big Five:** marca la casilla de consentimiento y pulsa *Analizar*. El análisis usa los datos ya extraídos y **no hace nuevas peticiones a Instagram**.

Los resultados se guardan en `datos/<usuario>/` (`datos.json`, `analisis_bigfive.json` y la multimedia).

### Flujo A: script directo

**1. Configura las variables** al inicio de `instagram_json.py`:

```python
PERFIL_OBJETIVO = "perfil_a_consultar"
CUENTA_ALTERNATIVA = "tu_cuenta_alternativa"
LIMITE_PUBLICACIONES = 50
ARCHIVO_SALIDA = "publicaciones_instagram.json"
```

**2. Importa la sesión desde Brave (solo la primera vez):** inicia sesión en `instagram.com`, cierra Brave, cambia `IMPORTAR_DESDE_BRAVE = True`, ejecuta `python instagram_json.py` y vuelve a ponerlo en `False`.

La sesión queda guardada en la carpeta de configuración de Instaloader (`~/.config/instaloader/` en macOS y Linux, `%LOCALAPPDATA%\Instaloader\` en Windows), **fuera del repositorio**.

**3. Ejecuta normalmente:**

```bash
python instagram_json.py
```

### Flujo B: respaldos `.json.xz` + convertidor

Alternativa que usa la línea de comandos de Instaloader:

```bash
instaloader --login=TU_CUENTA_ALTERNATIVA --no-videos --no-video-thumbnails \
            --no-profile-pic --count=20 PERFIL_A_CONSULTAR
python convertidor_publicaciones.py PERFIL_A_CONSULTAR --limite 20
```

Si no se usa `--no-pictures`, las fotos se descargan junto a cada `.json.xz` y el convertidor las enlaza en el campo `imagenes` de cada publicación.

| Parámetro | Descripción | Valor por defecto |
|---|---|---|
| `carpeta` | Carpeta con los archivos `.json.xz` | `.` |
| `--limite` | Máximo de publicaciones a exportar | `50` |
| `--salida` | Archivo JSON de salida | `publicaciones_desde_respaldo.json` |

> En este flujo el campo `perfil` queda en `null`, porque los respaldos de publicaciones no incluyen seguidores ni biografía.

---

## Análisis Big Five

El análisis es una **estimación exploratoria** de los cinco rasgos (apertura, responsabilidad, extraversión, amabilidad y neuroticismo). Cada rasgo recibe una puntuación de 1 a 5, un nivel de confianza y una evidencia descriptiva.

**Qué datos usa:** las descripciones de las publicaciones (anonimizadas) y las métricas agregadas de comportamiento obtenidas con el scraping.

**Qué no usa:** los comentarios de otras personas ni la identidad de nadie. Antes de enviar nada a la API se eliminan menciones, enlaces, correos, teléfonos y el nombre de usuario del autor.

**Salvaguardas incluidas:**

- Pide confirmar el consentimiento de la persona analizada.
- Exige un mínimo de 8 publicaciones con texto.
- Indica al modelo que no infiera salud, orientación sexual, religión, ideología política ni origen étnico.
- El resultado incluye siempre un aviso de que no es una evaluación psicológica.

---

## Formato de salida

```json
{
  "perfil": {
    "usuario": "ejemplo",
    "nombre_completo": "Nombre Ejemplo",
    "biografia": "Texto de la biografía",
    "seguidores": 959,
    "seguidos": 185,
    "total_publicaciones": 120,
    "url_externa": null,
    "es_privado": false,
    "es_verificado": false
  },
  "publicaciones": [
    {
      "numero_post": 1,
      "shortcode": "AbCdEfGhIjK",
      "url": "https://www.instagram.com/p/AbCdEfGhIjK/",
      "tipo": "foto",
      "descripcion": "Texto de la publicación",
      "hashtags": ["ejemplo"],
      "likes": 42,
      "comentarios": 3,
      "interacciones": 45,
      "fecha_publicacion": "2026-10-01T15:30:00+00:00"
    }
  ]
}
```

- `likes` puede ser `-1` si el autor oculta el número de "me gusta".
- `fecha_publicacion` está en UTC.
- `tipo` es `foto`, `carrusel` o `video/reel` en la interfaz (`GraphImage`, `GraphSidecar` o `GraphVideo` en el flujo A).
- La interfaz añade `metricas`, `comentarios_lista` y los nombres de los archivos descargados.

---

## Limitaciones conocidas

- **Listas de seguidores y seguidos.** El script incluye una opción para obtener los nombres de usuario (`INCLUIR_SEGUIDORES` e `INCLUIR_SEGUIDOS`), pero está **desactivada por defecto**. En las pruebas con Instaloader 4.15.4, Instagram devolvió listas vacías sin error, incluso con la sesión válida y también en la propia cuenta, donde el conteo indicaba que sí había datos. Por eso el proyecto registra únicamente los **conteos**. El script `diagnostico_seguidores.py` permite reproducir la comprobación.
- **Restricciones de la cuenta.** Tras varias ejecuciones, Instagram respondió con `429` (demasiadas peticiones) y con `feedback_required` en la consulta de publicaciones. Es la señal de que marcó la cuenta como automatizada. En ese caso hay que detenerse y esperar varios días; descargar multimedia y comentarios aumenta el riesgo porque requiere muchas más peticiones.
- **Reels.** Instaloader no distingue los reels de otros videos del perfil, por lo que se agrupan como `video/reel`.
- **Big Five.** Las publicaciones muestran cómo una persona se presenta, no cómo es. Con pocos textos el resultado es poco fiable.
- **Cookies de Brave.** Algunas versiones de navegadores basados en Chromium cifran las cookies de forma que `browser-cookie3` puede no descifrarlas.
- **Dependencia de Instagram.** Instagram cambia con frecuencia su funcionamiento interno, por lo que el código puede dejar de funcionar en cualquier momento.

## Seguridad y privacidad

- **Nunca subas** al repositorio el archivo `.env`, archivos de sesión (`session-*`), cookies, contraseñas ni capturas que los muestren. La cookie `sessionid` y la clave de API equivalen a contraseñas.
- El `.gitignore` está configurado para excluirlos, junto con la carpeta `datos/`, la multimedia y los JSON. Revisa siempre con `git status` antes de hacer commit.
- La carpeta `resultados/` contiene solo una **muestra reducida** de ejemplo. Los resultados completos incluyen contenido y nombres de usuario de terceros y no se publican.
- Si subes una credencial por error, borrarla en un commit posterior no basta, porque queda en el historial. Revoca la clave de API desde su consola y cierra la sesión de la cuenta de Instagram.

## Aviso ético y legal

Este proyecto se desarrolló con fines **académicos y de aprendizaje** sobre extracción y procesamiento de datos.

- Los términos de uso de Instagram restringen la recolección automatizada de datos. Ejecutar este código puede provocar la limitación o suspensión de la cuenta utilizada. Usa siempre una cuenta alternativa y asume ese riesgo bajo tu responsabilidad.
- Consulta únicamente información **pública** y limita el volumen de datos a lo estrictamente necesario.
- Los datos de otras personas (textos, usuarios, menciones, comentarios, fotos) son datos personales: no los publiques, no los vendas y no los uses para acosar o identificar a nadie.
- El análisis de personalidad solo debe hacerse con la **propia cuenta** o con el **consentimiento expreso** de la persona analizada.
- Para un uso profesional o a gran escala, la vía correcta es la [API oficial de Instagram](https://developers.facebook.com/docs/instagram-platform).

## Solución de problemas

| Problema | Posible causa y solución |
|---|---|
| La interfaz dice que falta `GEMINI_API_KEY` | El archivo debe llamarse exactamente `.env`, estar junto a `app_web.py` y hay que reiniciar la aplicación. |
| `Failed to build 'pyarrow'` / `libcst` / `Rust compiler` al instalar | Es una dependencia de Streamlit que no tiene versión compilada para algunos Mac Intel. Usa `app_web.py`, que no la necesita (`pip install -r requirements.txt`). |
| `No hay sesión guardada` | Importa la sesión desde Brave (botón de la interfaz o `IMPORTAR_DESDE_BRAVE = True`). |
| `No se encontró la cookie 'sessionid'` | Inicia sesión en instagram.com desde Brave y cierra el navegador antes de importar. |
| `La sesión no es válida` | Las cookies expiraron. Vuelve a iniciar sesión en Brave y reimporta. |
| `TooManyRequestsException` / `429` | Instagram limitó las peticiones. Espera al menos un día antes de reintentar. |
| `feedback_required` | La cuenta quedó restringida. No reintentes; espera varios días. |
| `PrivateProfileNotFollowedException` | El perfil es privado y la cuenta alternativa no lo sigue. |
| Listas de seguidores vacías | Limitación conocida, ver [Limitaciones conocidas](#limitaciones-conocidas). |

## Tecnologías

- Python 3
- [Instaloader](https://instaloader.github.io/)
- [browser-cookie3](https://github.com/borisbabic/browser_cookie3)
- [Flask](https://flask.palletsprojects.com/) para la interfaz web (opcional: [Streamlit](https://streamlit.io/) y [pandas](https://pandas.pydata.org/))
- [python-dotenv](https://github.com/theskumar/python-dotenv)
- SDK de Google Gen AI (`google-genai`) para el análisis con Gemini

## Autor

Proyecto desarrollado por **ISIS ALTAMIRANO** como práctica académica de web scraping. Curso: **ARQUITECTURA DE SOFTWARE** · Institución: **UNIVERSIDAD CENTRAL DEL ECUADOR**
