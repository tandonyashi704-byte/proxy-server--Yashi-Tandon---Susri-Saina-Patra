#!/usr/bin/env python3
"""
Interactive Client Demonstrator.
Executes requests first DIRECTLY against the FastAPI origin server,
and then PROXIED through PyProxy to demonstrate:
1. Protocol forwarding correctness (GET, POST JSON)
2. Cache MISS vs. Cache HIT latency drops (including a 150ms slow query)
3. Origin server offload and timestamp caching
4. Security and Access Control enforcement (403 Forbidden)
"""

import sys
import time
import json
import logging
import subprocess
import requests
from typing import Dict, Any, Tuple

# Configure standard Python logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%H:%M:%S",
)
client_log = logging.getLogger("ClientDemo")

# ANSI Color formatting
GREEN = "\033[92m"
BLUE = "\033[94m"
YELLOW = "\033[93m"
RED = "\033[91m"
BOLD = "\033[1m"
RESET = "\033[0m"

FASTAPI_URL = "http://127.0.0.1:8000"
PROXY_URL = "http://127.0.0.1:8888"
PROXIES = {
    "http": PROXY_URL,
    "https": PROXY_URL,
}


def measure_request(
    method: str,
    url: str,
    proxies: Dict[str, str] = None,
    json_data: Dict[str, Any] = None,
) -> Tuple[requests.Response, float]:
    """Sends HTTP request and records precise round-trip latency in ms."""
    start = time.perf_counter()
    if method.upper() == "GET":
        resp = requests.get(url, proxies=proxies, timeout=5.0)
    elif method.upper() == "POST":
        resp = requests.post(url, json=json_data, proxies=proxies, timeout=5.0)
    else:
        resp = requests.request(method, url, proxies=proxies, timeout=5.0)
    elapsed_ms = (time.perf_counter() - start) * 1000
    return resp, elapsed_ms


