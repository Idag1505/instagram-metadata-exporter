
import os
import threading
import webbrowser

from flask import (
    Flask, Response, abort, jsonify, redirect, render_template, request, send_file,
    send_from_directory, url_for,
)

import nucleo as n

app = Flask(__name__)

ETIQUETAS_RASGOS = {
    "apertura": "Apertura a la experiencia",
    "responsabilidad": "Responsabilidad",
    "extraversion": "Extraversión",
    "amabilidad": "Amabilidad",
    "neuroticismo": "Neuroticismo",
}

# Opciones del selector de la página de Benford
LISTAS = {"seguidores": "Mis seguidores", "seguidos": "Mis seguidos"}
METRICAS = {
    "seguidores": "Nº de seguidores de cada uno",
    "seguidos": "Nº de seguidos de cada uno",
}

CERROJO = threading.Lock()
ESTADO_INICIAL = {
    "activo": False, "terminado": False, "fraccion": 0.0, "mensaje": "",
    "error": None, "usuario": None, "estado_final": None, "avisos": [],
}
ESTADO = dict(ESTADO_INICIAL)


@app.context_processor
def mensajes():
    return {"msg": request.args.get("msg"), "tipo": request.args.get("tipo", "info")}


def volver(ruta: str, msg: str, tipo: str = "info"):
    return redirect(url_for(ruta, msg=msg, tipo=tipo))


def _actualizar(**cambios):
    with CERROJO:
        ESTADO.update(cambios)


# ----------------------------- Extracción en segundo plano -----------------------------
def _trabajo(usuario: str, cuenta: str, opciones: n.Opciones) -> None:
    def progreso(fraccion: float, mensaje: str) -> None:
        _actualizar(fraccion=fraccion, mensaje=mensaje)

    try:
        resultado = n.ejecutar_extraccion(usuario, cuenta, opciones, progreso)
        _actualizar(
            usuario=resultado["perfil"]["usuario"],
            estado_final=resultado["estado"],
            avisos=resultado["avisos"],
        )
    except (ValueError, RuntimeError) as error:
        _actualizar(error=str(error))
    except Exception as error:  # noqa: BLE001 - se informa en la interfaz
        _actualizar(error=f"Error inesperado: {error}")
    finally:
        _actualizar(activo=False, terminado=True)


# ------------------------------------- Rutas -------------------------------------
@app.route("/")
def index():
    cuenta = os.environ.get("IG_CUENTA", "")
    return render_template(
        "index.html",
        cuenta=cuenta,
        sesion=n.sesion_existe(cuenta),
        api_ok=n.api_key_configurada(),
        modelo=n.modelo_configurado(),
        motor=n.motor_actual(), 
        guardados=n.listar_usuarios_guardados(),
    )


@app.route("/importar", methods=["POST"])
def importar():
    cuenta = request.form.get("cuenta", "").strip()
    try:
        usuario = n.importar_sesion_brave(cuenta)
        return volver("index", f"Sesión de '{usuario}' guardada correctamente.", "ok")
    except Exception as error:  # noqa: BLE001
        return volver("index", str(error), "error")


@app.route("/extraer", methods=["POST"])
def extraer():
    with CERROJO:
        if ESTADO["activo"]:
            return jsonify(ok=False, error="Ya hay una extracción en curso."), 409

    try:
        usuario = n.validar_usuario(request.form.get("usuario", ""))
        opciones = n.Opciones(
            limite_publicaciones=int(request.form.get("limite", 10)),
            fotos=request.form.get("fotos") == "on",
            videos=request.form.get("videos") == "on",
            comentarios=request.form.get("comentarios") == "on",
            limite_comentarios=int(request.form.get("limite_comentarios", 20)),
            seguidores=request.form.get("seguidores") == "on",
            seguidos=request.form.get("seguidos") == "on",
            limite_usuarios=int(request.form.get("limite_usuarios") or 0),   # 0 = lista completa
            stats_redes=request.form.get("stats_redes") == "on",
            limite_stats=int(request.form.get("limite_stats") or 0),         # 0 = todos
            metodo_stats=request.form.get("metodo_stats", "api"),            # "api" | "hover"
        )
    except ValueError as error:
        return jsonify(ok=False, error=str(error)), 400

    cuenta = request.form.get("cuenta", "").strip() or os.environ.get("IG_CUENTA", "").strip()

    if not cuenta:
        return jsonify(
            ok=False,
            error="No hay cuenta alternativa configurada (revisa IG_CUENTA en .env o el campo del formulario).",
        ), 400

    with CERROJO:
        ESTADO.clear()
        ESTADO.update(ESTADO_INICIAL)
        ESTADO.update(activo=True, mensaje="Preparando...")

    threading.Thread(target=_trabajo, args=(usuario, cuenta, opciones), daemon=True).start()
    return jsonify(ok=True)


