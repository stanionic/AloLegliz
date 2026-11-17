import csv
import hmac
import io
import os
import secrets
import sqlite3
import time
from datetime import timedelta
from functools import wraps
from pathlib import Path

from flask import (
    Flask,
    Response,
    jsonify,
    request,
    send_from_directory,
    session,
)
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename


ROOT_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = ROOT_DIR / "frontend"
DATA_DIR = Path(os.environ.get("DATA_DIR", ROOT_DIR / "data"))
DB_PATH = DATA_DIR / "alolegliz.db"
IMAGES_DIR = ROOT_DIR / "images"
UPLOADS_DIR = DATA_DIR / "uploads"

IMAGE_EXTENSIONS = {".jpeg", ".jpg", ".png", ".webp", ".gif", ".avif"}

FEMOL_COLUMNS = [
    "type_femol", "nom", "date_creation", "effectif", "adresse", "departement",
    "ville", "email", "telephone", "registre_mdc",
    "resp1_nom", "resp1_fonction", "resp1_tel",
    "resp2_nom", "resp2_fonction", "resp2_tel",
    "resp3_nom", "resp3_fonction", "resp3_tel",
    "base_doctrinale", "fondation", "fondation_details", "programme_annuel",
    "pasteur_photo", "pasteur_biographie",
]

EVENTS_COLUMNS = [
    "titre", "description", "date_debut", "date_fin",
    "lieu", "type", "statut", "contact", "image_url",
]

CAROUSEL_COLUMNS = ["image_url", "titre", "description", "position", "actif"]


def list_image_files():
    """Liste les fichiers images présents dans le dossier racine ``images/``."""
    if not IMAGES_DIR.is_dir():
        return []
    return sorted(
        (
            entry.name
            for entry in IMAGES_DIR.iterdir()
            if entry.is_file() and entry.suffix.lower() in IMAGE_EXTENSIONS
        ),
        key=str.lower,
    )


def _pretty_title(filename):
    """Génère un titre lisible à partir d'un nom de fichier."""
    stem = Path(filename).stem
    if stem.lower().startswith("whatsapp"):
        return "Galerie ALO LEGLIZ"
    return stem.replace("_", " ").replace("-", " ").strip().title()


def _seed_carousel(conn):
    """Amorce le carrousel avec toutes les images du dossier ``images/`` (hors logo)."""
    if conn.execute("SELECT COUNT(*) AS c FROM carousel").fetchone()["c"]:
        return
    position = 0
    for name in list_image_files():
        if name.lower().startswith("logo"):
            continue
        conn.execute(
            "INSERT INTO carousel (image_url, titre, position, actif) VALUES (?, ?, ?, 1)",
            (f"/images/{name}", _pretty_title(name), position),
        )
        position += 1


def _ensure_columns(conn, table, columns):
    """Ajoute les colonnes manquantes à une table existante (migration douce)."""
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    for name, definition in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


def _load_secret_key():
    """Clé de session stable : variable d'environnement, sinon fichier persistant."""
    env_key = os.environ.get("SECRET_KEY")
    if env_key:
        return env_key
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    key_file = DATA_DIR / "secret_key"
    if key_file.is_file():
        stored = key_file.read_text(encoding="utf-8").strip()
        if stored:
            return stored
    generated = secrets.token_hex(32)
    key_file.write_text(generated, encoding="utf-8")
    return generated


# ── Compte super administrateur ──
ADMIN_PSEUDO = os.environ.get("ADMIN_PSEUDO", "AdminAloLegliz")
DEFAULT_ADMIN_PASSWORD = "NixonDieudonnee@AloLegliz2026"
ADMIN_PASSWORD_HASH = (
    os.environ.get("ADMIN_PASSWORD_HASH")
    or generate_password_hash(os.environ.get("ADMIN_PASSWORD", DEFAULT_ADMIN_PASSWORD))
)
MAX_LOGIN_ATTEMPTS = 5
LOCKOUT_SECONDS = 300
_login_attempts = {}

