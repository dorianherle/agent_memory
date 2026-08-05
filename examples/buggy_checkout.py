"""A small fake codebase with one real bug, used by debug_agent.py.

Orders above 40 EUR are charged twice. The retry in api.py calls
payments.charge() again, and charge() builds a fresh idempotency key
every time, so the gateway sees two unrelated payments.
"""

FILES = {
    "docs/incident-4412.md": """
# Incident 4412: duplicate charges at checkout

Severity: high
Window: 2026-07-28 14:00 UTC to 2026-07-29 09:00 UTC

Symptom
  318 orders produced two successful charges on the payment gateway.
  Both charges have the same amount and the same order id.
  The order row itself is created only once.

What we know
  Only orders above 40 EUR are affected.
  All of them were placed while the gateway was slow.
  A gateway webhook handler shipped on 2026-07-27, one day before
  the window opened.
  Refunds were issued by hand. The cause is still unknown.
""".strip(),

    "docs/runbook.md": """
# Payment runbook

Retries
  Any payment call may be retried. Every payment call carries an
  idempotency key, so the gateway collapses duplicates.
  Nobody has verified this end to end.

Timeouts
  If the gateway is slow, raise GATEWAY_ATTEMPTS. Do not raise
  GATEWAY_TIMEOUT_SECONDS above 3s, the checkout page gives up at 5s.
""".strip(),

    "docs/gateway-notes.md": """
# Gateway notes

Charges above the review threshold go through the gateway fraud path.
That path adds 0.4s to 1.1s. Below the threshold the gateway answers
in about 0.3s.

Idempotency keys are honoured for 24 hours. Two requests with the same
key return the same charge. Two requests with different keys always
create two charges, even for the same order id and the same amount.
""".strip(),

    "checkout/api.py": """
from checkout import orders, payments
from checkout.retry import retry_on_timeout


@app.post("/checkout/<order_id>/pay")
def pay(order_id):
    order = orders.load(order_id)
    if order.status == "paid":
        return {"status": "already_paid"}
    # wrapped because the gateway was flaky in June
    charge = retry_on_timeout(lambda: payments.charge(order), attempts=2)
    orders.mark_paid(order_id, charge.id)
    return {"status": "paid", "charge": charge.id}
""".strip(),

    "checkout/retry.py": """
import time

from checkout.errors import GatewayTimeout


def retry_on_timeout(call, attempts=2, backoff=0.5):
    \"\"\"Retry a gateway call that timed out.

    Safe for writes: payment calls carry an idempotency key, so the
    gateway collapses duplicates.
    \"\"\"
    for attempt in range(attempts):
        try:
            return call()
        except GatewayTimeout:
            if attempt == attempts - 1:
                raise
            time.sleep(backoff * (attempt + 1))
""".strip(),

    "checkout/payments.py": """
from checkout import idempotency
from checkout.http_client import GatewayClient
from checkout.models import Charge

client = GatewayClient()


def charge(order):
    key = idempotency.key_for(order)
    response = client.post(
        "/v1/charges",
        json={
            "amount": order.total,
            "currency": order.currency,
            "order_id": order.id,
        },
        idempotency_key=key,
    )
    return Charge(id=response["id"], amount=response["amount"])
""".strip(),

    "checkout/http_client.py": """
import requests

from checkout.errors import GatewayTimeout
from config import settings


class GatewayClient:
    def post(self, path, json, idempotency_key):
        # the same key is reused across these attempts
        for attempt in range(settings.GATEWAY_ATTEMPTS):
            try:
                response = requests.post(
                    settings.GATEWAY_URL + path,
                    json=json,
                    headers={"Idempotency-Key": idempotency_key},
                    timeout=settings.GATEWAY_TIMEOUT_SECONDS,
                )
                return response.json()
            except requests.Timeout:
                if attempt == settings.GATEWAY_ATTEMPTS - 1:
                    raise GatewayTimeout(path)
""".strip(),

    "checkout/webhooks.py": """
from checkout import orders

# shipped 2026-07-27


@app.post("/webhooks/gateway")
def gateway_event(event):
    if event["type"] == "charge.succeeded":
        orders.mark_paid(event["order_id"], event["charge_id"])
    return {"ok": True}
""".strip(),

    "checkout/idempotency.py": """
import hashlib
import time


def key_for(order):
    # the timestamp keeps keys unique so a customer can buy the same
    # basket twice in a row
    raw = f"{order.id}:{order.total}:{time.time()}"
    return hashlib.sha256(raw.encode()).hexdigest()
""".strip(),

    "config/settings.py": """
GATEWAY_URL = "https://gateway.internal/api"
GATEWAY_TIMEOUT_SECONDS = 2.0
GATEWAY_ATTEMPTS = 2

# gateway latency measured 2026-07-28: p50 0.3s, p99 2.4s
REVIEW_THRESHOLD_EUR = 40
""".strip(),

    "logs/payments.log": """
14:02:11 INFO charge start order=ord_88213 amount=59.90 key=6f1a2c
14:02:13 WARN gateway timeout after 2.0s order=ord_88213 attempt=1
14:02:13 INFO retry_on_timeout retrying order=ord_88213
14:02:13 INFO charge start order=ord_88213 amount=59.90 key=b7e4d9
14:02:14 INFO charge ok order=ord_88213 charge=ch_5512 key=b7e4d9
14:02:15 INFO gateway late reply order=ord_88213 charge=ch_5509 key=6f1a2c
14:02:15 INFO order marked paid order=ord_88213 charge=ch_5512
""".strip(),

    "tests/test_checkout.py": """
def test_pay_charges_once(monkeypatch):
    calls = []
    monkeypatch.setattr(
        client,
        "post",
        lambda *args, **kwargs: calls.append(kwargs) or {
            "id": "ch_1",
            "amount": 59.90,
        },
    )

    pay("ord_1")

    assert len(calls) == 1

# the mock never times out, so the retry path is never tested
""".strip(),
}
