import os
from pathlib import Path

from flask import Flask, send_from_directory


ROOT_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = ROOT_DIR / "frontend"
app = Flask(__name__)


@app.get("/")
def home():
    return send_from_directory(FRONTEND_DIR, "sos-alo-legliz.html")


@app.get("/manifest.json")
def manifest():
    return send_from_directory(FRONTEND_DIR, "manifest.json", mimetype="application/manifest+json")


@app.get("/sw.js")
def service_worker():
    return send_from_directory(FRONTEND_DIR, "sw.js", mimetype="application/javascript")


@app.get("/icons/<path:filename>")
def icons(filename):
    return send_from_directory(FRONTEND_DIR / "icons", filename)


if __name__ == "__main__":
    app.run(
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "5000")),
        debug=os.environ.get("FLASK_DEBUG", "").lower() in {"1", "true", "yes"},
    )