app = Flask(__name__)
app.secret_key = _load_secret_key()
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("SESSION_COOKIE_SECURE", "").lower() in {"1", "true", "yes"},
    PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
    MAX_CONTENT_LENGTH=8 * 1024 * 1024,
)


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    with get_db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS femol (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                type_femol TEXT NOT NULL,
                nom TEXT NOT NULL,
                date_creation TEXT,
                effectif TEXT,
                adresse TEXT,
                departement TEXT,
                ville TEXT,
                email TEXT,
                telephone TEXT,
                registre_mdc TEXT,
                resp1_nom TEXT, resp1_fonction TEXT, resp1_tel TEXT,
                resp2_nom TEXT, resp2_fonction TEXT, resp2_tel TEXT,
                resp3_nom TEXT, resp3_fonction TEXT, resp3_tel TEXT,
                base_doctrinale TEXT,
                fondation TEXT,
                fondation_details TEXT,
                programme_annuel TEXT,
                pasteur_photo TEXT,
                pasteur_biographie TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                titre TEXT NOT NULL,
                description TEXT,
                date_debut TEXT,
                date_fin TEXT,
                lieu TEXT,
                type TEXT,
                statut TEXT DEFAULT 'publie',
                contact TEXT,
                image_url TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS carousel (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                image_url TEXT NOT NULL,
                titre TEXT,
                description TEXT,
                position INTEGER DEFAULT 0,
                actif INTEGER DEFAULT 1,
                created_at TEXT DEFAULT (datetime('now'))
            )
            """
        )
        _ensure_columns(
            conn,
            "femol",
            {"pasteur_photo": "TEXT", "pasteur_biographie": "TEXT"},
        )
        _seed_carousel(conn)


init_db()


# ─────────────────────────────────────────────────────────────
#  Authentification
# ─────────────────────────────────────────────────────────────
def _client_ip():
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr or "unknown"


def _lockout_remaining():
    entry = _login_attempts.get(_client_ip())
    if not entry:
        return 0
    count, first = entry
    elapsed = time.time() - first
    if count >= MAX_LOGIN_ATTEMPTS and elapsed < LOCKOUT_SECONDS:
        return int(LOCKOUT_SECONDS - elapsed) + 1
    if elapsed >= LOCKOUT_SECONDS:
        _login_attempts.pop(_client_ip(), None)
    return 0


def _register_failure():
    ip = _client_ip()
    count, first = _login_attempts.get(ip, (0, time.time()))
    if time.time() - first >= LOCKOUT_SECONDS:
        count, first = 0, time.time()
    _login_attempts[ip] = (count + 1, first)


def is_authenticated():
    return bool(session.get("admin"))


def login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not is_authenticated():
            return jsonify(ok=False, error="Otorizasyon obligatwa."), 401
        return view(*args, **kwargs)

    return wrapper


@app.post("/api/admin/login")
def admin_login():
    remaining = _lockout_remaining()
    if remaining:
        return jsonify(ok=False, error=f"Twòp tantativ. Eseye ankò nan {remaining} segonn."), 429

    data = request.get_json(silent=True) or {}
    pseudo = (data.get("pseudo") or "").strip()
    password = data.get("password") or ""

    pseudo_ok = hmac.compare_digest(pseudo, ADMIN_PSEUDO)
    password_ok = check_password_hash(ADMIN_PASSWORD_HASH, password)
    if not (pseudo_ok and password_ok):
        _register_failure()
        return jsonify(ok=False, error="Psedonim oswa modpas la pa kòrèk."), 401

    _login_attempts.pop(_client_ip(), None)
    session.permanent = True
    session["admin"] = True
    session["admin_pseudo"] = ADMIN_PSEUDO
    return jsonify(ok=True, pseudo=ADMIN_PSEUDO)


@app.post("/api/admin/logout")
def admin_logout():
    session.clear()
    return jsonify(ok=True)


@app.get("/api/admin/me")
def admin_me():
    if is_authenticated():
        return jsonify(ok=True, authenticated=True, pseudo=session.get("admin_pseudo"))
    return jsonify(ok=True, authenticated=False)


# ─────────────────────────────────────────────────────────────
#  Pages
# ─────────────────────────────────────────────────────────────
@app.get("/")
def home():
    return send_from_directory(FRONTEND_DIR, "sos-alo-legliz.html")


@app.get("/manifest.json")
def manifest():
    return send_from_directory(FRONTEND_DIR, "manifest.json", mimetype="application/manifest+json")


@app.get("/sw.js")
def service_worker():
    return send_from_directory(FRONTEND_DIR, "sw.js", mimetype="application/javascript")


@app.get("/evenements.html")
def evenements():
    return send_from_directory(FRONTEND_DIR, "evenements.html")


@app.get("/enregistrement.html")
def enregistrement():
    return send_from_directory(FRONTEND_DIR, "enregistrement.html")


@app.get("/organigramme.html")
def organigramme():
    return send_from_directory(FRONTEND_DIR, "organigramme.html")


@app.get("/admin")
@app.get("/admin.html")
def admin_page():
    return send_from_directory(FRONTEND_DIR, "admin.html")


@app.get("/icons/<path:filename>")
def icons(filename):
    return send_from_directory(FRONTEND_DIR / "icons", filename)


@app.get("/images/<path:filename>")
def images(filename):
    # Les images sources sont stockées dans le dossier ``images/`` à la racine.
    return send_from_directory(IMAGES_DIR, filename)


@app.get("/uploads/<path:filename>")
def uploads(filename):
    # Images téléversées par l'admin (stockées sur le disque persistant).
    return send_from_directory(UPLOADS_DIR, filename)


# ─────────────────────────────────────────────────────────────
#  API publique
# ─────────────────────────────────────────────────────────────
@app.post("/api/femol")
def create_femol():
    data = request.get_json(silent=True) or {}
    if not data.get("type_femol") or not data.get("nom"):
        return jsonify(ok=False, error="Le type et le nom de la FEMOL sont obligatoires."), 400
    values = [data.get(column) for column in FEMOL_COLUMNS]
    placeholders = ", ".join("?" for _ in FEMOL_COLUMNS)
    with get_db() as conn:
        cursor = conn.execute(
            f"INSERT INTO femol ({', '.join(FEMOL_COLUMNS)}) VALUES ({placeholders})",
            values,
        )
        new_id = cursor.lastrowid
    return jsonify(ok=True, id=new_id), 201


@app.post("/api/femol/photo")
def upload_femol_photo():
    """Téléverse la photo du pasteur en chef (formulaire public)."""
    file = request.files.get("file")
    if file is None or not file.filename:
        return jsonify(ok=False, error="Pa gen fichye pou telechaje."), 400
    ext = Path(file.filename).suffix.lower()
    if ext not in IMAGE_EXTENSIONS:
        return jsonify(ok=False, error="Fòma fichye a pa sipòte."), 400
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    safe = secure_filename(file.filename) or "photo"
    stored_name = f"{secrets.token_hex(6)}_{safe}"
    file.save(UPLOADS_DIR / stored_name)
    return jsonify(ok=True, image_url=f"/uploads/{stored_name}"), 201


@app.get("/api/femol")
def list_femol():
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM femol ORDER BY id DESC").fetchall()
    return jsonify([dict(row) for row in rows])


@app.get("/api/events")
def public_events():
    """Événements visibles publiquement (hors brouillons)."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM events WHERE COALESCE(statut, 'publie') != 'brouillon' "
            "ORDER BY COALESCE(date_debut, '') ASC"
        ).fetchall()
    return jsonify([dict(row) for row in rows])


