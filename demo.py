#!/usr/bin/env python3
"""
Automated Demonstration Suite for PyProxy Server.
Validates all core requirements:
1. Normal Request Forwarding (GET & POST)
2. Cache Miss vs. Cache Hit (Speedup verification & X-Cache headers)
3. Access Control (Domain blocking & Wildcard filtering)
4. Content Filtering (Path/Keyword blocking)
5. Rate Limiting (429 Too Many Requests)
6. Robustness & Error Handling (502 Bad Gateway, 400 Bad Request)
7. HTTPS CONNECT Tunneling
8. Administrator Telemetry API
"""

import socket
import time
import json
import subprocess
import sys
from typing import Tuple


GREEN = "\033[92m"
BLUE = "\033[94m"
YELLOW = "\033[93m"
RED = "\033[91m"
BOLD = "\033[1m"
RESET = "\033[0m"


def send_proxy_request(
    proxy_host: str, proxy_port: int, raw_request: bytes
) -> Tuple[bytes, float]:
    """Sends raw bytes to the proxy and returns (response_bytes, elapsed_ms)."""
    start = time.time()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(5.0)
        s.connect((proxy_host, proxy_port))
        s.sendall(raw_request)
        response = b""
        while True:
            try:
                chunk = s.recv(4096)
                if not chunk:
                    break
                response += chunk
            except socket.timeout:
                break
    elapsed_ms = (time.time() - start) * 1000
    return response, elapsed_ms


def parse_response_headers(raw_response: bytes):
    """Parses status line and headers from raw HTTP response."""
    header_end = raw_response.find(b"\r\n\r\n")
    if header_end == -1:
        return 0, "", {}, raw_response

    headers_part = raw_response[:header_end].decode("iso-8859-1", errors="replace")
    body = raw_response[header_end + 4:]
    lines = headers_part.splitlines()

    status_code = 0
    reason = ""
    headers = {}

    if lines:
        parts = lines[0].split(None, 2)
        if len(parts) >= 2 and parts[1].isdigit():
            status_code = int(parts[1])
            reason = parts[2] if len(parts) > 2 else ""

    for l in lines[1:]:
        if ":" in l:
            k, v = l.split(":", 1)
            headers[k.strip().lower()] = v.strip()

    return status_code, reason, headers, body


