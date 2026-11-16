from flask import Blueprint, abort, render_template, request

from .models import NCR, Defect, db
from .utils import can_view, defect_scope

bp = Blueprint("ncr", __name__, url_prefix="/ncr")


@bp.route("/")
def index():
    status = request.args.get("status", "")
    q = defect_scope(NCR.query.join(Defect))
    if status in ("open", "closed"):
        q = q.filter(NCR.status == status)
    items = q.order_by(NCR.issued_at.desc()).all()
    return render_template("ncr/index.html", items=items, status=status)


@bp.route("/<int:ncr_id>")
def view(ncr_id):
    n = db.session.get(NCR, ncr_id)
    if n is None or not can_view(n.defect):
        abort(404)
    return render_template("ncr/print.html", n=n, d=n.defect)
