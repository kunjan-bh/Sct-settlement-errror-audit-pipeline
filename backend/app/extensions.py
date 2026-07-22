"""
Shared extension instances.

Why this file exists: Flask-SQLAlchemy's `db` object needs to be importable
by every model file AND by app/__init__.py (to call db.init_app(app)).
If we created `db` inside __init__.py, every model file would have to import
from __init__.py, which creates circular imports the moment __init__.py
also needs to import the models (e.g. for Flask-Migrate autodiscovery).

Pattern: create the extension here with no app attached, import it
everywhere, and bind it to the real app later with db.init_app(app).
This is the standard Flask "application factory" pattern.
"""
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()
