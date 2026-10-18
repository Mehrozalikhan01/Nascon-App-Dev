import base64
import math
import re
import threading
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from app.core.config import settings
from app.core.security import sign

def generate_pdf_receipt(order_id: str, amount_formatted: str) -> str:
    def escape_pdf_text(value: str) -> str:
        return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    text_stream = (
        "BT\n/F1 10 Tf\n48 754 Td\n(NASCON '26  -  CLUB STORE) Tj\n"
        "/F1 22 Tf\n0 -35 Td\n(Official payment receipt) Tj\n"
        "/F1 10 Tf\n0 -40 Td\n(ORDER REFERENCE) Tj\n"
        "/F1 13 Tf\n0 -20 Td\n(" + escape_pdf_text(order_id) + ") Tj\n"
        "/F1 10 Tf\n0 -38 Td\n(PAYMENT STATUS                       TOTAL PAID) Tj\n"
        "/F1 14 Tf\n0 -23 Td\n(PAID                                      " + escape_pdf_text(amount_formatted) + ") Tj\n"
        "/F1 9 Tf\n0 -45 Td\n(This receipt confirms a successful payment in the NaSCon demo store.) Tj\n"
        "0 -16 Td\n(The payment gateway used for this event environment is simulated.) Tj\n"
        "ET\n"
    ).encode("ascii")

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(text_stream)).encode("ascii") + b" >>\nstream\n"
        + text_stream + b"endstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    pdf = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for object_number, body in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf.extend(f"{object_number} 0 obj\n".encode("ascii"))
        pdf.extend(body)
        pdf.extend(b"\nendobj\n")

    xref_offset = len(pdf)
    pdf.extend(f"xref\n0 {len(offsets)}\n".encode("ascii"))
    pdf.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        pdf.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    pdf.extend(
        f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\n"
        f"startxref\n{xref_offset}\n%%EOF\n".encode("ascii")
    )
    return base64.b64encode(bytes(pdf)).decode("ascii")