@app.route("/estado")
def estado():
    with CERROJO:
        return jsonify(dict(ESTADO))


def _cargar(usuario: str):
    try:
        datos = n.cargar_datos_guardados(usuario)
    except ValueError:
        abort(404)
    if not datos:
        abort(404)
    return datos


@app.route("/u/<usuario>")
def ver_usuario(usuario):
    datos = _cargar(usuario)
    carpeta = n.carpeta_usuario(usuario)
    publicaciones = datos["publicaciones"]

    medios = []
    for pub in publicaciones:
        imagenes = [f for f in pub.get("archivos_imagen", []) if (carpeta / f).exists()]
        videos = [f for f in pub.get("archivos_video", []) if (carpeta / f).exists()]
        if imagenes or videos:
            medios.append({"pub": pub, "imagenes": imagenes, "videos": videos})

    return render_template(
        "usuario.html",
        usuario=usuario,
        datos=datos,
        perfil=datos["perfil"],
        publicaciones=publicaciones,
        max_interacciones=max([p["interacciones"] for p in publicaciones] + [1]),
        medios=medios,
        con_comentarios=[p for p in publicaciones if p.get("comentarios_lista")],
        analisis=n.cargar_analisis_guardado(usuario),
        rasgos=n.RASGOS,
        etiquetas=n.ETIQUETAS_RASGOS,
        api_ok=n.api_key_configurada(),
        config_analizar_imagenes=os.environ.get("ANALIZAR_IMAGENES", "0") == "1",
        config_max_imagenes=os.environ.get("MAX_IMAGENES_ANALISIS", "5"),
    )


@app.route("/media/<usuario>/<path:archivo>")
def media(usuario, archivo):
    try:
        carpeta = n.carpeta_usuario(usuario).resolve()
    except ValueError:
        abort(404)
    return send_from_directory(carpeta, archivo)  # protege contra rutas con ../


@app.route("/u/<usuario>/analizar", methods=["POST"])
def analizar(usuario):
    _cargar(usuario)
    if request.form.get("consentimiento") != "on":
        return redirect(url_for("ver_usuario", usuario=usuario, msg="Debes confirmar el consentimiento.", tipo="error") + "#bigfive")
    try:
        n.analizar_bigfive(usuario)
        return redirect(url_for("ver_usuario", usuario=usuario, msg="Análisis completado.", tipo="ok") + "#bigfive")
    except (ValueError, RuntimeError) as error:
        return redirect(url_for("ver_usuario", usuario=usuario, msg=str(error), tipo="error") + "#bigfive")


# ------------------------------------- Ley de Benford -------------------------------------
# ------------------------------------- Ley de Benford -------------------------------------
def _seleccion_benford(usuario: str):
    """Lee la lista y la cifra elegidas (query string) y calcula el análisis."""
    datos = _cargar(usuario)
    lista = request.args.get("lista", "seguidores")
    metrica = request.args.get("metrica", "seguidores")
    if lista not in LISTAS or metrica not in METRICAS:
        abort(400)
    usuarios = (datos.get("redes") or {}).get(lista) or []
    return lista, metrica, usuarios, n.analizar_lista(usuarios, metrica, lista)


@app.route("/u/<usuario>/benford")
def benford(usuario):
    lista, metrica, _, analisis = _seleccion_benford(usuario)
    return render_template(
        "benford.html", usuario=usuario, lista=lista, metrica=metrica,
        analisis=analisis, listas=LISTAS, metricas=METRICAS,
    )


@app.route("/u/<usuario>/benford.png")
def benford_png(usuario):
    lista, metrica, _, analisis = _seleccion_benford(usuario)
    if not analisis["n"]:
        abort(404)
    titulo = f"@{usuario} · {LISTAS[lista]} · {METRICAS[metrica]}"
    return Response(n.grafico_benford_png(analisis, titulo), mimetype="image/png")


@app.route("/u/<usuario>/benford.xlsx")
def benford_excel(usuario):
    lista, metrica, usuarios, analisis = _seleccion_benford(usuario)
    if not analisis["n"]:
        abort(404)
    ruta = n.carpeta_usuario(usuario) / f"benford_{lista}_{metrica}.xlsx"
    n.exportar_excel(usuario, lista, metrica, usuarios, analisis, ruta)
    return send_file(ruta, as_attachment=True, download_name=ruta.name)

if __name__ == "__main__":
    puerto = int(os.environ.get("PUERTO", "8000"))
    threading.Timer(1.0, lambda: webbrowser.open(f"http://127.0.0.1:{puerto}")).start()
    # Solo escucha en tu propio equipo (127.0.0.1): no es accesible desde la red.
    app.run(host="127.0.0.1", port=puerto, debug=False)
