import os
from flask import Flask
from flask_cors import CORS

from app.config import Config
from app.extensions import db


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    os.makedirs(os.path.dirname(app.config["SQLALCHEMY_DATABASE_URI"].replace("sqlite:///", "")), exist_ok=True)
    os.makedirs(app.config["UPLOAD_DIR"], exist_ok=True)
    os.makedirs(app.config["REPORT_DIR"], exist_ok=True)

    # Vite dev server runs on a different port (5173) than Flask (5000) --
    # without CORS, the browser blocks the frontend's fetch() calls.
    CORS(app, resources={r"/api/*": {"origins": "*"}})

    db.init_app(app)

    # Models must be imported (not necessarily used) before db.create_all()
    # so SQLAlchemy's metadata knows about every table. See models/__init__.py.
    from app import models  # noqa: F401

    from app.routes.partner_mappings import partner_mappings_bp
    app.register_blueprint(partner_mappings_bp)

    from app.routes.batches import batches_bp
    app.register_blueprint(batches_bp)

    from app.routes.analytics import analytics_bp
    app.register_blueprint(analytics_bp)

    from app.routes.settlement_type import settlement_type_bp
    app.register_blueprint(settlement_type_bp)

    return app
