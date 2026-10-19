# Instagram Metadata Exporter

Proyecto académico en Python que obtiene metadatos públicos de un perfil de Instagram usando [Instaloader](https://instaloader.github.io/) y los exporta a un archivo JSON.

> **Uso exclusivamente educativo.** Consulta la sección [Aviso ético y legal](#aviso-ético-y-legal) antes de ejecutar el código.

---

## Qué hace

- Obtiene los **datos del perfil**: nombre de usuario, nombre completo, biografía, número de seguidores, número de seguidos, total de publicaciones, enlace externo, si es privado y si está verificado.
- Obtiene las **últimas publicaciones** (50 por defecto): identificador, enlace, descripción, hashtags, menciones, likes, comentarios, fecha en UTC y tipo de contenido.
- Guarda todo en un archivo JSON estructurado.
- Permite reutilizar una sesión iniciada en el navegador **Brave** mediante sus cookies, sin escribir la contraseña en el código.
- Incluye un segundo flujo que convierte respaldos `.json.xz` de Instaloader a un único JSON.

## Estructura del repositorio

| Archivo | Descripción |
|---|---|
| `instagram_json.py` | **Flujo A.** Consulta Instagram y genera el JSON directamente (perfil + publicaciones). |
| `convertidor_publicaciones.py` | **Flujo B.** Convierte respaldos `.json.xz` de Instaloader en un JSON. |
| `diagnostico_seguidores.py` | Script de prueba para comprobar si Instagram devuelve las listas de seguidores y seguidos. |
| `requirements.txt` | Dependencias del proyecto. |
| `resultados/` | Muestra reducida de resultados de ejemplo. |
| `.gitignore` | Evita subir sesiones, cookies y datos sensibles. |

## Requisitos

- Python 3.10 o superior
- Una cuenta de Instagram **alternativa** (no uses tu cuenta personal principal)
- Navegador Brave con la sesión de esa cuenta iniciada (solo para la primera configuración)

## Instalación

```bash
git clone https://github.com/TU_USUARIO/instagram-metadata-exporter.git
cd instagram-metadata-exporter

python3 -m venv venv
source venv/bin/activate        # En Windows: venv\Scripts\activate

pip install -r requirements.txt
```

Contenido de `requirements.txt`:

```
instaloader
browser-cookie3
```

---

## Uso

### Flujo A: script directo (recomendado)

**1. Configura las variables** al inicio de `instagram_json.py`:

```python
PERFIL_OBJETIVO = "perfil_a_consultar"
CUENTA_ALTERNATIVA = "tu_cuenta_alternativa"
LIMITE_PUBLICACIONES = 50
ARCHIVO_SALIDA = "publicaciones_instagram.json"
```

**2. Importa la sesión desde Brave (solo la primera vez):**

1. Inicia sesión en `instagram.com` con la cuenta alternativa desde Brave.
2. **Cierra Brave por completo.**
3. En el script, cambia `IMPORTAR_DESDE_BRAVE = True` y ejecuta:

   ```bash
   python instagram_json.py
   ```
4. Cuando veas el mensaje de sesión guardada, vuelve a poner `IMPORTAR_DESDE_BRAVE = False`.

La sesión queda guardada en la carpeta de configuración de Instaloader (`~/.config/instaloader/` en macOS y Linux, `%LOCALAPPDATA%\Instaloader\` en Windows), **fuera del repositorio**.

**3. Ejecuta normalmente:**

```bash
python instagram_json.py
```

El resultado se guarda en la carpeta desde la que ejecutas el comando.

### Flujo B: respaldos `.json.xz` + convertidor

Este flujo es una alternativa al anterior. Primero se descargan solo los metadatos con la línea de comandos de Instaloader:

```bash
instaloader --login=TU_CUENTA_ALTERNATIVA --no-pictures --no-videos \
            --no-video-thumbnails --no-profile-pic --count=50 PERFIL_A_CONSULTAR
```

Esto crea una carpeta con el nombre del perfil que contiene un `.json.xz` por publicación. Luego se convierten a un único JSON:

```bash
python convertidor_publicaciones.py PERFIL_A_CONSULTAR --limite 50
```

Parámetros del convertidor:

| Parámetro | Descripción | Valor por defecto |
|---|---|---|
| `carpeta` | Carpeta con los archivos `.json.xz` | `.` |
| `--limite` | Máximo de publicaciones a exportar | `50` |
| `--salida` | Archivo JSON de salida | `publicaciones_desde_respaldo.json` |

> En este flujo el campo `perfil` queda en `null`, porque los respaldos de publicaciones no incluyen seguidores ni biografía.

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
      "id_post": "1234567890",
      "shortcode": "AbCdEfGhIjK",
      "url": "https://www.instagram.com/p/AbCdEfGhIjK/",
      "usuario": "ejemplo",
      "tipo": "GraphImage",
      "cantidad_medios": 1,
      "descripcion": "Texto de la publicación",
      "hashtags": ["ejemplo"],
      "menciones": [],
      "likes": 42,
      "comentarios": 3,
      "vistas_video": null,
      "fecha_publicacion": "2026-10-01T15:30:00+00:00",
      "es_video": false
    }
  ]
}
```

Notas sobre los campos:

- `likes` puede ser `-1` si el autor oculta el número de "me gusta".
- `fecha_publicacion` está en UTC.
- `tipo` puede ser `GraphImage`, `GraphVideo` o `GraphSidecar` (carrusel).

---

## Limitaciones conocidas

- **Listas de seguidores y seguidos.** El script incluye una opción para obtener los nombres de usuario de seguidores y seguidos (`INCLUIR_SEGUIDORES` e `INCLUIR_SEGUIDOS`), pero está **desactivada por defecto**. En las pruebas realizadas con Instaloader 4.15.4, Instagram devolvió listas vacías sin error, incluso con la sesión válida, con un perfil público que la cuenta sigue y también en la propia cuenta, donde el conteo indicaba que sí había datos. Por eso el proyecto registra únicamente los **conteos** (`seguidores` y `seguidos`). El script `diagnostico_seguidores.py` permite reproducir la comprobación.
- **Cookies de Brave.** Algunas versiones de navegadores basados en Chromium cifran las cookies de forma que `browser-cookie3` puede no descifrarlas. En ese caso hay que copiar las cookies manualmente desde las herramientas de desarrollador del navegador.
- **Límites de Instagram.** Instagram puede limitar o bloquear temporalmente la cuenta si detecta demasiadas peticiones. El script incluye pausas aleatorias y manejo de errores, pero no elimina ese riesgo.
- **Dependencia de Instagram.** Instagram cambia con frecuencia su funcionamiento interno, por lo que el código puede dejar de funcionar en cualquier momento.

## Seguridad y privacidad

- **Nunca subas** al repositorio archivos de sesión (`session-*`), cookies, contraseñas ni capturas que las muestren. La cookie `sessionid` equivale a la contraseña de la cuenta.
- El archivo `.gitignore` está configurado para excluirlos. Revisa siempre con `git status` antes de hacer commit.
- La carpeta `resultados/` contiene solo una **muestra reducida** de ejemplo. Los resultados completos incluyen contenido y nombres de usuario de terceros y no se publican.
- Si subes una credencial por error, borrarla en un commit posterior no basta, porque queda en el historial. Cierra la sesión de esa cuenta en Instagram o cambia su contraseña de inmediato.

## Aviso ético y legal

Este proyecto se desarrolló con fines **académicos y de aprendizaje** sobre extracción y procesamiento de datos.

- Los términos de uso de Instagram restringen la recolección automatizada de datos. Ejecutar este código puede provocar la limitación o suspensión de la cuenta utilizada. Usa siempre una cuenta alternativa y asume ese riesgo bajo tu responsabilidad.
- Consulta únicamente información **pública** y limita el volumen de datos a lo estrictamente necesario.
- Los datos de otras personas (textos, usuarios, menciones) son datos personales: no los publiques, no los vendas y no los uses para perfilar, acosar o identificar a nadie.
- Para un uso profesional o a gran escala, la vía correcta es la [API oficial de Instagram](https://developers.facebook.com/docs/instagram-platform).

## Solución de problemas

| Problema | Posible causa y solución |
|---|---|
| `No hay sesión guardada` | Ejecuta una vez con `IMPORTAR_DESDE_BRAVE = True`. |
| `No se encontró la cookie 'sessionid'` | Inicia sesión en instagram.com desde Brave y cierra el navegador antes de importar. |
| `La sesión no es válida` | Las cookies expiraron. Vuelve a iniciar sesión en Brave y reimporta. |
| `TooManyRequestsException` | Instagram limitó las peticiones. Espera varias horas antes de reintentar. |
| `PrivateProfileNotFollowedException` | El perfil es privado y la cuenta alternativa no lo sigue. |
| Listas de seguidores vacías | Limitación conocida, ver [Limitaciones conocidas](#limitaciones-conocidas). |

## Tecnologías

- Python 3
- [Instaloader](https://instaloader.github.io/)
- [browser-cookie3](https://github.com/borisbabic/browser_cookie3)

## Autor

Proyecto desarrollado por **TU NOMBRE** como práctica académica de web scraping.
Curso: **NOMBRE DEL CURSO** · Institución: **NOMBRE DE LA INSTITUCIÓN**
