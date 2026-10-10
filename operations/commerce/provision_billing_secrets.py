#!/usr/bin/env python3
"""Prepare billing runtime Secrets on a cluster administrator host.

Run as root on R640 so kubectl uses the reviewed cluster administrator context.
The plan prints only resource names and connection hosts, never credentials.
"""

import argparse
import base64
import json
import secrets
import subprocess
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit


def kubectl(*args, input_text=None, allow_missing=False):
    result = subprocess.run(
        ["kubectl", *args], input=input_text, text=True, capture_output=True, check=False
    )
    if result.returncode and not (allow_missing and "NotFound" in result.stderr):
        raise RuntimeError(f"kubectl operation failed (exit {result.returncode})")
    return result.stdout if result.returncode == 0 else None


def get_secret(namespace, name, optional=False):
    output = kubectl("-n", namespace, "get", "secret", name, "-o", "json", allow_missing=optional)
    return json.loads(output) if output else None


def decoded(secret, key):
    return base64.b64decode(secret["data"][key]).decode()


def billing_uri(uri, readonly=False):
    parts = urlsplit(uri)
    if parts.scheme not in ("postgres", "postgresql") or not parts.hostname:
        raise ValueError("Source credential is not a PostgreSQL URI")
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    if readonly:
        query["options"] = "-c default_transaction_read_only=on"
    # libpq treats '+' literally in the URI query; encode the option space as %20.
    return urlunsplit((parts.scheme, parts.netloc, "/re8ch_billing", urlencode(query, quote_via=quote), parts.fragment))


def secret_object(name, values):
    return {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {"name": name, "namespace": "payments"},
        "type": "Opaque",
        "data": {key: base64.b64encode(value.encode()).decode() for key, value in values.items()},
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("plan", "apply"))
    args = parser.parse_args()

    payment = get_secret("payments", "payment-postgrest-secrets")
    commerce = get_secret("re8ch-default", "re8ch-commerce-db-credentials")
    payment_rw = billing_uri(decoded(payment, "PGRST_DB_URI_RW"))
    commerce_rw = billing_uri(decoded(commerce, "PGRST_DB_URI"))
    payment_ro = billing_uri(payment_rw, readonly=True)
    commerce_ro = billing_uri(commerce_rw, readonly=True)

    names = (
        "billing-payment-postgrest-secrets",
        "commerce-postgrest-secrets",
        "commerce-api-secrets",
    )
    existing = [name for name in names if get_secret("payments", name, optional=True)]
    if existing:
        raise RuntimeError(f"Destination Secrets already exist: {', '.join(existing)}")
    print(json.dumps({
        "phase": args.phase,
        "namespace": "payments",
        "create": names,
        "payment_host": urlsplit(payment_rw).hostname,
        "commerce_host": urlsplit(commerce_rw).hostname,
        "database": "re8ch_billing",
        "readonly_session_option": True,
    }))
    if args.phase == "plan":
        return

    commerce_jwt = secrets.token_urlsafe(48)
    objects = [
        secret_object(names[0], {
            "PGRST_DB_URI_RW": payment_rw,
            "PGRST_DB_URI_RO": payment_ro,
            "PGRST_JWT_SECRET": decoded(payment, "PGRST_JWT_SECRET"),
        }),
        secret_object(names[1], {
            "PGRST_DB_URI_RW": commerce_rw,
            "PGRST_DB_URI_RO": commerce_ro,
            "PGRST_JWT_SECRET": commerce_jwt,
        }),
        secret_object(names[2], {
            "COMMERCE_JWT_SECRET": commerce_jwt,
            "COMMERCE_RECONCILE_TOKEN": secrets.token_urlsafe(48),
        }),
    ]
    for obj in objects:
        kubectl("create", "-f", "-", input_text=json.dumps(obj))
    print(json.dumps({"status": "created", "namespace": "payments", "names": names}))


if __name__ == "__main__":
    main()
