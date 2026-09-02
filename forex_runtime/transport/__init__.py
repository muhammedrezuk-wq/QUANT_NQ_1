"""The project's single network transport.

Owner's ruling on item 11: ONE transport, and 608 / 620 are its consumers --
without adding a 213th atom. Atom rule 18 forbids a direct network connection
inside an atom; both of them held one (urllib in 620, a UDP socket in 608), so
the project had two independent egress points and no single place to see, time
out, or account for outbound traffic.

Everything that leaves this machine for the open network goes through here.
"""
from .client import StreamSession, TransportError, http_get_json, quote, udp_exchange

__all__ = ["StreamSession", "TransportError", "http_get_json", "quote", "udp_exchange"]
