"""FIX 4.4 for the cTrader (Spotware) QUOTE session -- protocol only, no socket.

The socket belongs to `transport.StreamSession` (the project's single egress).
This module turns bytes into messages and messages into market state, so the
atom on top stays thin and testable without a network.

What is asserted here is only what the wire itself proves:

* Framing, BodyLength and CheckSum are FIX 4.4 and fully determined.
* Admin flow (Logon / Heartbeat / TestRequest / Logout) is FIX 4.4.
* Symbol identity on cTrader is a NUMBER, not a name. This module never
  invents one: `SecurityList` is requested first and the id/name map is built
  from the venue's own answer. Every tag seen inside the security group is
  kept, so the first live run reports the real layout instead of a guess.

Business time is never taken from this machine. `SendingTime` is written for
the wire (the server rejects a stale one), but every market fact carries the
venue's own stamp, taken from the message that delivered it.
"""
from __future__ import annotations

import datetime as _dt
from typing import Any, Iterable

SOH = b"\x01"
BEGIN_STRING = "FIX.4.4"
HEADER = b"8=" + BEGIN_STRING.encode("ascii") + SOH

# --- tags used here, by number, so a reader can check them against the spec ---
TAG_BEGIN, TAG_BODY_LEN, TAG_MSG_TYPE, TAG_CHECKSUM = 8, 9, 35, 10
TAG_SENDER, TAG_TARGET, TAG_SEQ, TAG_SENDING_TIME = 49, 56, 34, 52
TAG_SENDER_SUB, TAG_TARGET_SUB = 50, 57
TAG_ENCRYPT, TAG_HEARTBT_INT, TAG_RESET_SEQ = 98, 108, 141
TAG_USERNAME, TAG_PASSWORD = 553, 554
TAG_TEST_REQ_ID, TAG_TEXT = 112, 58
TAG_SYMBOL = 55
TAG_MD_REQ_ID, TAG_SUB_TYPE, TAG_MARKET_DEPTH, TAG_MD_UPDATE_TYPE = 262, 263, 264, 265
TAG_NO_MD_ENTRY_TYPES, TAG_MD_ENTRY_TYPE = 267, 269
TAG_NO_RELATED_SYM = 146
TAG_NO_MD_ENTRIES, TAG_MD_ENTRY_PX, TAG_MD_ENTRY_SIZE, TAG_MD_ENTRY_ID = 268, 270, 271, 278
TAG_MD_UPDATE_ACTION = 279
TAG_SEC_REQ_ID, TAG_SEC_LIST_REQ_TYPE, TAG_SEC_RESP_ID = 320, 559, 322
TAG_SYMBOL_NAME = 1007

MSG_HEARTBEAT, MSG_TEST_REQUEST, MSG_RESEND, MSG_REJECT = "0", "1", "2", "3"
MSG_SEQUENCE_RESET, MSG_LOGOUT, MSG_LOGON = "4", "5", "A"
MSG_MARKET_DATA_REQUEST, MSG_SECURITY_LIST_REQUEST = "V", "x"
MSG_SNAPSHOT, MSG_INCREMENTAL, MSG_SECURITY_LIST = "W", "X", "y"
MSG_MD_REQUEST_REJECT = "Y"

ENTRY_BID, ENTRY_OFFER = "0", "1"
ACTION_NEW, ACTION_CHANGE, ACTION_DELETE = "0", "1", "2"

_ADMIN = {MSG_HEARTBEAT, MSG_TEST_REQUEST, MSG_RESEND, MSG_REJECT,
          MSG_SEQUENCE_RESET, MSG_LOGOUT, MSG_LOGON}


