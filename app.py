import os
import sqlite3
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory


ROOT_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = ROOT_DIR / "frontend"
DATA_DIR = Path(os.environ.get("DATA_DIR", ROOT_DIR / "data"))
DB_PATH = DATA_DIR / "alolegliz.db"

app = Flask(__name__)

FEMOL_COLUMNS = [
    "type_femol", "nom", "date_creation", "effectif", "adresse", "departement",
    "ville", "email", "telephone", "registre_mdc",
    "resp1_nom", "resp1_fonction", "resp1_tel",
    "resp2_nom", "resp2_fonction", "resp2_tel",
    "resp3_nom", "resp3_fonction", "resp3_tel",
    "base_doctrinale", "fondation", "fondation_details", "programme_annuel",
]


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
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
                created_at TEXT DEFAULT (datetime('now'))
            )
            """
        )


init_db()


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


@app.get("/icons/<path:filename>")
def icons(filename):
    return send_from_directory(FRONTEND_DIR / "icons", filename)


@app.get("/enregistrement.html")
def enregistrement():
    return send_from_directory(FRONTEND_DIR, "enregistrement.html")


@app.get("/images/<path:filename>")
def images(filename):
    # Sert d'abord frontend/images/, sinon retombe sur la racine du projet
    # (les fichiers Logo.jpeg, manisfeste.jpeg, etc. sont à la racine).
    if (FRONTEND_DIR / "images" / filename).is_file():
        return send_from_directory(FRONTEND_DIR / "images", filename)
    return send_from_directory(ROOT_DIR, filename)


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


@app.get("/api/femol")
def list_femol():
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM femol ORDER BY id DESC").fetchall()
    return jsonify([dict(row) for row in rows])


if __name__ == "__main__":
    app.run(
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "5000")),
        debug=os.environ.get("FLASK_DEBUG", "").lower() in {"1", "true", "yes"},
    )
