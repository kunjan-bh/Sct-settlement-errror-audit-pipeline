"""
Import every model here so that a single `from app import models` (or
`from app.models import *`) registers all tables with SQLAlchemy's
metadata. This matters for `db.create_all()` and for Flask-Migrate's
autogenerate -- if a model isn't imported somewhere before those run,
SQLAlchemy doesn't know it exists and silently skips its table.
"""
from app.models.batch import Batch
from app.models.transaction import Transaction
from app.models.classification_rule import ClassificationRule
from app.models.partner_mapping import PartnerMapping
from app.models.issue_status import IssueStatus
from app.models.app_setting import AppSetting

__all__ = [
    "Batch", "Transaction", "ClassificationRule", "PartnerMapping", "IssueStatus",
    "AppSetting",
]
