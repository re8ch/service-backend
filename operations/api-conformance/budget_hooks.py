"""Schemathesis 4.29.1 transport guard, loaded before any remote request."""
import os

from request_budget import take


def install():
    import requests.adapters
    import schemathesis.transport.requests as transport

    path = os.environ["RE8CH_REQUEST_BUDGET"]
    original = requests.adapters.HTTPAdapter.send

    def bounded_send(self, request, **kwargs):
        take(path)
        return original(self, request, **kwargs)

    requests.adapters.HTTPAdapter.send = bounded_send
    # 4.29.1 retries dropped connections once even with --request-retries=0.
    # An unknown write outcome must not silently be replayed.
    transport._request_resending_on_dropped_connection = lambda session, data: session.request(**data)


install()
