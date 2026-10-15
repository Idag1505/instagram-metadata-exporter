# instagram-metadata-exporter

# Instagram Metadata Exporter

Proyecto académico en Python para procesar metadatos
obtenidos mediante Instaloader y exportarlos a JSON.

## Funcionalidades

- Procesamiento de archivos JSON comprimidos (.json.xz).
- Extracción de fechas y descripciones.
- Recuperación de likes y comentarios cuando están disponibles.
- Generación de enlaces a publicaciones.
- Exportación de hasta 50 registros a un único archivo JSON.

## Requisitos

- Python 3.13
- Instaloader 4.15.4 para la extracción.

## Uso

```bash
python3 exportar_publicaciones.py \
  --input-dir "/ruta/a/los/metadatos" \
  --output publicaciones_instagram.json \
  --limit 50
```

## Privacidad

No se incluyen sesiones, cookies, contraseñas,
archivos descargados ni datos personales reales.

Utilizar únicamente datos a los que se tenga
autorización de acceso y respetar las condiciones
del servicio de origen.

## Limitaciones

Los metadatos disponibles dependen de la versión
de Instaloader y de las respuestas de Instagram.
Los valores no disponibles se representan como null.