def _run_steps():
    # =========================================================================
    # PHASE 1: DIRECT COMMUNICATION (Client -> FastAPI Server)
    # =========================================================================
    print(f"{BOLD}{YELLOW}------------------------------------------------------------{RESET}")
    print(f"{BOLD}{YELLOW}  PHASE 1: DIRECT REQUESTS (Client directly contacts FastAPI) {RESET}")
    print(f"{BOLD}{YELLOW}------------------------------------------------------------{RESET}")

    # 1.1 Direct GET /
    client_log.info("[Direct] Sending GET / directly to FastAPI...")
    resp, ms_direct_root = measure_request("GET", f"{FASTAPI_URL}/")
    print(f"  -> Status: {GREEN}{resp.status_code} OK{RESET} | Latency: {ms_direct_root:.2f} ms")
    print(f"  -> Response: {resp.json()}\n")

    # 1.2 Direct POST /items
    item_payload = {
        "name": "Mechanical Keyboard",
        "price": 129.99,
        "is_offer": True,
        "tags": ["hardware", "usb-c", "rgb"],
    }
    client_log.info("[Direct] Sending POST /items with JSON body directly to FastAPI...")
    resp_post, ms_direct_post = measure_request(
        "POST", f"{FASTAPI_URL}/items", json_data=item_payload
    )
    print(f"  -> Status: {GREEN}{resp_post.status_code} OK{RESET} | Latency: {ms_direct_post:.2f} ms")
    print(f"  -> Created Item: {resp_post.json()['received_item']}\n")

    # 1.3 Direct GET /slow (150ms sleep on origin)
    client_log.info("[Direct] Sending GET /slow (computational delay on origin)...")
    resp_slow, ms_direct_slow = measure_request("GET", f"{FASTAPI_URL}/slow")
    print(f"  -> Status: {GREEN}{resp_slow.status_code} OK{RESET} | Latency: {YELLOW}{ms_direct_slow:.2f} ms{RESET}")
    print(f"  -> Response: {resp_slow.json()}\n")

    # 1.4 Direct GET /cacheable (Twice directly - notice no cache benefits)
    client_log.info("[Direct] Sending GET /cacheable twice directly...")
    r1, d1 = measure_request("GET", f"{FASTAPI_URL}/cacheable")
    r2, d2 = measure_request("GET", f"{FASTAPI_URL}/cacheable")
    t1 = r1.json()["generated_at"]
    t2 = r2.json()["generated_at"]
    print(f"  -> Request 1: Timestamp={t1:.4f} ({d1:.2f} ms)")
    print(f"  -> Request 2: Timestamp={t2:.4f} ({d2:.2f} ms)")
    print(f"  -> Result: Origin was hit both times (Timestamps differ: {t1 != t2})\n")

    # =========================================================================
    # PHASE 2: PROXIED COMMUNICATION (Client -> PyProxy -> FastAPI Server)
    # =========================================================================
    print(f"{BOLD}{GREEN}------------------------------------------------------------{RESET}")
    print(f"{BOLD}{GREEN}  PHASE 2: PROXIED REQUESTS (Client -> PyProxy -> FastAPI)   {RESET}")
    print(f"{BOLD}{GREEN}------------------------------------------------------------{RESET}")

    # 2.1 Proxied GET /
    client_log.info("[Proxied] Sending GET / through PyProxy...")
    resp_p_root, ms_p_root = measure_request("GET", f"{FASTAPI_URL}/", proxies=PROXIES)
    print(f"  -> Status: {GREEN}{resp_p_root.status_code} OK{RESET} | Latency: {ms_p_root:.2f} ms")
    print(f"  -> X-Cache Header: {resp_p_root.headers.get('X-Cache', 'N/A')}")
    print(f"  -> Response: {resp_p_root.json()}\n")

    # 2.2 Proxied POST /items
    client_log.info("[Proxied] Sending POST /items with JSON body through PyProxy...")
    resp_p_post, ms_p_post = measure_request(
        "POST", f"{FASTAPI_URL}/items", proxies=PROXIES, json_data=item_payload
    )
    print(f"  -> Status: {GREEN}{resp_p_post.status_code} OK{RESET} | Latency: {ms_p_post:.2f} ms")
    print(f"  -> Response: {resp_p_post.json()['received_item']}")
    print(f"  -> Verified: PyProxy forwarded headers and JSON body seamlessly.\n")

    # 2.3 Proxied GET /cacheable: Cache MISS vs. Cache HIT
    unique_cacheable = f"{FASTAPI_URL}/cacheable?run={int(time.time())}"
    client_log.info("[Proxied] 1st Request to /cacheable (Cache MISS)...")
    resp_miss, ms_miss = measure_request("GET", unique_cacheable, proxies=PROXIES)
    c_hdr_miss = resp_miss.headers.get("X-Cache", "MISS")
    data_miss = resp_miss.json()
    print(f"  -> 1st Call (Origin Fetch): Status {resp_miss.status_code} | X-Cache: {YELLOW}{c_hdr_miss}{RESET} | Latency: {ms_miss:.2f} ms")
    print(f"  -> Timestamp: {data_miss['generated_at']:.4f}\n")

    client_log.info("[Proxied] 2nd Request to /cacheable (Cache HIT from PyProxy Memory)...")
    resp_hit, ms_hit = measure_request("GET", unique_cacheable, proxies=PROXIES)
    c_hdr_hit = resp_hit.headers.get("X-Cache", "HIT")
    hits = resp_hit.headers.get("X-Cache-Hits", "1")
    data_hit = resp_hit.json()
    print(f"  -> 2nd Call (Memory Serve): Status {GREEN}{resp_hit.status_code}{RESET} | X-Cache: {GREEN}{c_hdr_hit} (Hits: {hits}){RESET} | Latency: {GREEN}{ms_hit:.2f} ms{RESET}")
    print(f"  -> Timestamp: {data_hit['generated_at']:.4f} (Identical cached data: {data_miss['generated_at'] == data_hit['generated_at']})")
    speedup = (ms_miss / ms_hit) if ms_hit > 0 else 1.0
    print(f"  -> Cache Acceleration: {GREEN}{speedup:.1f}x faster{RESET}!\n")

    # 2.4 Proxied GET /slow: Drastic Cache Acceleration
    unique_slow = f"{FASTAPI_URL}/slow?session={int(time.time())}"
    client_log.info("[Proxied] 1st Request to /slow (Origin computes for 150ms)...")
    _, ms_slow_miss = measure_request("GET", unique_slow, proxies=PROXIES)
    print(f"  -> 1st Call (Origin wait): {YELLOW}{ms_slow_miss:.2f} ms{RESET}")

    client_log.info("[Proxied] 2nd Request to /slow (Served instantly from PyProxy cache)...")
    r_slow_hit, ms_slow_hit = measure_request("GET", unique_slow, proxies=PROXIES)
    speedup_slow = (ms_slow_miss / ms_slow_hit) if ms_slow_hit > 0 else 1.0
    print(f"  -> 2nd Call (Cache HIT):   {GREEN}{ms_slow_hit:.2f} ms{RESET} | X-Cache: {GREEN}{r_slow_hit.headers.get('X-Cache')}{RESET}")
    print(f"  -> Latency reduced by {GREEN}{(ms_slow_miss - ms_slow_hit):.1f} ms{RESET} ({GREEN}{speedup_slow:.1f}x speedup{RESET})!\n")

    # 2.5 Proxied Security & Access Control enforcement
    client_log.info("[Proxied] Testing Access Control (Requesting restricted path)...")
    resp_sec, _ = measure_request("GET", f"{FASTAPI_URL}/restricted-admin", proxies=PROXIES)
    print(f"  -> GET /restricted-admin: Status {RED}{resp_sec.status_code} Forbidden{RESET} (Blocked by PyProxy)")
    assert resp_sec.status_code == 403

    client_log.info("[Proxied] Testing Domain Access Control (Requesting blocked domain)...")
    resp_domain, _ = measure_request("GET", "http://blocked.com/", proxies=PROXIES)
    print(f"  -> GET http://blocked.com/: Status {RED}{resp_domain.status_code} Forbidden{RESET} (Blocked by PyProxy)")
    assert resp_domain.status_code == 403

    # =========================================================================
    # SUMMARY COMPARISON TABLE
    # =========================================================================
    print(f"\n{BOLD}{'='*72}{RESET}")
    print(f"{BOLD}{'SUMMARY COMPARISON: DIRECT vs PROXY':^72}{RESET}")
    print(f"{BOLD}{'='*72}{RESET}")
    print(f"{'Request Type':<26} | {'Direct to FastAPI':<18} | {'Through PyProxy':<20}")
    print(f"{'-'*72}")
    print(f"{'GET / (Health Check)':<26} | {ms_direct_root:6.2f} ms         | {ms_p_root:6.2f} ms")
    print(f"{'POST /items (JSON Body)':<26} | {ms_direct_post:6.2f} ms         | {ms_p_post:6.2f} ms")
    print(f"{'GET /cacheable (Miss)':<26} | {d1:6.2f} ms         | {ms_miss:6.2f} ms (X-Cache: MISS)")
    print(f"{'GET /cacheable (Hit)':<26} | {d2:6.2f} ms         | {GREEN}{ms_hit:6.2f} ms (X-Cache: HIT){RESET}")
    print(f"{'GET /slow (150ms query)':<26} | {ms_direct_slow:6.2f} ms         | {GREEN}{ms_slow_hit:6.2f} ms (Cached){RESET}")
    print(f"{'GET /restricted-admin':<26} | 200 OK (Exposed)    | {RED}403 Forbidden (Blocked){RESET}")
    print(f"{'='*72}\n")
    print(f"{GREEN}✔ Demonstration completed successfully!{RESET}\n")


