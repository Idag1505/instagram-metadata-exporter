"""
Interfaz web (Streamlit) del proyecto.

Ejecutar:   streamlit run app.py
"""
import os
from pathlib import Path

import pandas as pd
import streamlit as st

import nucleo as n

st.set_page_config(page_title="Instagram Metadata + Big Five", page_icon="📊", layout="wide")

ETIQUETAS_RASGOS = {
    "apertura": "Apertura a la experiencia",
    "responsabilidad": "Responsabilidad",
    "extraversion": "Extraversión",
    "amabilidad": "Amabilidad",
    "neuroticismo": "Neuroticismo",
}


# ------------------------------- Barra lateral -------------------------------
with st.sidebar:
    st.header("⚙️ Configuración")

    cuenta = st.text_input(
        "Cuenta alternativa (sesión de Instagram)",
        value=os.environ.get("IG_CUENTA", ""),
        help="Cuenta que se usa para consultar. No uses tu cuenta personal principal.",
    )

    if n.sesion_existe(cuenta):
        st.success("Sesión guardada encontrada")
    else:
        st.warning("No hay sesión guardada para esta cuenta")

    with st.expander("Importar sesión desde Brave"):
        st.caption(
            "1) Inicia sesión en instagram.com con la cuenta alternativa en Brave.  \n"
            "2) **Cierra Brave por completo.**  \n"
            "3) Pulsa el botón."
        )
        if st.button("Importar sesión"):
            try:
                usuario_sesion = n.importar_sesion_brave(cuenta)
                st.success(f"Sesión de '{usuario_sesion}' guardada.")
            except Exception as error:  # noqa: BLE001 - se muestra el motivo al usuario
                st.error(str(error))

    st.divider()
    if n.api_key_configurada():
        st.success("Clave de API detectada en .env")
    else:
        st.error("Falta GEMINI_API_KEY en el archivo .env")
    st.caption(f"Modelo: `{n.modelo_configurado()}`")
    st.caption("La clave nunca se muestra ni se guarda en los resultados.")


# --------------------------------- Cabecera ---------------------------------
st.title("📊 Instagram Metadata + análisis Big Five")
st.warning(
    "**Uso académico y responsable.** Instagram restringe las cuentas que consultan de forma "
    "automatizada: usa una cuenta alternativa, límites bajos y no repitas la extracción "
    "seguida. Si ves errores `429` o `feedback_required`, detente y espera varios días. "
    "Los datos extraídos incluyen contenido de terceros: no los publiques."
)

# ------------------------------- Formulario -------------------------------
st.subheader("1. Extraer datos")

with st.form("formulario_extraccion"):
    usuario_objetivo = st.text_input("Usuario de Instagram a consultar", placeholder="nombre_de_usuario")

    c1, c2, c3, c4 = st.columns(4)
    limite = c1.number_input("Publicaciones", min_value=1, max_value=50, value=10)
    fotos = c2.checkbox("Descargar fotos", value=True)
    videos = c3.checkbox("Descargar videos / reels", value=True)
    comentarios = c4.checkbox("Obtener comentarios", value=False,
                              help="Cada publicación requiere peticiones extra: más riesgo de bloqueo.")
    limite_comentarios = st.slider("Comentarios por publicación", 5, 100, 20, disabled=not comentarios)

    enviar = st.form_submit_button("Extraer datos", type="primary")

if enviar:
    barra = st.progress(0.0, text="Preparando...")

    def progreso(fraccion: float, mensaje: str) -> None:
        barra.progress(min(max(fraccion, 0.0), 1.0), text=mensaje)

    try:
        opciones = n.Opciones(
            limite_publicaciones=int(limite),
            fotos=fotos,
            videos=videos,
            comentarios=comentarios,
            limite_comentarios=int(limite_comentarios),
        )
        resultado = n.ejecutar_extraccion(usuario_objetivo, cuenta, opciones, progreso)
        st.session_state["usuario_activo"] = resultado["perfil"]["usuario"]

        if resultado["estado"] == "completo":
            st.success(f"Extracción completa: {len(resultado['publicaciones'])} publicaciones.")
        else:
            st.warning(f"Extracción incompleta: se guardaron {len(resultado['publicaciones'])} publicaciones.")
        for aviso in resultado["avisos"]:
            st.warning(aviso)
    except (ValueError, RuntimeError) as error:
        st.error(str(error))
    finally:
        barra.empty()

# ----------------------------- Selección de datos -----------------------------
guardados = n.listar_usuarios_guardados()
if not guardados:
    st.info("Aún no hay datos guardados. Extrae un perfil para empezar.")
    st.stop()

activo = st.session_state.get("usuario_activo")
indice = guardados.index(activo) if activo in guardados else 0
st.divider()
usuario = st.selectbox("Datos guardados", guardados, index=indice)
datos = n.cargar_datos_guardados(usuario)
carpeta = n.carpeta_usuario(usuario)
perfil = datos["perfil"]
publicaciones = datos["publicaciones"]

if datos.get("estado") != "completo":
    st.warning("Estos datos corresponden a una extracción incompleta.")

tab_datos, tab_media, tab_comentarios, tab_bigfive = st.tabs(
    ["📋 Perfil y métricas", "🖼️ Multimedia", "💬 Comentarios", "🧠 Big Five"]
)

