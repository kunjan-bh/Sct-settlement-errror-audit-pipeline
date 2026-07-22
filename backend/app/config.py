import os

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


class Config:
    # SQLite file lives in backend/instance/ -- Flask's default convention
    # for "stuff that shouldn't be in version control" (instance-specific
    # data). Keeps the DB file out of your repo root automatically.
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL", f"sqlite:///{os.path.join(BASE_DIR, 'instance', 'smartqr.db')}"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    UPLOAD_DIR = os.path.join(BASE_DIR, "storage", "uploads")
    REPORT_DIR = os.path.join(BASE_DIR, "storage", "reports")
