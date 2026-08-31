import os

from dotenv import load_dotenv

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

# Load backend/.env before any module reads os.environ. Secrets (the SMTP
# password above all) live there and are gitignored; .env.example documents
# every variable. Values already set in the real environment win, so a
# container or CI can override the file without editing it.
load_dotenv(os.path.join(BASE_DIR, ".env"), override=False)


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