@app.get("/api/carousel")
def public_carousel():
    """Images actives du carrousel, dans l'ordre d'affichage."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM carousel WHERE actif = 1 ORDER BY position ASC, id ASC"
        ).fetchall()
    return jsonify([dict(row) for row in rows])


# ─────────────────────────────────────────────────────────────
#  API administrateur — statistiques
# ─────────────────────────────────────────────────────────────
@app.get("/api/admin/stats")
@login_required
def admin_stats():
    with get_db() as conn:
        total_femol = conn.execute("SELECT COUNT(*) AS c FROM femol").fetchone()["c"]
        total_events = conn.execute("SELECT COUNT(*) AS c FROM events").fetchone()["c"]
        by_type = conn.execute(
            "SELECT type_femol, COUNT(*) AS c FROM femol GROUP BY type_femol ORDER BY c DESC"
        ).fetchall()
        by_departement = conn.execute(
            "SELECT COALESCE(departement, '—') AS departement, COUNT(*) AS c "
            "FROM femol GROUP BY departement ORDER BY c DESC"
        ).fetchall()
        recent = conn.execute(
            "SELECT id, nom, type_femol, ville, created_at FROM femol ORDER BY id DESC LIMIT 5"
        ).fetchall()
    return jsonify(
        ok=True,
        total_femol=total_femol,
        total_events=total_events,
        by_type=[dict(row) for row in by_type],
        by_departement=[dict(row) for row in by_departement],
        recent=[dict(row) for row in recent],
    )


# ─────────────────────────────────────────────────────────────
#  API administrateur — FEMOL
# ─────────────────────────────────────────────────────────────
def _int_arg(name, default, minimum=1, maximum=None):
    try:
        value = int(request.args.get(name, default))
    except (TypeError, ValueError):
        value = default
    value = max(value, minimum)
    if maximum is not None:
        value = min(value, maximum)
    return value


@app.get("/api/admin/femol")
@login_required
def admin_list_femol():
    q = (request.args.get("q") or "").strip()
    type_femol = (request.args.get("type") or "").strip()
    departement = (request.args.get("departement") or "").strip()
    page = _int_arg("page", 1)
    per_page = _int_arg("per_page", 25, maximum=100)

    where, params = [], []
    if q:
        where.append(
            "(nom LIKE ? OR ville LIKE ? OR email LIKE ? OR telephone LIKE ? OR resp1_nom LIKE ?)"
        )
        like = f"%{q}%"
        params.extend([like] * 5)
    if type_femol:
        where.append("type_femol = ?")
        params.append(type_femol)
    if departement:
        where.append("departement = ?")
        params.append(departement)
    clause = ("WHERE " + " AND ".join(where)) if where else ""

    with get_db() as conn:
        total = conn.execute(f"SELECT COUNT(*) AS c FROM femol {clause}", params).fetchone()["c"]
        rows = conn.execute(
            f"SELECT * FROM femol {clause} ORDER BY id DESC LIMIT ? OFFSET ?",
            params + [per_page, (page - 1) * per_page],
        ).fetchall()
    return jsonify(
        ok=True,
        total=total,
        page=page,
        per_page=per_page,
        items=[dict(row) for row in rows],
    )


@app.get("/api/admin/femol/export.csv")
@login_required
def admin_export_femol():
    headers = ["id"] + FEMOL_COLUMNS + ["created_at"]
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM femol ORDER BY id DESC").fetchall()
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(headers)
    for row in rows:
        writer.writerow([row[column] for column in headers])
    return Response(
        "\ufeff" + buffer.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=femol_export.csv"},
    )


@app.get("/api/admin/femol/<int:femol_id>")
@login_required
def admin_get_femol(femol_id):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM femol WHERE id = ?", (femol_id,)).fetchone()
    if row is None:
        return jsonify(ok=False, error="Enregistrement pa jwenn."), 404
    return jsonify(ok=True, item=dict(row))


@app.put("/api/admin/femol/<int:femol_id>")
@login_required
def admin_update_femol(femol_id):
    data = request.get_json(silent=True) or {}
    fields = [column for column in FEMOL_COLUMNS if column in data]
    if not fields:
        return jsonify(ok=False, error="Pa gen chanjman pou anrejistre."), 400
    if "type_femol" in fields and not (data.get("type_femol") or "").strip():
        return jsonify(ok=False, error="Tip FEMOL la obligatwa."), 400
    if "nom" in fields and not (data.get("nom") or "").strip():
        return jsonify(ok=False, error="Non an obligatwa."), 400

    assignments = ", ".join(f"{column} = ?" for column in fields)
    values = [data[column] for column in fields] + [femol_id]
    with get_db() as conn:
        cursor = conn.execute(f"UPDATE femol SET {assignments} WHERE id = ?", values)
        if cursor.rowcount == 0:
            return jsonify(ok=False, error="Enregistrement pa jwenn."), 404
    return jsonify(ok=True)


@app.delete("/api/admin/femol/<int:femol_id>")
@login_required
def admin_delete_femol(femol_id):
    with get_db() as conn:
        cursor = conn.execute("DELETE FROM femol WHERE id = ?", (femol_id,))
        if cursor.rowcount == 0:
            return jsonify(ok=False, error="Enregistrement pa jwenn."), 404
    return jsonify(ok=True)


# ─────────────────────────────────────────────────────────────
#  API administrateur — Événements
# ─────────────────────────────────────────────────────────────
@app.get("/api/admin/events")
@login_required
def admin_list_events():
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM events ORDER BY COALESCE(date_debut, '') DESC, id DESC"
        ).fetchall()
    return jsonify(ok=True, items=[dict(row) for row in rows])


@app.post("/api/admin/events")
@login_required
def admin_create_event():
    data = request.get_json(silent=True) or {}
    if not (data.get("titre") or "").strip():
        return jsonify(ok=False, error="Tit evènman an obligatwa."), 400
    values = [data.get(column) for column in EVENTS_COLUMNS]
    placeholders = ", ".join("?" for _ in EVENTS_COLUMNS)
    with get_db() as conn:
        cursor = conn.execute(
            f"INSERT INTO events ({', '.join(EVENTS_COLUMNS)}) VALUES ({placeholders})",
            values,
        )
        new_id = cursor.lastrowid
    return jsonify(ok=True, id=new_id), 201


@app.put("/api/admin/events/<int:event_id>")
@login_required
def admin_update_event(event_id):
    data = request.get_json(silent=True) or {}
    fields = [column for column in EVENTS_COLUMNS if column in data]
    if not fields:
        return jsonify(ok=False, error="Pa gen chanjman pou anrejistre."), 400
    if "titre" in fields and not (data.get("titre") or "").strip():
        return jsonify(ok=False, error="Tit evènman an obligatwa."), 400

    assignments = ", ".join(f"{column} = ?" for column in fields)
    values = [data[column] for column in fields] + [event_id]
    with get_db() as conn:
        cursor = conn.execute(f"UPDATE events SET {assignments} WHERE id = ?", values)
        if cursor.rowcount == 0:
            return jsonify(ok=False, error="Evènman an pa jwenn."), 404
    return jsonify(ok=True)


@app.delete("/api/admin/events/<int:event_id>")
@login_required
def admin_delete_event(event_id):
    with get_db() as conn:
        cursor = conn.execute("DELETE FROM events WHERE id = ?", (event_id,))
        if cursor.rowcount == 0:
            return jsonify(ok=False, error="Evènman an pa jwenn."), 404
    return jsonify(ok=True)


# ─────────────────────────────────────────────────────────────
#  API administrateur — Carrousel
# ─────────────────────────────────────────────────────────────
@app.get("/api/admin/carousel")
@login_required
def admin_list_carousel():
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM carousel ORDER BY position ASC, id ASC"
        ).fetchall()
    return jsonify(ok=True, items=[dict(row) for row in rows])


@app.get("/api/admin/carousel/available")
@login_required
def admin_carousel_available():
    """Images du dossier ``images/`` disponibles pour le carrousel."""
    return jsonify(
        ok=True,
        images=[{"image_url": f"/images/{name}", "name": name} for name in list_image_files()],
    )


@app.post("/api/admin/carousel")
@login_required
def admin_create_carousel():
    data = request.get_json(silent=True) or {}
    image_url = (data.get("image_url") or "").strip()
    if not image_url:
        return jsonify(ok=False, error="Imaj la obligatwa."), 400
    with get_db() as conn:
        max_pos = conn.execute(
            "SELECT COALESCE(MAX(position), -1) AS p FROM carousel"
        ).fetchone()["p"]
        cursor = conn.execute(
            "INSERT INTO carousel (image_url, titre, description, position, actif) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                image_url,
                (data.get("titre") or "").strip() or None,
                (data.get("description") or "").strip() or None,
                max_pos + 1,
                1 if data.get("actif", 1) else 0,
            ),
        )
        new_id = cursor.lastrowid
    return jsonify(ok=True, id=new_id), 201


@app.put("/api/admin/carousel/<int:item_id>")
@login_required
def admin_update_carousel(item_id):
    data = request.get_json(silent=True) or {}
    fields = [column for column in CAROUSEL_COLUMNS if column in data]
    if not fields:
        return jsonify(ok=False, error="Pa gen chanjman pou anrejistre."), 400
    if "image_url" in fields and not (data.get("image_url") or "").strip():
        return jsonify(ok=False, error="Imaj la obligatwa."), 400
    if "actif" in fields:
        data["actif"] = 1 if data["actif"] else 0

    assignments = ", ".join(f"{column} = ?" for column in fields)
    values = [data[column] for column in fields] + [item_id]
    with get_db() as conn:
        cursor = conn.execute(f"UPDATE carousel SET {assignments} WHERE id = ?", values)
        if cursor.rowcount == 0:
            return jsonify(ok=False, error="Eleman an pa jwenn."), 404
    return jsonify(ok=True)


@app.delete("/api/admin/carousel/<int:item_id>")
@login_required
def admin_delete_carousel(item_id):
    with get_db() as conn:
        cursor = conn.execute("DELETE FROM carousel WHERE id = ?", (item_id,))
        if cursor.rowcount == 0:
            return jsonify(ok=False, error="Eleman an pa jwenn."), 404
    return jsonify(ok=True)


@app.post("/api/admin/carousel/reorder")
@login_required
def admin_reorder_carousel():
    data = request.get_json(silent=True) or {}
    order = data.get("order") or []
    if not isinstance(order, list):
        return jsonify(ok=False, error="Lòd la pa valid."), 400
    with get_db() as conn:
        for index, item_id in enumerate(order):
            conn.execute(
                "UPDATE carousel SET position = ? WHERE id = ?", (index, item_id)
            )
    return jsonify(ok=True)


@app.post("/api/admin/carousel/upload")
@login_required
def admin_upload_carousel_image():
    file = request.files.get("file")
    if file is None or not file.filename:
        return jsonify(ok=False, error="Pa gen fichye pou telechaje."), 400
    ext = Path(file.filename).suffix.lower()
    if ext not in IMAGE_EXTENSIONS:
        return jsonify(ok=False, error="Fòma fichye a pa sipòte."), 400
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    safe = secure_filename(file.filename) or "image"
    stored_name = f"{secrets.token_hex(6)}_{safe}"
    file.save(UPLOADS_DIR / stored_name)
    return jsonify(ok=True, image_url=f"/uploads/{stored_name}"), 201


if __name__ == "__main__":
    app.run(
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "5000")),
        debug=os.environ.get("FLASK_DEBUG", "").lower() in {"1", "true", "yes"},
    )
