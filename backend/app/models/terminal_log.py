from datetime import datetime

from app.extensions import db


class TerminalLog(db.Model):
    """
    A record of every terminal this application created on the switch.

    The switch has its own created_on, but it cannot say which tool did it or
    who was sitting at it. This is the only place that knows, and it is the
    reason writing to live is defensible: anything this app added can be
    listed, and if it turns out to be wrong, found again.
    """

    __tablename__ = "terminal_logs"

    id = db.Column(db.Integer, primary_key=True)
    environment = db.Column(db.String(8), nullable=False, index=True)  # live | uat
    mid = db.Column(db.String(32), nullable=False, index=True)
    terminal_name = db.Column(db.String(128), nullable=False)
    pag_id = db.Column(db.String(64), nullable=False)
    outlet_id = db.Column(db.String(64), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)

    def to_dict(self):
        return {
            "id": self.id,
            "environment": self.environment,
            "mid": self.mid,
            "terminal_name": self.terminal_name,
            "pag_id": self.pag_id,
            "outlet_id": self.outlet_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
