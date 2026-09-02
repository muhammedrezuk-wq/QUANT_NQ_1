"""NTP invariants that pre-date the shared-clock repair and must not regress."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
from build_registry.paths import RegistryAtomRoot
ATOM_ROOT = RegistryAtomRoot(ROOT)

_SOURCE = (ATOM_ROOT / "608_مزامنة_الوقت" / "atom.py").read_text("utf-8")


def test_608_keeps_central_udp_transport():
    assert "import transport" in _SOURCE and "transport.udp_exchange" in _SOURCE


def test_608_does_not_open_a_private_socket():
    assert "import socket" not in _SOURCE and "socket.socket" not in _SOURCE


def test_608_keeps_exact_ntp_packet_length_validation():
    assert "_NTP_PACKET_BYTES = 48" in _SOURCE
    assert "len(data)" in _SOURCE and "NTP reply length invalid" in _SOURCE


def test_608_keeps_server_mode_validation():
    assert "_NTP_MODE_MASK" in _SOURCE and "_NTP_SERVER_MODES" in _SOURCE
    assert "mode not in _NTP_SERVER_MODES" in _SOURCE


def test_608_keeps_stratum_validation():
    assert "_NTP_MIN_STRATUM" in _SOURCE and "_NTP_MAX_STRATUM" in _SOURCE
    assert "stratum" in _SOURCE and "NTP reply mode" in _SOURCE


def test_608_keeps_originate_timestamp_validation():
    assert "NTP originate timestamp mismatch" in _SOURCE
    assert "_NTP_TX_START:_NTP_TX_END" in _SOURCE


def test_608_keeps_absolute_offset_bound():
    assert "max_accepted_offset_s" in _SOURCE
    assert "abs(offset) > self._max_accepted_offset_s" in _SOURCE
