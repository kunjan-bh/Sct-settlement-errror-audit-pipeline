from app import create_app
from app.services.classification_service import RuleEngine, PartnerResolver

app = create_app()

SAMPLE_ROWS = [
    {"mid": "0961234567", "remark": "Wallet account validation failed"},
    {"mid": "2001234567", "remark": "Destination branch operation date issue"},
    {"mid": "1051234567", "remark": "Failed to decode:Unrecognized field \"foo\""},
    {"mid": "9991234567", "remark": "Failed"},
    {"mid": "0961234567", "remark": "connection refused: interpay.interpay.svc.cluster.local:8080"},
]

with app.app_context():
    engine = RuleEngine.load()
    resolver = PartnerResolver.load()

    for row in SAMPLE_ROWS:
        result = engine.classify_row(row["remark"])
        partner_name, partner_type = resolver.resolve(row["mid"])
        print(f"MID={row['mid']:<12} remark={row['remark']!r:<70} "
              f"-> side={result.side:<10} category={result.category:<45} "
              f"partner={partner_name}({partner_type})")
