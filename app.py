import os, io, csv, sqlite3
from datetime import datetime, timedelta
from functools import wraps
from flask import (Flask, g, render_template, request, redirect, url_for,
                   session, flash, send_file)
from werkzeug.security import generate_password_hash, check_password_hash

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, "citas.db")

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "cambia-esta-clave-secreta")

HORA_APERTURA = 7     # 07:00
HORA_CIERRE = 19      # 19:00
UTC_OFFSET = -5       # Colombia
ESTADOS = ["Programada", "Completada", "Cancelada"]
DURACIONES = [15, 30, 45, 60]


def ahora():
    return datetime.utcnow() + timedelta(hours=UTC_OFFSET)


# ---------- Base de datos ----------
def dict_factory(cur, row):
    return {c[0]: row[i] for i, c in enumerate(cur.description)}


def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB)
        g.db.row_factory = dict_factory
        g.db.execute("PRAGMA foreign_keys=ON")
    return g.db


@app.teardown_appcontext
def cerrar_db(_):
    conn = g.pop("db", None)
    if conn:
        conn.close()


def q(sql, args=(), one=False):
    rows = db().execute(sql, args).fetchall()
    if one:
        return rows[0] if rows else None
    return rows


def ex(sql, args=()):
    cur = db().execute(sql, args)
    db().commit()
    return cur.lastrowid


