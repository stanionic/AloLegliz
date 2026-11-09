import os
from pathlib import Path

from flask import Flask, send_from_directory


ROOT_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = ROOT_DIR / "frontend"
app = Flask(__name__)


@app.get("/")
def home():
    return send_from_directory(FRONTEND_DIR, "sos-alo-legliz.html")


if __name__ == "__main__":
    app.run(
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "5000")),
        debug=os.environ.get("FLASK_DEBUG", "").lower() in {"1", "true", "yes"},
    )