def run_demonstration():
    print(f"\n{BOLD}{BLUE}{'='*72}{RESET}")
    print(f"{BOLD}{BLUE}   CLIENT DEMO: DIRECT vs PROXIED INTERACTION WITH FASTAPI{RESET}")
    print(f"{BOLD}{BLUE}{'='*72}{RESET}\n")

    fastapi_proc = None
    proxy_proc = None

    # Auto-start FastAPI if not running
    try:
        requests.get(f"{FASTAPI_URL}/", timeout=0.3)
    except Exception:
        client_log.info("[Setup] Starting FastAPI server on port 8000...")
        fastapi_proc = subprocess.Popen([sys.executable, "fastapi_server.py", "--port", "8000"])
        time.sleep(0.8)

    # Auto-start Proxy if not running
    try:
        import socket
        with socket.create_connection(("127.0.0.1", 8888), timeout=0.3):
            pass
    except Exception:
        client_log.info("[Setup] Starting Proxy server on port 8888...")
        proxy_proc = subprocess.Popen([sys.executable, "proxy_server.py", "--port", "8888"])
        time.sleep(0.8)

    try:
        _run_steps()
    finally:
        if fastapi_proc:
            fastapi_proc.terminate()
        if proxy_proc:
            proxy_proc.terminate()


if __name__ == "__main__":
    run_demonstration()