class StoreService:
    def __init__(self):
        self._lock = threading.Lock()
        self._checkout_lock = threading.RLock()
        self._stock: Dict[str, int] = {}
        self._reserved: Dict[str, int] = {}
        self._orders: Dict[str, Dict[str, Any]] = {}
        self._variants: Dict[str, Dict[str, Any]] = {}
        self.init_baseline()

    def init_baseline(self, stock_map: Optional[Dict[str, int]] = None):
        with self._lock:
            self._orders = {}
            # Baseline variants
            self._variants = {
                "var-jersey-001-M": {
                    "variant_id": "var-jersey-001-M",
                    "product_id": "prod-jersey-001",
                    "price_minor": 450000,
                    "currency": "PKR"
                },
                "var-jersey-001-L": {
                    "variant_id": "var-jersey-001-L",
                    "product_id": "prod-jersey-001",
                    "price_minor": 450000,
                    "currency": "PKR"
                }
            }
            stocks = stock_map or {"var-jersey-001-M": 1, "var-jersey-001-L": 5}
            self._stock = {vid: stocks.get(vid, 0) for vid in self._variants}
            self._reserved = {vid: 0 for vid in self._variants}

    def get_inventory(self, variant_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            if variant_id not in self._variants:
                return None
            return {
                "variant_id": variant_id,
                "stock": self._stock.get(variant_id, 0),
                "reserved": self._reserved.get(variant_id, 0),
                "price_minor": self._variants[variant_id]["price_minor"],
                "currency": self._variants[variant_id]["currency"],
            }

    def checkout(self, data: Dict[str, Any]) -> Dict[str, Any]:
        # Keep idempotency lookup, stock reservation, payment, and order commit
        # in one critical section so concurrent requests cannot double-charge
        # the same order id.
        with self._checkout_lock:
            return self._checkout(data)

    def _checkout(self, data: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(data, dict):
            return {"error": "VALIDATION_ERROR", "fields": ["body"]}
        order_id = data.get("order_id")
        customer_id = data.get("customer_id")
        items = data.get("items")
        discount_code = data.get("discount_code")
        payment = data.get("payment", {})
        if not isinstance(payment, dict):
            return {"error": "VALIDATION_ERROR", "fields": ["payment"]}
        token = payment.get("token")

        # 1. Validation Order: 1) 400 Schema/Malformed
        valid_id = lambda value: isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.:@+-]{1,64}", value) is not None
        if not valid_id(order_id):
            return {"error": "VALIDATION_ERROR", "fields": ["order_id"]}
        if not valid_id(customer_id):
            return {"error": "VALIDATION_ERROR", "fields": ["customer_id"]}
        if not items or not isinstance(items, list) or len(items) < 1 or len(items) > 10:
            return {"error": "VALIDATION_ERROR", "fields": ["items"]}

        # Check items structure
        variant_ids_seen = set()
        for it in items:
            if not isinstance(it, dict):
                return {"error": "VALIDATION_ERROR", "fields": ["items"]}
            vid = it.get("variant_id")
            qty = it.get("quantity")
            if not isinstance(vid, str) or not vid or isinstance(qty, bool) or not isinstance(qty, int) or qty < 1 or qty > 10:
                return {"error": "VALIDATION_ERROR", "fields": ["items"]}
            if vid in variant_ids_seen:
                return {"error": "VALIDATION_ERROR", "fields": ["items"]}
            variant_ids_seen.add(vid)

        if not isinstance(token, str) or not token:
            return {"error": "VALIDATION_ERROR", "fields": ["payment.token"]}
        if discount_code is not None and not isinstance(discount_code, str):
            return {"error": "VALIDATION_ERROR", "fields": ["discount_code"]}

        # Idempotency is defined over the complete request body. Keeping all
        # fields prevents a changed payment token or extra option from being
        # mistaken for a replay of a previously captured order.
        request_fingerprint = dict(data)

        # Idempotency check
        with self._lock:
            if order_id in self._orders:
                existing = self._orders[order_id]
                if existing["req_data"] == request_fingerprint:
                    return {"is_replay": True, "response": existing["response"]}
                return {"error": "DUPLICATE_ORDER_ID"}

        # 2. Variants exist (404)
        for it in items:
            vid = it["variant_id"]
            if vid not in self._variants:
                return {"error": "VARIANT_NOT_FOUND", "variant_id": vid}

        # 3. Discount code check (400)
        if discount_code is not None:
            if discount_code != "WELCOME10":
                return {"error": "INVALID_DISCOUNT_CODE"}

        # Atomic reservation (409)
        with self._lock:
            for it in items:
                vid = it["variant_id"]
                qty = it["quantity"]
                if self._stock[vid] < qty:
                    return {"error": "OUT_OF_STOCK", "variant_id": vid}

            # Deduct from stock, add to reserved
            for it in items:
                vid = it["variant_id"]
                qty = it["quantity"]
                self._stock[vid] -= qty
                self._reserved[vid] += qty

        # 4. Mock payment gateway
        if token == "tok_declined":
            with self._lock:
                for it in items:
                    vid = it["variant_id"]
                    qty = it["quantity"]
                    self._stock[vid] += qty
                    self._reserved[vid] -= qty
            return {"error": "PAYMENT_DECLINED"}

        if token == "tok_capture_fail":
            with self._lock:
                for it in items:
                    vid = it["variant_id"]
                    qty = it["quantity"]
                    self._stock[vid] += qty
                    self._reserved[vid] -= qty
            return {"error": "PAYMENT_CAPTURE_FAILED"}

        if token != "tok_success":
            with self._lock:
                for it in items:
                    vid = it["variant_id"]
                    qty = it["quantity"]
                    self._stock[vid] += qty
                    self._reserved[vid] -= qty
            return {"error": "PAYMENT_DECLINED"}

        # Success: commit reservation (reserved decreases)
        with self._lock:
            for it in items:
                vid = it["variant_id"]
                qty = it["quantity"]
                self._reserved[vid] -= qty

        # Calculate pricing
        subtotal_minor = 0
        order_items = []
        for it in items:
            vid = it["variant_id"]
            qty = it["quantity"]
            unit_price = self._variants[vid]["price_minor"]
            subtotal_minor += unit_price * qty
            order_items.append({
                "variant_id": vid,
                "quantity": qty,
                "unit_price_minor": unit_price
            })

        discount_minor = 0
        if discount_code == "WELCOME10":
            discount_minor = math.floor(subtotal_minor * 10 / 100)

        total_minor = subtotal_minor - discount_minor
        paid_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        # Format total as PKR 4500.00
        major_units = total_minor / 100.0
        formatted_total = f"PKR {major_units:.2f}"

        receipt_payload = {
            "order_id": order_id,
            "customer_id": customer_id,
            "items": order_items,
            "subtotal_minor": subtotal_minor,
            "discount_minor": discount_minor,
            "total_minor": total_minor,
            "currency": "PKR",
            "paid_at": paid_at
        }
        receipt_sig = sign(settings.DAY1_SALT, order_id, receipt_payload)
        pdf_b64 = generate_pdf_receipt(order_id, formatted_total)

        response = {
            "order_id": order_id,
            "status": "paid",
            "currency": "PKR",
            "items": order_items,
            "subtotal_minor": subtotal_minor,
            "discount_minor": discount_minor,
            "total_minor": total_minor,
            "payment": {
                "authorization_id": f"auth_{order_id}",
                "capture_id": f"cap_{order_id}",
                "state": "captured"
            },
            "receipt": {
                "payload": receipt_payload,
                "signature": receipt_sig,
                "pdf_base64": pdf_b64
            }
        }

        with self._lock:
            self._orders[order_id] = {
                "req_data": request_fingerprint,
                "response": response
            }

        return {"is_replay": False, "response": response}

store_service = StoreService()
