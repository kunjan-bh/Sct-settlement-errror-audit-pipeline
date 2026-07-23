"""
Routes for managing PartnerMapping rows -- this is the backend for the
"frontend entry" page you asked for: add a bank/wallet one at a time, or
create an aggregator and bulk-import a JSON list of its member codes.

Blueprint is registered under /api/partner-mappings in app/__init__.py.
"""
from flask import Blueprint, request, jsonify

from app.extensions import db
from app.models.partner_mapping import PartnerMapping, normalize_member_code

partner_mappings_bp = Blueprint("partner_mappings", __name__, url_prefix="/api/partner-mappings")


@partner_mappings_bp.get("")
def list_mappings():
    """
    GET /api/partner-mappings?bucket=aggregator
    GET /api/partner-mappings?bucket=bank_wallet
    Returns everything if no ?bucket filter is given.
    """
    bucket = request.args.get("bucket")
    query = PartnerMapping.query
    if bucket:
        query = query.filter_by(bucket=bucket)
    mappings = query.order_by(PartnerMapping.partner_name, PartnerMapping.member_code).all()
    return jsonify([m.to_dict() for m in mappings])


@partner_mappings_bp.post("")
def create_mapping():
    """
    Add a single mapping -- typically used for bank_wallet entries, which
    are added one at a time (no bulk import case for those).

    Body: { "member_code": "07" or "007", "bucket": "bank_wallet",
            "partner_name": "NIC ASIA Bank Ltd", "institution_label": "..." }
    """
    data = request.get_json(force=True)

    member_code = normalize_member_code(data.get("member_code", ""))
    bucket = data.get("bucket")
    partner_name = data.get("partner_name", "").strip()

    if not member_code or len(member_code) != 3:
        return jsonify({"error": "member_code must normalize to exactly 3 characters"}), 400
    if bucket not in ("aggregator", "bank_wallet"):
        return jsonify({"error": "bucket must be 'aggregator' or 'bank_wallet'"}), 400
    if not partner_name:
        return jsonify({"error": "partner_name is required"}), 400

    if PartnerMapping.query.filter_by(member_code=member_code).first():
        return jsonify({"error": f"member_code {member_code} is already mapped"}), 409

    mapping = PartnerMapping(
        member_code=member_code,
        bucket=bucket,
        partner_name=partner_name,
        institution_label=data.get("institution_label"),
        active=True,
    )
    db.session.add(mapping)
    db.session.commit()
    return jsonify(mapping.to_dict()), 201


@partner_mappings_bp.post("/bulk-import")
def bulk_import():
    """
    Create/attach many cooperative member codes to ONE aggregator in a
    single call -- this is the JSON import you described.

    Body: {
      "partner_name": "SomeAggregatorX",
      "member_codes": ["59", "096", "114", "18", ...],
      "institution_labels": {"59": "Lord Buddha Saving...", ...}   // optional
    }

    Codes already mapped elsewhere are reported back as skipped rather than
    silently overwritten -- an aggregator import should never quietly steal
    a code that was already assigned (e.g. to a bank_wallet entry).
    """
    data = request.get_json(force=True)
    partner_name = data.get("partner_name", "").strip()
    raw_codes = data.get("member_codes", [])
    labels = data.get("institution_labels", {}) or {}

    if not partner_name:
        return jsonify({"error": "partner_name is required"}), 400
    if not isinstance(raw_codes, list) or not raw_codes:
        return jsonify({"error": "member_codes must be a non-empty list"}), 400

    created, skipped = [], []
    for raw in raw_codes:
        code = normalize_member_code(str(raw))
        if len(code) != 3:
            skipped.append({"code": raw, "reason": "does not normalize to 3 characters"})
            continue

        existing = PartnerMapping.query.filter_by(member_code=code).first()
        if existing:
            skipped.append({"code": code, "reason": f"already mapped to {existing.partner_name}"})
            continue

        mapping = PartnerMapping(
            member_code=code,
            bucket="aggregator",
            partner_name=partner_name,
            institution_label=labels.get(str(raw)) or labels.get(code),
            active=True,
        )
        db.session.add(mapping)
        created.append(code)

    db.session.commit()
    return jsonify({"created": created, "skipped": skipped, "created_count": len(created)}), 201


@partner_mappings_bp.put("/<int:mapping_id>")
def update_mapping(mapping_id):
    mapping = PartnerMapping.query.get_or_404(mapping_id)
    data = request.get_json(force=True)

    if "partner_name" in data:
        mapping.partner_name = data["partner_name"].strip()
    if "bucket" in data:
        if data["bucket"] not in ("aggregator", "bank_wallet"):
            return jsonify({"error": "bucket must be 'aggregator' or 'bank_wallet'"}), 400
        mapping.bucket = data["bucket"]
    if "institution_label" in data:
        mapping.institution_label = data["institution_label"]
    if "active" in data:
        mapping.active = bool(data["active"])

    db.session.commit()
    return jsonify(mapping.to_dict())


@partner_mappings_bp.delete("/<int:mapping_id>")
def delete_mapping(mapping_id):
    mapping = PartnerMapping.query.get_or_404(mapping_id)
    db.session.delete(mapping)
    db.session.commit()
    return "", 204


@partner_mappings_bp.delete("/aggregators/<string:partner_name>")
def delete_aggregator(partner_name):
    """Deletes all codes associated with a specific aggregator."""
    PartnerMapping.query.filter_by(bucket="aggregator", partner_name=partner_name).delete()
    db.session.commit()
    return "", 204


@partner_mappings_bp.get("/aggregators")
def list_aggregator_names():
    """Distinct aggregator names -- powers a dropdown so the frontend can
    offer 'add to existing aggregator' instead of only 'create new'."""
    rows = (
        db.session.query(PartnerMapping.partner_name)
        .filter_by(bucket="aggregator")
        .distinct()
        .order_by(PartnerMapping.partner_name)
        .all()
    )
    return jsonify([r[0] for r in rows])