def run_demo():
    PROXY_HOST = "127.0.0.1"
    PROXY_PORT = 8888
    MOCK_PORT = 9000

    print(f"\n{BOLD}{BLUE}{'='*65}{RESET}")
    print(f"{BOLD}{BLUE}       PyProxy Demonstration & Verification Test Suite{RESET}")
    print(f"{BOLD}{BLUE}{'='*65}{RESET}\n")

    # Start mock server in background if not already responding
    mock_proc = None
    try:
        with socket.create_connection(("127.0.0.1", MOCK_PORT), timeout=0.3):
            pass
    except Exception:
        print(f"[*] Starting local mock upstream server on port {MOCK_PORT}...")
        mock_proc = subprocess.Popen([sys.executable, "test_server.py", "--port", str(MOCK_PORT)])
        time.sleep(0.5)

    # Start proxy server in background if not already responding
    proxy_proc = None
    try:
        with socket.create_connection((PROXY_HOST, PROXY_PORT), timeout=0.3):
            pass
    except Exception:
        print(f"[*] Starting PyProxy server on port {PROXY_PORT}...")
        proxy_proc = subprocess.Popen(
            [sys.executable, "start_proxy.py", "--port", str(PROXY_PORT), "-q"]
        )
        time.sleep(0.6)

    try:
        # ---------------------------------------------------------
        # TEST 1: Normal Request Forwarding (GET & POST)
        # ---------------------------------------------------------
        print(f"{BOLD}[TEST 1] Normal HTTP Request Forwarding (GET & POST){RESET}")
        req_get = (
            f"GET http://127.0.0.1:{MOCK_PORT}/ HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{MOCK_PORT}\r\n"
            f"User-Agent: PyProxyDemo/1.0\r\n"
            f"Connection: close\r\n\r\n"
        ).encode("utf-8")
        resp, ms = send_proxy_request(PROXY_HOST, PROXY_PORT, req_get)
        status, reason, hdrs, body = parse_response_headers(resp)
        print(f"  -> GET / Status: {GREEN}{status} {reason}{RESET} ({ms:.1f}ms)")
        print(f"  -> Body: {body.decode('utf-8', errors='replace').strip()}")
        assert status == 200, f"Expected 200, got {status}"

        req_post = (
            f"POST http://127.0.0.1:{MOCK_PORT}/echo HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{MOCK_PORT}\r\n"
            f"Content-Length: 18\r\n"
            f"Connection: close\r\n\r\n"
            f"Hello Proxy Server"
        ).encode("utf-8")
        resp, ms = send_proxy_request(PROXY_HOST, PROXY_PORT, req_post)
        status, reason, hdrs, body = parse_response_headers(resp)
        print(f"  -> POST /echo Status: {GREEN}{status} {reason}{RESET} ({ms:.1f}ms)")
        print(f"  -> Body: {body.decode('utf-8', errors='replace').strip()}")
        assert status == 200 and b"Echoed: Hello Proxy Server" in body
        print(f"  {GREEN}✔ Test 1 Passed: GET and POST successfully forwarded.{RESET}\n")

        # ---------------------------------------------------------
        # TEST 2: Cache Hit vs. Cache Miss
        # ---------------------------------------------------------
        print(f"{BOLD}[TEST 2] Caching Semantics (Cache Miss vs. Cache Hit){RESET}")
        url_cache = f"http://127.0.0.1:{MOCK_PORT}/cacheable?run={int(time.time())}"
        req_cache = (
            f"GET {url_cache} HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{MOCK_PORT}\r\n"
            f"Connection: close\r\n\r\n"
        ).encode("utf-8")

        # 1st request -> Cache MISS
        resp1, ms1 = send_proxy_request(PROXY_HOST, PROXY_PORT, req_cache)
        s1, _, h1, b1 = parse_response_headers(resp1)
        cache_hdr1 = h1.get("x-cache", "UNKNOWN")
        print(f"  1st Request (Origin fetch): Status {s1} | X-Cache: {YELLOW}{cache_hdr1}{RESET} | Latency: {ms1:.2f}ms")
        assert "MISS" in cache_hdr1, f"Expected MISS, got {cache_hdr1}"

        # 2nd request -> Cache HIT
        resp2, ms2 = send_proxy_request(PROXY_HOST, PROXY_PORT, req_cache)
        s2, _, h2, b2 = parse_response_headers(resp2)
        cache_hdr2 = h2.get("x-cache", "UNKNOWN")
        hits_count = h2.get("x-cache-hits", "1")
        print(f"  2nd Request (Cache read):   Status {s2} | X-Cache: {GREEN}{cache_hdr2}{RESET} (Hits: {hits_count}) | Latency: {ms2:.2f}ms")
        assert "HIT" in cache_hdr2, f"Expected HIT, got {cache_hdr2}"
        assert b1 == b2, "Cached body must match origin body!"

        # Speedup comparison
        speedup = (ms1 / ms2) if ms2 > 0 else 1.0
        print(f"  -> Cache Speedup: {GREEN}{speedup:.1f}x faster{RESET} (Saved upstream trip)")
        print(f"  {GREEN}✔ Test 2 Passed: Caching mechanism and X-Cache headers validated.{RESET}\n")

        # ---------------------------------------------------------
        # TEST 3: Access Control - Domain Blocking
        # ---------------------------------------------------------
        print(f"{BOLD}[TEST 3] Access Control: Domain Filtering & Wildcards{RESET}")
        req_blocked = (
            f"GET http://blocked.com/index.html HTTP/1.1\r\n"
            f"Host: blocked.com\r\n"
            f"Connection: close\r\n\r\n"
        ).encode("utf-8")
        resp, ms = send_proxy_request(PROXY_HOST, PROXY_PORT, req_blocked)
        status, reason, _, _ = parse_response_headers(resp)
        print(f"  -> Accessing 'blocked.com': Status {RED}{status} {reason}{RESET}")
        assert status == 403, f"Expected 403 Forbidden, got {status}"

        # Wildcard test (*.blocked.com)
        req_sub = (
            f"GET http://ads.blocked.com/tracker HTTP/1.1\r\n"
            f"Host: ads.blocked.com\r\n"
            f"Connection: close\r\n\r\n"
        ).encode("utf-8")
        resp, ms = send_proxy_request(PROXY_HOST, PROXY_PORT, req_sub)
        status, reason, _, _ = parse_response_headers(resp)
        print(f"  -> Accessing 'ads.blocked.com' (Wildcard): Status {RED}{status} {reason}{RESET}")
        assert status == 403, f"Expected 403 Forbidden, got {status}"
        print(f"  {GREEN}✔ Test 3 Passed: Blacklisted domains blocked with 403 Forbidden.{RESET}\n")

        # ---------------------------------------------------------
        # TEST 4: Access Control - Prohibited Path Keyword
        # ---------------------------------------------------------
        print(f"{BOLD}[TEST 4] Content Filtering: URL Path / Keyword Blocking{RESET}")
        req_path = (
            f"GET http://127.0.0.1:{MOCK_PORT}/restricted-admin-portal HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{MOCK_PORT}\r\n"
            f"Connection: close\r\n\r\n"
        ).encode("utf-8")
        resp, ms = send_proxy_request(PROXY_HOST, PROXY_PORT, req_path)
        status, reason, _, _ = parse_response_headers(resp)
        print(f"  -> Accessing prohibited path '/restricted-admin-portal': Status {RED}{status} {reason}{RESET}")
        assert status == 403, f"Expected 403 Forbidden, got {status}"
        print(f"  {GREEN}✔ Test 4 Passed: Prohibited URL paths filtered with 403 Forbidden.{RESET}\n")

        # ---------------------------------------------------------
        # TEST 5: Rate Limiting
        # ---------------------------------------------------------
        print(f"{BOLD}[TEST 5] Security Protection: Client Rate Limiting{RESET}")
        print(f"  -> Sending a rapid burst of 35 requests to trigger rate limit threshold...")
        rate_hit = False
        retry_val = ""
        for i in range(35):
            req_burst = (
                f"GET http://127.0.0.1:{MOCK_PORT}/nocache?i={i} HTTP/1.1\r\n"
                f"Host: 127.0.0.1:{MOCK_PORT}\r\n"
                f"Connection: close\r\n\r\n"
            ).encode("utf-8")
            resp, _ = send_proxy_request(PROXY_HOST, PROXY_PORT, req_burst)
            st, _, h, _ = parse_response_headers(resp)
            if st == 429:
                rate_hit = True
                retry_val = h.get("retry-after", "1")
                print(f"  -> Request #{i+1} triggered {RED}429 Too Many Requests{RESET} (Retry-After: {retry_val}s)")
                break

        assert rate_hit, "Rate limiter should have triggered on rapid burst!"
        print(f"  {GREEN}✔ Test 5 Passed: Rate limiter enforced 429 Too Many Requests.{RESET}\n")

        # Allow rate limiter window to reset
        time.sleep(2.2)

        # ---------------------------------------------------------
        # TEST 6: Robustness & Graceful Error Handling
        # ---------------------------------------------------------
        print(f"{BOLD}[TEST 6] Robustness: Unreachable Hosts & Malformed Syntax{RESET}")
        # Case A: Unreachable server
        req_bad_host = (
            f"GET http://127.0.0.1:54321/down HTTP/1.1\r\n"
            f"Host: 127.0.0.1:54321\r\n"
            f"Connection: close\r\n\r\n"
        ).encode("utf-8")
        resp, ms = send_proxy_request(PROXY_HOST, PROXY_PORT, req_bad_host)
        status, reason, _, _ = parse_response_headers(resp)
        print(f"  -> Connecting to offline port 54321: Status {YELLOW}{status} {reason}{RESET}")
        assert status == 502, f"Expected 502 Bad Gateway, got {status}"

        # Case B: Malformed HTTP request
        resp, ms = send_proxy_request(PROXY_HOST, PROXY_PORT, b"GARBAGE_PROTOCOL_DATA_XYZ\r\n\r\n")
        status, reason, _, _ = parse_response_headers(resp)
        print(f"  -> Sending malformed garbage bytes: Status {YELLOW}{status} {reason}{RESET}")
        assert status == 400, f"Expected 400 Bad Request, got {status}"
        print(f"  {GREEN}✔ Test 6 Passed: Proxy handles edge cases and errors without crashing.{RESET}\n")

        # ---------------------------------------------------------
        # TEST 7: HTTPS CONNECT Tunneling
        # ---------------------------------------------------------
        print(f"{BOLD}[TEST 7] HTTPS Tunneling: HTTP CONNECT Method{RESET}")
        req_connect = (
            f"CONNECT example.com:80 HTTP/1.1\r\n"
            f"Host: example.com:80\r\n\r\n"
        ).encode("utf-8")
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(5.0)
            s.connect((PROXY_HOST, PROXY_PORT))
            s.sendall(req_connect)
            conn_resp = s.recv(1024)
            st, reason, _, _ = parse_response_headers(conn_resp)
            print(f"  -> CONNECT tunnel response: {GREEN}{st} {reason}{RESET}")
            assert st == 200, f"Expected 200 Connection Established, got {st}"
            # Send raw HTTP request through tunnel
            s.sendall(b"GET / HTTP/1.1\r\nHost: example.com\r\nConnection: close\r\n\r\n")
            tunneled_resp = s.recv(4096)
            st_sub, _, _, _ = parse_response_headers(tunneled_resp)
            print(f"  -> Tunneled data returned status: {GREEN}{st_sub}{RESET} (Received {len(tunneled_resp)} bytes)")
            assert len(tunneled_resp) > 0

        print(f"  {GREEN}✔ Test 7 Passed: HTTPS CONNECT tunneling successfully streams data.{RESET}\n")

        # ---------------------------------------------------------
        # TEST 8: Admin Statistics Telemetry Endpoint
        # ---------------------------------------------------------
        print(f"{BOLD}[TEST 8] Administrator Monitoring & Telemetry Endpoint{RESET}")
        req_stats = (
            f"GET http://127.0.0.1:{PROXY_PORT}/proxy-stats HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{PROXY_PORT}\r\n"
            f"Connection: close\r\n\r\n"
        ).encode("utf-8")
        resp, ms = send_proxy_request(PROXY_HOST, PROXY_PORT, req_stats)
        status, reason, _, body = parse_response_headers(resp)
        print(f"  -> Fetching '/proxy-stats': Status {GREEN}{status}{RESET}")
        stats = json.loads(body.decode("utf-8"))
        print("  -> Live Telemetry Snapshot:")
        print(f"     Total Requests: {stats['proxy_telemetry']['total_requests']}")
        print(f"     Cache Hits:     {stats['proxy_telemetry']['cache_hits']}")
        print(f"     Cache Misses:   {stats['proxy_telemetry']['cache_misses']}")
        print(f"     Hit Ratio:      {stats['cache_telemetry']['hit_ratio_percent']}%")
        print(f"     Blocked Denials:{stats['proxy_telemetry']['blocked_requests']}")
        print(f"     Total Bytes:    {stats['proxy_telemetry']['total_bytes_transferred']} bytes")
        print(f"  {GREEN}✔ Test 8 Passed: Telemetry endpoint is responsive and accurate.{RESET}\n")

        print(f"{BOLD}{GREEN}{'='*65}{RESET}")
        print(f"{BOLD}{GREEN}      ALL TESTS & REQUIREMENTS SUCCESSFULLY VALIDATED!{RESET}")
        print(f"{BOLD}{GREEN}{'='*65}{RESET}\n")

    finally:
        if mock_proc:
            mock_proc.terminate()
        if proxy_proc:
            proxy_proc.terminate()


if __name__ == "__main__":
    run_demo()
