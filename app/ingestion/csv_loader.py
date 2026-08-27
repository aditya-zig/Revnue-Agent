import csv
import hashlib
from io import StringIO

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.db.tables import Customer
from app.domain.models import NormalizedPaymentEvent
from app.ingestion.record_event import record_event_and_update_case


def import_csv(session: Session, content: str) -> tuple[int, int]:
    imported = 0
    duplicates = 0
    for row in csv.DictReader(StringIO(content)):
        try:
            # obligation_reference optional; empty means isolated attempt
            obligation_raw = (
                row.get("obligation_reference")
                or row.get("order_id")
                or row.get("obligation")
                or ""
            )
            obligation_reference = obligation_raw.strip() or None
            event = NormalizedPaymentEvent.model_validate(
                {
                    "event_id": row["event_id"],
                    "provider_event_id": row["event_id"],
                    "event_type": row["event_type"],
                    "payment_id": row["payment_id"],
                    "obligation_reference": obligation_reference,
                    "customer_id": row["customer_id"] or None,
                    "amount": row["amount"],
                    "currency": row["currency"],
                    "method": row["method"] or None,
                    "status": row["status"],
                    "error_source": row.get("error_source") or None,
                    "error_step": row.get("error_step") or None,
                    "error_code": row["error_code"] or None,
                    "error_reason": row["error_reason"] or None,
                    "occurred_at": row["occurred_at"],
                    "provider": row.get("provider") or "csv_import",
                    "raw_hash": hashlib.sha256(str(sorted(row.items())).encode()).hexdigest(),
                    "raw_body": None,
                }
            )
        except (KeyError, ValidationError) as error:
            raise ValueError("invalid CSV row") from error

        if event.customer_id:
            customer = session.get(Customer, event.customer_id)
            if customer is None:
                customer = Customer(customer_id=event.customer_id)
                session.add(customer)
            if row.get("tenure_days"):
                customer.tenure_days = int(row["tenure_days"])
            if row.get("successful_payments"):
                customer.successful_payments = int(row["successful_payments"])
            if row.get("prior_failures"):
                customer.prior_failures = int(row["prior_failures"])
            if row.get("preferred_method"):
                customer.preferred_method = row["preferred_method"]
            if row.get("consent"):
                customer.consent = row["consent"].lower() in {"true", "1", "yes"}
            if row.get("locale"):
                customer.locale = row["locale"]

        if not record_event_and_update_case(session, event):
            duplicates += 1
            continue
        imported += 1
    return imported, duplicates