# --------------------------------- Pestaña datos ---------------------------------
with tab_datos:
    st.subheader(f"@{perfil['usuario']}  {'✔️' if perfil.get('es_verificado') else ''}")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Seguidores", f"{perfil['seguidores']:,}")
    m2.metric("Seguidos", f"{perfil['seguidos']:,}")
    m3.metric("Publicaciones (total)", f"{perfil['total_publicaciones']:,}")
    m4.metric("Extraídas", len(publicaciones))

    st.markdown(f"**Nombre:** {perfil.get('nombre_completo') or '—'}")
    st.markdown("**Biografía:**")
    st.write(perfil.get("biografia") or "—")
    if perfil.get("url_externa"):
        st.markdown(f"**Enlace:** {perfil['url_externa']}")

    if publicaciones:
        st.subheader("Interacciones por publicación")
        df = pd.DataFrame(publicaciones)
        tabla = df[["numero_post", "fecha_publicacion", "tipo", "likes", "comentarios",
                    "interacciones", "vistas_video"]].copy()
        tabla["hashtags"] = df["hashtags"].apply(len)
        st.dataframe(tabla, hide_index=True)
        st.caption("Likes = -1 significa que el autor oculta el número de 'me gusta'.")

        grafico = df.set_index("numero_post")[["likes", "comentarios"]].clip(lower=0)
        st.bar_chart(grafico)

        if datos.get("metricas"):
            st.subheader("Métricas agregadas")
            st.json(datos["metricas"], expanded=False)

# -------------------------------- Pestaña multimedia --------------------------------
with tab_media:
    con_medios = [p for p in publicaciones if p.get("archivos_imagen") or p.get("archivos_video")]
    if not con_medios:
        st.info("No hay archivos descargados. Activa 'Descargar fotos' o 'videos / reels' al extraer.")
    for pub in con_medios[:30]:
        st.markdown(f"**#{pub['numero_post']} · {pub['tipo']} · {pub['fecha_publicacion'][:10]}** "
                    f"· ❤️ {pub['likes']} · 💬 {pub['comentarios']}")
        columnas = st.columns(3)
        for i, nombre in enumerate(pub.get("archivos_imagen", [])):
            ruta = carpeta / nombre
            if ruta.exists():
                columnas[i % 3].image(str(ruta))
        for nombre in pub.get("archivos_video", []):
            ruta = carpeta / nombre
            if ruta.exists():
                st.video(str(ruta))
        if pub.get("descripcion"):
            st.caption(pub["descripcion"][:300])
        st.divider()

# ------------------------------- Pestaña comentarios -------------------------------
with tab_comentarios:
    con_comentarios = [p for p in publicaciones if p.get("comentarios_lista")]
    if not con_comentarios:
        st.info("No se obtuvieron comentarios. Activa 'Obtener comentarios' al extraer.")
    else:
        st.caption(
            "Los usuarios de quienes comentan se muestran seudonimizados y los textos "
            "anonimizados (sin @menciones, enlaces, correos ni teléfonos)."
        )
    for pub in con_comentarios:
        with st.expander(f"Publicación #{pub['numero_post']} — {len(pub['comentarios_lista'])} comentarios"):
            st.dataframe(pd.DataFrame(pub["comentarios_lista"]), hide_index=True)

# ------------------------------- Pestaña Big Five -------------------------------
with tab_bigfive:
    st.markdown(
        "Estimación **exploratoria** basada en las descripciones de las publicaciones y en "
        "métricas agregadas de comportamiento. No usa los comentarios de otras personas ni "
        "hace nuevas peticiones a Instagram. Los textos se envían **anonimizados** a la API."
    )
    consentimiento = st.checkbox(
        "Confirmo que es mi propia cuenta o que tengo el consentimiento expreso de la persona "
        "para analizar su contenido."
    )

    if st.button("Analizar Big Five", type="primary", disabled=not consentimiento):
        with st.spinner("Consultando al modelo..."):
            try:
                n.analizar_bigfive(usuario)
                st.success("Análisis completado.")
            except (ValueError, RuntimeError) as error:
                st.error(str(error))

    analisis = n.cargar_analisis_guardado(usuario)
    if analisis:
        st.caption(analisis["aviso"])
        resultado = analisis["resultado"]
        rasgos = resultado.get("rasgos", {})

        puntuaciones = pd.DataFrame(
            {"Puntuación (1-5)": [rasgos.get(r, {}).get("puntuacion", 0) for r in n.RASGOS]},
            index=[ETIQUETAS_RASGOS[r] for r in n.RASGOS],
        )
        st.bar_chart(puntuaciones)

        for rasgo in n.RASGOS:
            info = rasgos.get(rasgo, {})
            with st.expander(
                f"{ETIQUETAS_RASGOS[rasgo]} — {info.get('puntuacion', '?')}/5 "
                f"(confianza {info.get('confianza', '?')})"
            ):
                st.write(info.get("evidencia", ""))

        st.markdown("**Resumen**")
        st.write(resultado.get("resumen", ""))
        st.markdown("**Limitaciones**")
        st.write(resultado.get("limitaciones", ""))
        st.caption(f"Modelo: {analisis['modelo']} · textos analizados: {analisis['publicaciones_analizadas']}")
