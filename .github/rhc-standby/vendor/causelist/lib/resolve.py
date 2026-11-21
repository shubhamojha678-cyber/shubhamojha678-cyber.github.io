"""DNS fallback for hcraj.nic.in.

Why this exists: the ISP/router resolver SERVFAILs the entire `nic.in` zone
(it fails DNSSEC validation on that zone), while the site itself is perfectly
healthy. The Mac has no explicit DNS set, so it inherits the router and every
nic.in lookup dies with gaierror(8) — which surfaced as `state=network_error`
on every scheduled run. Public resolvers answer fine.

Strategy: try the system resolver first (so this becomes a no-op the moment
the user sets Wi-Fi DNS to 1.1.1.1). If it fails, query the public resolvers
directly over UDP/53 and pin the answer into socket.getaddrinfo for the life
of the process. Pinning at the getaddrinfo layer keeps TLS SNI and the Host
header pointing at the real hostname, so nothing else has to change.

Standalone check:
    python3 lib/resolve.py [hostname]
"""
from __future__ import annotations

import logging
import random
import socket
import struct

log = logging.getLogger(__name__)

PUBLIC_RESOLVERS = ["1.1.1.1", "8.8.8.8", "9.9.9.9", "1.0.0.1"]
DNS_TIMEOUT_S = 4.0

_ORIG_GETADDRINFO = socket.getaddrinfo
_PINNED: dict[str, str] = {}
_PATCHED = False


# ---- minimal DNS/A client (no third-party deps) ----------------------------

def _build_query(host: str, qid: int) -> bytes:
    # QR=0, RD=1 (recursion desired); 1 question, no other sections.
    header = struct.pack("!HHHHHH", qid, 0x0100, 1, 0, 0, 0)
    qname = b"".join(
        bytes([len(label)]) + label.encode("idna")
        for label in host.rstrip(".").split(".")
    ) + b"\x00"
    return header + qname + struct.pack("!HH", 1, 1)  # QTYPE=A, QCLASS=IN


def _skip_name(data: bytes, off: int) -> int:
    """Advance past a (possibly compressed) DNS name, return the new offset."""
    while True:
        length = data[off]
        if length == 0:
            return off + 1
        if length & 0xC0 == 0xC0:  # compression pointer — always terminal
            return off + 2
        off += 1 + length


def _parse_a_records(data: bytes, qid: int) -> list[str]:
    if len(data) < 12:
        raise RuntimeError("short DNS response")
    resp_id, flags, qdcount, ancount = struct.unpack("!HHHH", data[:8])
    if resp_id != qid:
        raise RuntimeError("DNS transaction id mismatch")
    rcode = flags & 0x0F
    if rcode != 0:
        names = {2: "SERVFAIL", 3: "NXDOMAIN", 5: "REFUSED"}
        raise RuntimeError(f"DNS rcode {rcode} ({names.get(rcode, 'error')})")

    off = 12
    for _ in range(qdcount):
        off = _skip_name(data, off) + 4  # + QTYPE + QCLASS

    ips: list[str] = []
    for _ in range(ancount):
        off = _skip_name(data, off)
        rtype, _rclass, _ttl, rdlength = struct.unpack("!HHIH", data[off:off + 10])
        off += 10
        if rtype == 1 and rdlength == 4:  # A record
            ips.append(socket.inet_ntoa(data[off:off + 4]))
        off += rdlength  # CNAMEs etc. are skipped; the A they point to follows
    return ips


def query_a(host: str, server: str, timeout: float = DNS_TIMEOUT_S) -> list[str]:
    """Query one resolver for A records. Raises on failure."""
    qid = random.randint(0, 0xFFFF)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.settimeout(timeout)
        sock.sendto(_build_query(host, qid), (server, 53))
        data, _ = sock.recvfrom(4096)
    finally:
        sock.close()
    return _parse_a_records(data, qid)


# ---- getaddrinfo pinning ---------------------------------------------------

def _patched_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    ip = _PINNED.get(host)
    if ip is None:
        return _ORIG_GETADDRINFO(host, port, family, type, proto, flags)
    return [(socket.AF_INET, type or socket.SOCK_STREAM, proto or 6, "", (ip, port))]


def pin(host: str, ip: str) -> None:
    """Force `host` to resolve to `ip` for the rest of this process."""
    global _PATCHED
    _PINNED[host] = ip
    if not _PATCHED:
        socket.getaddrinfo = _patched_getaddrinfo
        _PATCHED = True


def ensure_resolvable(host: str) -> tuple[str, str]:
    """Make `host` resolvable. Returns (ip, source).

    source is "system" when the OS resolver worked (nothing was patched),
    otherwise the public resolver that answered.
    Raises RuntimeError if no resolver could answer.
    """
    if host in _PINNED:
        return _PINNED[host], "pinned"

    try:
        ip = _ORIG_GETADDRINFO(host, 443, socket.AF_INET, socket.SOCK_STREAM)[0][4][0]
        return ip, "system"
    except OSError as e:
        log.warning(f"system resolver failed for {host}: {e!r} — falling back to public DNS")

    errors = []
    for server in PUBLIC_RESOLVERS:
        try:
            ips = query_a(host, server)
        except Exception as e:
            errors.append(f"{server}: {e!r}")
            continue
        if not ips:
            errors.append(f"{server}: no A records")
            continue
        pin(host, ips[0])
        log.info(f"{host} pinned to {ips[0]} via {server} (system resolver is broken)")
        return ips[0], server

    raise RuntimeError(f"could not resolve {host} — system resolver failed and "
                       f"public DNS failed: {'; '.join(errors)}")


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    target = sys.argv[1] if len(sys.argv) > 1 else "hcraj.nic.in"
    resolved_ip, src = ensure_resolvable(target)
    print(f"{target} -> {resolved_ip}  (via {src})")