def init_db():
    conn = sqlite3.connect(DB)
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS usuarios(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        usuario TEXT UNIQUE NOT NULL,
        clave TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS pacientes(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        documento TEXT UNIQUE NOT NULL,
        nombre TEXT NOT NULL,
        telefono TEXT,
        email TEXT,
        nacimiento TEXT,
        activo INTEGER DEFAULT 1);
    CREATE TABLE IF NOT EXISTS profesionales(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nombre TEXT NOT NULL,
        especialidad TEXT NOT NULL,
        telefono TEXT,
        email TEXT,
        activo INTEGER DEFAULT 1);
    CREATE TABLE IF NOT EXISTS citas(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        paciente_id INTEGER NOT NULL REFERENCES pacientes(id),
        profesional_id INTEGER NOT NULL REFERENCES profesionales(id),
        inicio TEXT NOT NULL,
        duracion INTEGER NOT NULL DEFAULT 30,
        motivo TEXT,
        estado TEXT NOT NULL DEFAULT 'Programada',
        creada TEXT);
    """)
    if not conn.execute("SELECT 1 FROM usuarios").fetchone():
        conn.execute("INSERT INTO usuarios(usuario, clave) VALUES(?,?)",
                     ("admin", generate_password_hash("admin123")))
        conn.executemany(
            "INSERT INTO profesionales(nombre, especialidad, telefono, email) VALUES(?,?,?,?)",
            [("Dra. Laura Mejía", "Medicina general", "3001112233", "laura@clinica.co"),
             ("Dr. Carlos Restrepo", "Pediatría", "3004445566", "carlos@clinica.co"),
             ("Dra. Ana Gómez", "Odontología", "3007778899", "ana@clinica.co")])
        conn.executemany(
            "INSERT INTO pacientes(documento, nombre, telefono, email, nacimiento) VALUES(?,?,?,?,?)",
            [("1001001001", "Jorge Arteaga", "3101234567", "jorge@correo.com", "1995-04-12"),
             ("1002002002", "María Fernanda Ruiz", "3119876543", "maria@correo.com", "1988-09-30"),
             ("1003003003", "Luis Pérez", "3125550000", "", "2001-01-15")])
    conn.commit()
    conn.close()


init_db()


# ---------- Utilidades ----------
def login_required(f):
    @wraps(f)
    def wrapper(*a, **kw):
        if "usuario" not in session:
            return redirect(url_for("login"))
        return f(*a, **kw)
    return wrapper


def parse_dt(s):
    return datetime.strptime(s, "%Y-%m-%d %H:%M")


def validar_cita(paciente_id, prof_id, fecha, hora, dur, cita_id=None):
    """Devuelve (inicio_str, error). Evita cruces de horario."""
    try:
        ini = parse_dt(f"{fecha} {hora}")
        dur = int(dur)
    except (ValueError, TypeError):
        return None, "La fecha o la hora no son válidas."
    if dur not in DURACIONES:
        return None, "La duración no es válida."
    fin = ini + timedelta(minutes=dur)
    if ini < ahora():
        return None, "No se puede programar una cita en una fecha u hora pasada."
    apertura = ini.replace(hour=HORA_APERTURA, minute=0)
    cierre = ini.replace(hour=HORA_CIERRE, minute=0)
    if ini < apertura or fin > cierre:
        return None, f"El horario de atención es de {HORA_APERTURA:02d}:00 a {HORA_CIERRE:02d}:00."
    otras = q("""SELECT * FROM citas WHERE estado='Programada'
                 AND substr(inicio,1,10)=? AND id!=?
                 AND (profesional_id=? OR paciente_id=?)""",
              (fecha, cita_id or 0, prof_id, paciente_id))
    for o in otras:
        oi = parse_dt(o["inicio"])
        of = oi + timedelta(minutes=o["duracion"])
        if oi < fin and of > ini:
            quien = "El profesional" if o["profesional_id"] == int(prof_id) else "El paciente"
            return None, (f"{quien} ya tiene una cita de "
                          f"{oi:%H:%M} a {of:%H:%M} que se cruza con este horario.")
    return ini.strftime("%Y-%m-%d %H:%M"), None


SELECT_CITAS = """SELECT c.*, p.nombre AS paciente, p.documento,
                  f.nombre AS profesional, f.especialidad
                  FROM citas c
                  JOIN pacientes p ON p.id=c.paciente_id
                  JOIN profesionales f ON f.id=c.profesional_id"""


def consultar_citas(args):
    where, params = [], []
    if args.get("desde"):
        where.append("substr(c.inicio,1,10) >= ?"); params.append(args["desde"])
    if args.get("hasta"):
        where.append("substr(c.inicio,1,10) <= ?"); params.append(args["hasta"])
    if args.get("estado"):
        where.append("c.estado = ?"); params.append(args["estado"])
    if args.get("profesional"):
        where.append("c.profesional_id = ?"); params.append(args["profesional"])
    if args.get("q"):
        where.append("(p.nombre LIKE ? OR p.documento LIKE ?)")
        params += [f"%{args['q']}%"] * 2
    sql = SELECT_CITAS + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY c.inicio"
    return q(sql, params)


@app.context_processor
def globales():
    return dict(DURACIONES=DURACIONES, ESTADOS=ESTADOS,
                hoy=ahora().strftime("%Y-%m-%d"))


# ---------- Acceso ----------
@app.route("/")
def index():
    return redirect(url_for("dashboard" if "usuario" in session else "login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        u = q("SELECT * FROM usuarios WHERE usuario=?",
              (request.form.get("usuario", "").strip(),), one=True)
        if u and check_password_hash(u["clave"], request.form.get("clave", "")):
            session["usuario"] = u["usuario"]
            flash("Sesión iniciada correctamente.", "ok")
            return redirect(url_for("dashboard"))
        flash("Usuario o contraseña incorrectos.", "error")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------- Panel ----------
@app.route("/dashboard")
@login_required
def dashboard():
    hoy = ahora().strftime("%Y-%m-%d")
    stats = dict(
        pacientes=q("SELECT COUNT(*) n FROM pacientes WHERE activo=1", one=True)["n"],
        profesionales=q("SELECT COUNT(*) n FROM profesionales WHERE activo=1", one=True)["n"],
        hoy=q("SELECT COUNT(*) n FROM citas WHERE substr(inicio,1,10)=? AND estado='Programada'",
              (hoy,), one=True)["n"],
        canceladas=q("SELECT COUNT(*) n FROM citas WHERE substr(inicio,1,10)=? AND estado='Cancelada'",
                     (hoy,), one=True)["n"],
    )
    proximas = q(SELECT_CITAS + " WHERE c.estado='Programada' AND c.inicio >= ? ORDER BY c.inicio LIMIT 6",
                 (ahora().strftime("%Y-%m-%d %H:%M"),))
    return render_template("dashboard.html", stats=stats, proximas=proximas, activo="dashboard")


# ---------- Pacientes ----------
@app.route("/pacientes")
@login_required
def pacientes():
    b = request.args.get("q", "").strip()
    rows = q("""SELECT * FROM pacientes WHERE activo=1 AND (nombre LIKE ? OR documento LIKE ?)
                ORDER BY nombre""", (f"%{b}%", f"%{b}%"))
    return render_template("pacientes.html", rows=rows, b=b, activo="pacientes")


@app.route("/pacientes/guardar", methods=["POST"])
@login_required
def pacientes_guardar():
    f = request.form
    datos = (f["documento"].strip(), f["nombre"].strip(), f.get("telefono", "").strip(),
             f.get("email", "").strip(), f.get("nacimiento", ""))
    if not datos[0] or not datos[1]:
        flash("Documento y nombre son obligatorios.", "error")
        return redirect(url_for("pacientes"))
    try:
        if f.get("id"):
            ex("UPDATE pacientes SET documento=?, nombre=?, telefono=?, email=?, nacimiento=? WHERE id=?",
               datos + (f["id"],))
            flash("Paciente actualizado correctamente.", "ok")
        else:
            ex("INSERT INTO pacientes(documento,nombre,telefono,email,nacimiento) VALUES(?,?,?,?,?)", datos)
            flash("Paciente registrado correctamente.", "ok")
    except sqlite3.IntegrityError:
        flash("Ya existe un paciente con ese documento.", "error")
    return redirect(url_for("pacientes"))


@app.route("/pacientes/<int:pid>/eliminar", methods=["POST"])
@login_required
def pacientes_eliminar(pid):
    n = q("SELECT COUNT(*) n FROM citas WHERE paciente_id=? AND estado='Programada'", (pid,), one=True)["n"]
    if n:
        flash("El paciente tiene citas programadas. Cancélalas antes de eliminarlo.", "error")
    else:
        ex("UPDATE pacientes SET activo=0 WHERE id=?", (pid,))
        flash("Paciente eliminado correctamente.", "ok")
    return redirect(url_for("pacientes"))


# ---------- Profesionales ----------
@app.route("/profesionales")
@login_required
def profesionales():
    b = request.args.get("q", "").strip()
    rows = q("""SELECT * FROM profesionales WHERE activo=1 AND (nombre LIKE ? OR especialidad LIKE ?)
                ORDER BY nombre""", (f"%{b}%", f"%{b}%"))
    return render_template("profesionales.html", rows=rows, b=b, activo="profesionales")


@app.route("/profesionales/guardar", methods=["POST"])
@login_required
def profesionales_guardar():
    f = request.form
    datos = (f["nombre"].strip(), f["especialidad"].strip(),
             f.get("telefono", "").strip(), f.get("email", "").strip())
    if not datos[0] or not datos[1]:
        flash("Nombre y especialidad son obligatorios.", "error")
        return redirect(url_for("profesionales"))
    if f.get("id"):
        ex("UPDATE profesionales SET nombre=?, especialidad=?, telefono=?, email=? WHERE id=?",
           datos + (f["id"],))
        flash("Profesional actualizado correctamente.", "ok")
    else:
        ex("INSERT INTO profesionales(nombre,especialidad,telefono,email) VALUES(?,?,?,?)", datos)
        flash("Profesional registrado correctamente.", "ok")
    return redirect(url_for("profesionales"))


@app.route("/profesionales/<int:pid>/eliminar", methods=["POST"])
@login_required
def profesionales_eliminar(pid):
    n = q("SELECT COUNT(*) n FROM citas WHERE profesional_id=? AND estado='Programada'", (pid,), one=True)["n"]
    if n:
        flash("El profesional tiene citas programadas. Cancélalas o reprográmalas primero.", "error")
    else:
        ex("UPDATE profesionales SET activo=0 WHERE id=?", (pid,))
        flash("Profesional eliminado correctamente.", "ok")
    return redirect(url_for("profesionales"))


# ---------- Citas ----------
@app.route("/citas")
@login_required
def citas():
    rows = consultar_citas(request.args)
    return render_template(
        "citas.html", rows=rows, f=request.args, activo="citas",
        lista_pacientes=q("SELECT id, nombre, documento FROM pacientes WHERE activo=1 ORDER BY nombre"),
        lista_prof=q("SELECT id, nombre, especialidad FROM profesionales WHERE activo=1 ORDER BY nombre"))


@app.route("/citas/guardar", methods=["POST"])
@login_required
def citas_guardar():
    f = request.form
    cita_id = f.get("id") or None
    if cita_id:
        actual = q("SELECT estado FROM citas WHERE id=?", (cita_id,), one=True)
        if not actual or actual["estado"] != "Programada":
            flash("Solo se pueden reprogramar citas en estado Programada.", "error")
            return redirect(url_for("citas"))
    inicio, error = validar_cita(f["paciente_id"], f["profesional_id"], f["fecha"],
                                 f["hora"], f["duracion"], cita_id)
    if error:
        flash(error, "error")
        return redirect(request.referrer or url_for("citas"))
    if cita_id:
        ex("""UPDATE citas SET paciente_id=?, profesional_id=?, inicio=?, duracion=?, motivo=?
              WHERE id=?""", (f["paciente_id"], f["profesional_id"], inicio,
                              f["duracion"], f.get("motivo", "").strip(), cita_id))
        flash("Cita reprogramada correctamente.", "ok")
    else:
        ex("""INSERT INTO citas(paciente_id, profesional_id, inicio, duracion, motivo, creada)
              VALUES(?,?,?,?,?,?)""", (f["paciente_id"], f["profesional_id"], inicio,
                                       f["duracion"], f.get("motivo", "").strip(),
                                       ahora().strftime("%Y-%m-%d %H:%M")))
        flash("Cita programada correctamente.", "ok")
    return redirect(url_for("citas"))


@app.route("/citas/<int:cid>/<accion>", methods=["POST"])
@login_required
def citas_estado(cid, accion):
    nuevo = {"cancelar": "Cancelada", "completar": "Completada"}.get(accion)
    c = q("SELECT estado FROM citas WHERE id=?", (cid,), one=True)
    if not nuevo or not c:
        flash("La cita no existe.", "error")
    elif c["estado"] != "Programada":
        flash("Solo se pueden modificar citas en estado Programada.", "error")
    else:
        ex("UPDATE citas SET estado=? WHERE id=?", (nuevo, cid))
        flash("Cita cancelada correctamente." if nuevo == "Cancelada"
              else "Cita marcada como completada.", "ok")
    return redirect(request.referrer or url_for("citas"))


# ---------- Reportes ----------
@app.route("/reportes")
@login_required
def reportes():
    rows = consultar_citas(request.args)
    resumen = {e: sum(1 for r in rows if r["estado"] == e) for e in ESTADOS}
    por_prof = {}
    for r in rows:
        por_prof[r["profesional"]] = por_prof.get(r["profesional"], 0) + 1
    return render_template("reportes.html", rows=rows, f=request.args, resumen=resumen,
                           por_prof=sorted(por_prof.items(), key=lambda x: -x[1]),
                           lista_prof=q("SELECT id, nombre FROM profesionales WHERE activo=1 ORDER BY nombre"),
                           activo="reportes")


@app.route("/reportes/csv")
@login_required
def reportes_csv():
    rows = consultar_citas(request.args)
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["Fecha", "Hora", "Duración (min)", "Paciente", "Documento",
                "Profesional", "Especialidad", "Motivo", "Estado"])
    for r in rows:
        w.writerow([r["inicio"][:10], r["inicio"][11:], r["duracion"], r["paciente"],
                    r["documento"], r["profesional"], r["especialidad"], r["motivo"], r["estado"]])
    data = io.BytesIO(("\ufeff" + out.getvalue()).encode("utf-8"))
    return send_file(data, mimetype="text/csv", as_attachment=True, download_name="reporte_citas.csv")


@app.route("/reportes/pdf")
@login_required
def reportes_pdf():
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle

    rows = consultar_citas(request.args)
    st = getSampleStyleSheet()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4), leftMargin=1.5*cm, rightMargin=1.5*cm,
                            topMargin=1.5*cm, bottomMargin=1.5*cm, title="Reporte de citas")
    filtros = []
    for k, t in (("desde", "Desde"), ("hasta", "Hasta"), ("estado", "Estado")):
        if request.args.get(k):
            filtros.append(f"{t}: {request.args[k]}")
    if request.args.get("profesional"):
        p = q("SELECT nombre FROM profesionales WHERE id=?", (request.args["profesional"],), one=True)
        if p:
            filtros.append(f"Profesional: {p['nombre']}")
    el = [Paragraph("Reporte de citas médicas", st["Title"]),
          Paragraph(" | ".join(filtros) or "Sin filtros", st["Normal"]),
          Paragraph("Generado: " + ahora().strftime("%Y-%m-%d %H:%M") +
                    f" | Total de citas: {len(rows)}", st["Normal"]),
          Spacer(1, 12)]
    data = [["Fecha", "Hora", "Min", "Paciente", "Profesional", "Motivo", "Estado"]]
    for r in rows:
        data.append([r["inicio"][:10], r["inicio"][11:], r["duracion"], r["paciente"],
                     r["profesional"], (r["motivo"] or "")[:40], r["estado"]])
    t = Table(data, repeatRows=1, colWidths=[2.4*cm, 1.8*cm, 1.3*cm, 5.5*cm, 5.5*cm, 6*cm, 2.8*cm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#07140f")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f1f5f9")]),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#cbd5e1")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
    el.append(t)
    doc.build(el)
    buf.seek(0)
    return send_file(buf, mimetype="application/pdf", as_attachment=True,
                     download_name="reporte_citas.pdf")


if __name__ == "__main__":
    app.run(debug=True)
