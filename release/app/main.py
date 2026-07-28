import sys
from pathlib import Path

from flask import Flask, jsonify, send_from_directory


APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from config import Config
from db import init_database
from routes import register_routes


def create_app(test_config=None):
    frontend_dir = APP_DIR.parent / "frontend"
    app = Flask(__name__, static_folder=str(frontend_dir), static_url_path="")
    app.config.from_object(Config)
    if test_config:
        app.config.update(test_config)
    Config.validate(app.config)

    init_database(app)
    register_routes(app)

    @app.get("/health")
    def health():
        return jsonify({"status": "ok"})

    @app.get("/")
    def index():
        response = send_from_directory(frontend_dir, "index.html")
        response.headers["Cache-Control"] = "no-store, max-age=0"
        return response

    @app.get("/<path:filename>")
    def static_files(filename):
        return send_from_directory(frontend_dir, filename)

    return app


if __name__ == "__main__":
    application = create_app()
    application.run(host="0.0.0.0", port=application.config["API_PORT"])