def sending_time(now: float) -> str:
    """FIX UTCTimestamp with milliseconds. Wire plumbing, not business time."""
    stamp = _dt.datetime.fromtimestamp(now, _dt.timezone.utc)
    return stamp.strftime("%Y%m%d-%H:%M:%S.") + "%03d" % (stamp.microsecond // 1000)


def parse_sending_time(value: str) -> float | None:
    """Venue stamp -> epoch seconds. Returns None rather than guessing."""
    for shape in ("%Y%m%d-%H:%M:%S.%f", "%Y%m%d-%H:%M:%S"):
        try:
            naive = _dt.datetime.strptime(value, shape)
        except (TypeError, ValueError):
            continue
        return naive.replace(tzinfo=_dt.timezone.utc).timestamp()
    return None


def encode(fields: Iterable[tuple[int, Any]]) -> bytes:
    """Build a complete FIX message: 8, 9, body, 10 -- lengths computed, never assumed."""
    body = SOH.join(b"%d=%s" % (tag, str(value).encode("ascii", "replace"))
                    for tag, value in fields) + SOH
    head = b"8=" + BEGIN_STRING.encode("ascii") + SOH + b"9=%d" % len(body) + SOH
    raw = head + body
    checksum = sum(raw) % 256
    return raw + b"10=%03d" % checksum + SOH


def frame(buffer: bytes) -> tuple[list[bytes], bytes]:
    """Split a byte stream into whole messages. Returns (messages, remainder).

    Framing is driven by BodyLength (tag 9), never by a delimiter search: a
    price or a text field may legally contain anything, and a scan for the
    checksum tag would cut a message in half the first time it did.
    """
    messages: list[bytes] = []
    head = b"8=" + BEGIN_STRING.encode("ascii") + SOH
    pos = 0
    while True:
        # Walk by index. Re-slicing the remainder after every message is
        # quadratic, and it does not merely run slow -- it spirals: the slower
        # it drains, the bigger the buffer, the more it copies. Measured on the
        # live feed 2026-08-21 with a 1 MB backlog: 13,200 bytes/s arriving
        # against 0.10 messages/s consumed, i.e. a stalled feed.
        start = buffer.find(head, pos)
        if start < 0:
            keep = max(pos, len(buffer) - len(head))
            return messages, buffer[keep:]
        len_start = buffer.find(b"9=", start)
        if len_start < 0:
            return messages, buffer[start:]
        len_end = buffer.find(SOH, len_start)
        if len_end < 0:
            return messages, buffer[start:]
        try:
            body_len = int(buffer[len_start + 2:len_end])
        except ValueError:
            pos = len_start + 2
            continue
        body_end = len_end + 1 + body_len
        tail_end = buffer.find(SOH, body_end)
        if tail_end < 0 or body_end > len(buffer):
            return messages, buffer[start:]
        messages.append(buffer[start:tail_end + 1])
        pos = tail_end + 1


def parse(message: bytes) -> list[tuple[int, str]]:
    """Ordered (tag, value) pairs. Order is kept because repeating groups need it."""
    out: list[tuple[int, str]] = []
    for part in message.split(SOH):
        if not part:
            continue
        head, _, value = part.partition(b"=")
        try:
            tag = int(head)
        except ValueError:
            continue
        out.append((tag, value.decode("ascii", "replace")))
    return out


def first(pairs: Iterable[tuple[int, str]], tag: int) -> str | None:
    for key, value in pairs:
        if key == tag:
            return value
    return None


def _to_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result


def split_groups(pairs: list[tuple[int, str]], count_tag: int,
                 start_tag: int | None = None) -> list[dict[int, str]]:
    """Cut a repeating group into rows, opening a new row at the delimiter.

    The delimiter is READ, not assumed: FIX 4.4 defines it as the first field
    after the count tag, and it differs per message. Measured on the live
    cServer 2026-08-21: an incremental refresh opens each entry with 279
    (MDUpdateAction), while a security list opens with 55. Hard-coding 269 for
    the entries shifted every update action onto the previous level -- a delete
    would have removed the wrong price.

    Every tag inside the group is preserved. Nothing is filtered to a known
    list, so a venue field this project has not met yet arrives visible rather
    than silently dropped.
    """
    rows: list[dict[int, str]] = []
    inside = False
    delimiter = None
    current: dict[int, str] = {}
    for tag, value in pairs:
        if tag == count_tag:
            inside = True
            delimiter = None
            continue
        if not inside:
            continue
        if delimiter is None:
            delimiter = tag
        if tag == delimiter:
            if current:
                rows.append(current)
            current = {tag: value}
            continue
        if current:
            current[tag] = value
    if current:
        rows.append(current)
    return rows


class Book:
    """One symbol's ladder, rebuilt from snapshots and kept by incrementals.

    Entries are keyed by the venue's own MDEntryID, which is how cTrader
    addresses a level for change and delete. A level this book never saw is not
    invented on a change; it is added, and the miss is counted so the atom can
    report it instead of drifting quietly out of sync with the venue.
    """

    def __init__(self) -> None:
        self.bids: dict[str, tuple[float, float]] = {}
        self.asks: dict[str, tuple[float, float]] = {}
        self.orphan_updates = 0

    def clear(self) -> None:
        self.bids.clear()
        self.asks.clear()

    def apply(self, entry_type: str, entry_id: str, price: float | None,
              size: float | None, action: str | None) -> None:
        if action == ACTION_DELETE:
            # Measured on cServer 2026-08-21: a delete carries 279 and 278 only
            # -- no MDEntryType, so the side is unknown. The id is unique across
            # the book, so it is dropped wherever it lives. Reading the side
            # from a missing tag would have silently dropped every delete, and
            # the ladder grew to 1595 levels before this was caught.
            gone = self.bids.pop(entry_id, None) is not None
            gone = (self.asks.pop(entry_id, None) is not None) or gone
            if not gone:
                self.orphan_updates += 1
            return
        if not entry_type or price is None:
            self.orphan_updates += 1
            return
        side = self.bids if entry_type == ENTRY_BID else self.asks
        if action == ACTION_CHANGE and entry_id not in side:
            self.orphan_updates += 1
        side[entry_id] = (price, size if size is not None else 0.0)

    def ladder(self) -> tuple[list[dict[str, float]], list[dict[str, float]]]:
        bids = sorted(self.bids.values(), key=lambda row: row[0], reverse=True)
        asks = sorted(self.asks.values(), key=lambda row: row[0])
        return ([{"price": p, "volume": v} for p, v in bids],
                [{"price": p, "volume": v} for p, v in asks])

    def top(self) -> tuple[float | None, float | None]:
        best_bid = max(self.bids.values(), key=lambda row: row[0])[0] if self.bids else None
        best_ask = min(self.asks.values(), key=lambda row: row[0])[0] if self.asks else None
        return best_bid, best_ask


class FixSession:
    """Sequence numbers, admin replies, and outbound message construction.

    Holds no socket and no clock of its own: the caller supplies the current
    time and carries the bytes. That is what makes the whole session testable
    without a network and without waiting on a real second to pass.
    """

    def __init__(self, sender_comp_id: str, target_comp_id: str, sub_id: str,
                 username: str, heartbeat_s: int = 30) -> None:
        self.sender_comp_id = sender_comp_id
        self.target_comp_id = target_comp_id
        self.sub_id = sub_id
        self.username = username
        self.heartbeat_s = int(heartbeat_s)
        self.out_seq = 0
        self.in_seq = 0
        self.logged_on = False
        self.last_error = ""
        self.rejects = 0

    def _header(self, msg_type: str, now: float) -> list[tuple[int, Any]]:
        self.out_seq += 1
        return [(TAG_MSG_TYPE, msg_type), (TAG_SENDER, self.sender_comp_id),
                (TAG_TARGET, self.target_comp_id), (TAG_SENDER_SUB, self.sub_id),
                (TAG_TARGET_SUB, self.sub_id), (TAG_SEQ, self.out_seq),
                (TAG_SENDING_TIME, sending_time(now))]

    def logon(self, password: str, now: float, reset: bool = True) -> bytes:
        self.out_seq = 0
        fields = self._header(MSG_LOGON, now) + [
            (TAG_ENCRYPT, 0), (TAG_HEARTBT_INT, self.heartbeat_s),
            (TAG_RESET_SEQ, "Y" if reset else "N"),
            (TAG_USERNAME, self.username), (TAG_PASSWORD, password)]
        return encode(fields)

    def heartbeat(self, now: float, test_req_id: str | None = None) -> bytes:
        fields = self._header(MSG_HEARTBEAT, now)
        if test_req_id:
            fields.append((TAG_TEST_REQ_ID, test_req_id))
        return encode(fields)

    def test_request(self, request_id: str, now: float) -> bytes:
        return encode(self._header(MSG_TEST_REQUEST, now) + [(TAG_TEST_REQ_ID, request_id)])

    def logout(self, now: float, text: str = "") -> bytes:
        fields = self._header(MSG_LOGOUT, now)
        if text:
            fields.append((TAG_TEXT, text))
        return encode(fields)

    def security_list_request(self, request_id: str, now: float) -> bytes:
        return encode(self._header(MSG_SECURITY_LIST_REQUEST, now) +
                      [(TAG_SEC_REQ_ID, request_id), (TAG_SEC_LIST_REQ_TYPE, 0)])

    def market_data_request(self, request_id: str, symbol_id: str, depth: int,
                            now: float) -> bytes:
        """Subscribe to a symbol. depth 0 = full ladder, 1 = top of book.

        Both the tick and the depth event this project publishes are derived
        from one full-ladder subscription, so the best price and the ladder can
        never disagree about the same instant.
        """
        return encode(self._header(MSG_MARKET_DATA_REQUEST, now) + [
            (TAG_MD_REQ_ID, request_id), (TAG_SUB_TYPE, 1),
            (TAG_MARKET_DEPTH, depth), (TAG_MD_UPDATE_TYPE, 1),
            (TAG_NO_MD_ENTRY_TYPES, 2),
            (TAG_MD_ENTRY_TYPE, ENTRY_BID), (TAG_MD_ENTRY_TYPE, ENTRY_OFFER),
            (TAG_NO_RELATED_SYM, 1), (TAG_SYMBOL, symbol_id)])

    def observe(self, pairs: list[tuple[int, str]]) -> str:
        """Track inbound sequence and session state. Returns the message type."""
        msg_type = first(pairs, TAG_MSG_TYPE) or ""
        seq = first(pairs, TAG_SEQ)
        if seq is not None:
            try:
                self.in_seq = int(seq)
            except ValueError:
                pass
        if msg_type == MSG_LOGON:
            self.logged_on = True
            self.last_error = ""
        elif msg_type == MSG_LOGOUT:
            self.logged_on = False
            self.last_error = first(pairs, TAG_TEXT) or "LOGOUT"
        elif msg_type in (MSG_REJECT, MSG_MD_REQUEST_REJECT):
            self.rejects += 1
            self.last_error = "%s: %s" % (msg_type, first(pairs, TAG_TEXT) or "")
        return msg_type

    @staticmethod
    def is_admin(msg_type: str) -> bool:
        return msg_type in _ADMIN


def read_security_list(pairs: list[tuple[int, str]]) -> list[dict[int, str]]:
    """Symbols exactly as the venue lists them -- id, name, and every other tag."""
    return split_groups(pairs, TAG_NO_RELATED_SYM, TAG_SYMBOL)


def read_md_entries(pairs: list[tuple[int, str]]) -> list[dict[int, str]]:
    return split_groups(pairs, TAG_NO_MD_ENTRIES, TAG_MD_ENTRY_TYPE)


def entry_values(row: dict[int, str]) -> tuple[str, str, float | None, float | None, str | None]:
    return (row.get(TAG_MD_ENTRY_TYPE, ""), row.get(TAG_MD_ENTRY_ID, ""),
            _to_float(row.get(TAG_MD_ENTRY_PX)), _to_float(row.get(TAG_MD_ENTRY_SIZE)),
            row.get(TAG_MD_UPDATE_ACTION))
