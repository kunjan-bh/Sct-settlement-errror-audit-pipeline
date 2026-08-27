from datetime import datetime

from app.extensions import db


class AppSetting(db.Model):
    """
    Key/value store for operator-editable configuration -- currently the mail
    settings behind the end-of-batch summary email (who it comes from, who it
    goes to, and the SMTP server to hand it to).

    A table rather than a config file or env vars because these are meant to
    be changed from the Settings page by whoever runs the batch, without a
    redeploy or a shell. One row per key, values stored as text and coerced by
    services/settings_service.py, so adding a setting never needs a migration.
    """

    __tablename__ = "app_settings"

    key = db.Column(db.String(64), primary_key=True)
    value = db.Column(db.Text, nullable=True)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    def to_dict(self):
        return {
            "key": self.key,
            "value": self.value,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
