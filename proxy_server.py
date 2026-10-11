"""
Computer Networks Project: Multi-Threaded HTTP/HTTPS Proxy Server
Author: Student Submission
Description:
A simple forward proxy server built using Python's socket and threading modules.
Features:
1. Multi-threaded concurrency for multiple simultaneous clients
2. HTTP request parsing and forwarding (GET, POST, etc.)
3. HTTPS tunneling via HTTP CONNECT method
4. In-memory response caching (with TTL and Cache-Control check)
5. Access control (Domain blacklist and path blocking)
6. Simple client rate limiting
7. Standard Python logging and error handling (400, 403, 429, 502)
"""

import socket
import threading
import select
import time
import logging
from urllib.parse import urlparse

# Configure standard Python logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("ProxyServer")

# Proxy Configuration
PROXY_HOST = "127.0.0.1"
PROXY_PORT = 8888
BUFFER_SIZE = 8192
CACHE_DEFAULT_TTL = 60  # seconds

# Access Control Lists
BLOCKED_DOMAINS = [
    "blocked.com",
    "www.blocked.com",
    "ads.tracker.com",
    "malicious-site.com",
    "facebook.com",
]

BLOCKED_PATHS = [
    "/restricted-admin",
    "/secret",
]

# Simple In-Memory Cache: { "KEY": { "response": bytes, "expires_at": float } }
CACHE = {}
cache_lock = threading.Lock()

# Simple Metrics Tracking
METRICS = {
    "total_requests": 0,
    "cache_hits": 0,
    "cache_misses": 0,
    "blocked_requests": 0,
    "total_bytes_transferred": 0,
}
metrics_lock = threading.Lock()

# Simple Rate Limiter: { "CLIENT_IP": [timestamp1, timestamp2, ...] }
RATE_LIMIT_MAX_REQUESTS = 25  # max requests
RATE_LIMIT_WINDOW = 2.0       # within 2 seconds
CLIENT_REQUEST_LOG = {}
rate_limit_lock = threading.Lock()


def is_rate_limited(client_ip: str) -> bool:
    """Checks if a client has exceeded the allowed request rate."""
    import os
    if os.environ.get("DISABLE_RATE_LIMIT") == "1":
        return False

    now = time.time()
    with rate_limit_lock:
        if client_ip not in CLIENT_REQUEST_LOG:
            CLIENT_REQUEST_LOG[client_ip] = []

        # Filter out requests older than the time window
        recent_requests = [t for t in CLIENT_REQUEST_LOG[client_ip] if now - t < RATE_LIMIT_WINDOW]
        CLIENT_REQUEST_LOG[client_ip] = recent_requests

        if len(recent_requests) >= RATE_LIMIT_MAX_REQUESTS:
            return True

        CLIENT_REQUEST_LOG[client_ip].append(now)
        return False


def is_blocked(host: str, path: str) -> (bool, str):
    """Checks if destination host or path is blocked."""
    # Strip port from host if present
    clean_host = host.split(":")[0].lower()

    for blocked_domain in BLOCKED_DOMAINS:
        if clean_host == blocked_domain or clean_host.endswith("." + blocked_domain):
            return True, f"Domain '{clean_host}' is blocked by proxy access control policy"

    for blocked_path in BLOCKED_PATHS:
        if blocked_path in path.lower():
            return True, f"Path '{path}' is blocked by proxy content filtering policy"

    return False, ""


def handle_https_tunnel(client_sock: socket.socket, host: str, port: int, client_ip: str):
    """
    Handles HTTPS requests using HTTP CONNECT method (Blind TCP Tunneling).
    Relays encrypted TLS bytes back and forth between client and destination server.
    """
    try:
        upstream_sock = socket.create_connection((host, port), timeout=10)
        # Inform the client that tunnel is ready
        client_sock.sendall(b"HTTP/1.1 200 Connection Established\r\nProxy-Agent: SimpleProxy/1.0\r\n\r\n")
        logger.info(f"HTTPS Tunnel established for {client_ip} -> {host}:{port}")

        # Non-blocking bidirectional data relay
        sockets = [client_sock, upstream_sock]
        while True:
            readable, _, _ = select.select(sockets, [], sockets, 20.0)
            if not readable:
                break

            for s in readable:
                other = upstream_sock if s is client_sock else client_sock
                data = s.recv(BUFFER_SIZE)
                if not data:
                    return
                other.sendall(data)
    except Exception as e:
        logger.error(f"Error in HTTPS tunnel for {host}:{port}: {e}")
    finally:
        client_sock.close()


def handle_client(client_sock: socket.socket, client_addr: tuple):
    """
    Handles a single client connection in a separate thread.
    """
    client_ip = client_addr[0]
    client_sock.settimeout(10.0)

    try:
        # Step 1: Read the HTTP request from client
        raw_request = b""
        while True:
            chunk = client_sock.recv(BUFFER_SIZE)
            if not chunk:
                break
            raw_request += chunk
            if b"\r\n\r\n" in raw_request:
                break

        if not raw_request:
            client_sock.close()
            return

        # Step 2: Parse Request Line and Headers
        header_part = raw_request.split(b"\r\n\r\n")[0].decode("iso-8859-1", errors="ignore")
        body_part = raw_request[len(header_part) + 4:]
        lines = header_part.split("\r\n")
        if not lines or not lines[0]:
            client_sock.close()
            return

        request_line = lines[0].split()
        if len(request_line) < 2:
            client_sock.sendall(b"HTTP/1.1 400 Bad Request\r\n\r\nMalformed HTTP Request")
            client_sock.close()
            return

        method = request_line[0].upper()
        target = request_line[1]
        version = request_line[2] if len(request_line) > 2 else "HTTP/1.1"

        # Parse headers into a dictionary
        headers = {}
        for line in lines[1:]:
            if ":" in line:
                k, v = line.split(":", 1)
                headers[k.strip().lower()] = v.strip()

        # Step 3: Determine Host, Port, and Path
        if method == "CONNECT":
            # For HTTPS: target is "example.com:443"
            if ":" in target:
                host, port_str = target.split(":", 1)
                port = int(port_str)
            else:
                host = target
                port = 443
            path = ""
        elif target.startswith("http://") or target.startswith("https://"):
            parsed = urlparse(target)
            host = parsed.hostname or headers.get("host", "localhost")
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
            path = parsed.path or "/"
            if parsed.query:
                path += "?" + parsed.query
        else:
            host_header = headers.get("host", "localhost")
            if ":" in host_header:
                host, port_str = host_header.split(":", 1)
                port = int(port_str)
            else:
                host = host_header
                port = 80
        # Track total request count
        with metrics_lock:
            METRICS["total_requests"] += 1

        # Built-in Admin Stats endpoint
        if path in ("/proxy-stats", "/proxy-stats/"):
            import json
            with metrics_lock, cache_lock:
                total_cache = METRICS["cache_hits"] + METRICS["cache_misses"]
                ratio = round((METRICS["cache_hits"] / total_cache * 100), 2) if total_cache > 0 else 0.0
                stats_payload = {
                    "proxy_telemetry": {
                        "total_requests": METRICS["total_requests"],
                        "cache_hits": METRICS["cache_hits"],
                        "cache_misses": METRICS["cache_misses"],
                        "blocked_requests": METRICS["blocked_requests"],
                        "total_bytes_transferred": METRICS["total_bytes_transferred"],
                    },
                    "cache_telemetry": {
                        "total_entries": len(CACHE),
                        "hit_ratio_percent": ratio,
                    },
                }
            resp_body = json.dumps(stats_payload, indent=2).encode("utf-8")
            stats_resp = (
                f"HTTP/1.1 200 OK\r\n"
                f"Content-Type: application/json\r\n"
                f"Content-Length: {len(resp_body)}\r\n"
                f"Connection: close\r\n\r\n"
            ).encode("iso-8859-1") + resp_body
            client_sock.sendall(stats_resp)
            client_sock.close()
            return

        # Step 4: Rate Limiting Check
        if is_rate_limited(client_ip):
            with metrics_lock:
                METRICS["blocked_requests"] += 1
            logger.warning(f"Rate limit exceeded for {client_ip}")
            resp = (
                b"HTTP/1.1 429 Too Many Requests\r\n"
                b"Content-Type: text/plain\r\n"
                b"Retry-After: 10\r\n"
                b"Connection: close\r\n\r\n"
                b"429 Too Many Requests: Rate limit exceeded. Try again in 10 seconds."
            )
            client_sock.sendall(resp)
            client_sock.close()
            return

        # Step 5: Access Control / Blocklist Check
        blocked, reason = is_blocked(host, path)
        if blocked:
            logger.warning(f"BLOCKED: {client_ip} tried to access {host}{path} ({reason})")
            resp = (
                f"HTTP/1.1 403 Forbidden\r\n"
                f"Content-Type: text/plain\r\n"
                f"Connection: close\r\n\r\n"
                f"403 Forbidden: Access Denied. {reason}."
            ).encode("utf-8")
            client_sock.sendall(resp)
            client_sock.close()
            return

        # Step 6: Handle HTTPS CONNECT Method
        if method == "CONNECT":
            handle_https_tunnel(client_sock, host, port, client_ip)
            return

        # Step 7: Caching Check (Only for GET requests)
        cache_key = f"{method}:{host}:{port}:{path}"
        now = time.time()

        if method == "GET":
            with cache_lock:
                if cache_key in CACHE:
                    entry = CACHE[cache_key]
                    if now < entry["expires_at"]:
                        logger.info(f"CACHE HIT: {client_ip} -> {host}:{port}{path}")
                        # Send cached response with X-Cache: HIT
                        cached_resp = entry["response"]
                        # Inject header into cached response if not present
                        if b"X-Cache:" not in cached_resp:
                            header_end = cached_resp.find(b"\r\n\r\n")
                            if header_end != -1:
                                cached_resp = (
                                    cached_resp[:header_end]
                                    + b"\r\nX-Cache: HIT\r\n\r\n"
                                    + cached_resp[header_end + 4:]
                                )
                        with metrics_lock:
                            METRICS["cache_hits"] += 1
                        client_sock.sendall(cached_resp)
                        client_sock.close()
                        return
                    else:
                        # Expired entry
                        del CACHE[cache_key]

        # Step 8: Cache MISS or non-GET -> Forward request to upstream server
        with metrics_lock:
            METRICS["cache_misses"] += 1
        logger.info(f"FORWARDING (MISS): {client_ip} -> {method} {host}:{port}{path}")
        try:
            upstream_sock = socket.create_connection((host, port), timeout=10)

            # Build cleaned forward request (use relative path in request line)
            fwd_request_line = f"{method} {path} {version}\r\n"
            fwd_headers = dict(headers)
            fwd_headers["connection"] = "close"
            if "host" not in fwd_headers:
                fwd_headers["host"] = host

            fwd_header_str = "".join(f"{k.title()}: {v}\r\n" for k, v in fwd_headers.items())
            fwd_data = fwd_request_line.encode("iso-8859-1") + fwd_header_str.encode("iso-8859-1") + b"\r\n" + body_part

            upstream_sock.sendall(fwd_data)

            # Receive entire response from upstream
            upstream_response = b""
            while True:
                chunk = upstream_sock.recv(BUFFER_SIZE)
                if not chunk:
                    break
                upstream_response += chunk

            upstream_sock.close()

            if not upstream_response:
                client_sock.sendall(b"HTTP/1.1 502 Bad Gateway\r\n\r\nEmpty response from upstream server")
                client_sock.close()
                return

            # Check if response is cacheable (status 200 and no 'no-store')
            status_code = 502
            first_line = upstream_response.split(b"\r\n")[0].decode("iso-8859-1", errors="ignore")
            status_parts = first_line.split()
            if len(status_parts) >= 2 and status_parts[1].isdigit():
                status_code = int(status_parts[1])

            # Inject X-Cache: MISS header
            header_end = upstream_response.find(b"\r\n\r\n")
            if header_end != -1:
                response_to_send = (
                    upstream_response[:header_end]
                    + b"\r\nX-Cache: MISS\r\n\r\n"
                    + upstream_response[header_end + 4:]
                )
            else:
                response_to_send = upstream_response

            # Save in Cache if GET and 200 OK and not no-store
            if method == "GET" and status_code == 200 and b"no-store" not in upstream_response.lower():
                ttl = CACHE_DEFAULT_TTL
                # Extract max-age if present
                for line in upstream_response[:header_end].decode("iso-8859-1", errors="ignore").split("\r\n"):
                    if line.lower().startswith("cache-control:") and "max-age=" in line.lower():
                        try:
                            parts = line.split("max-age=")[1].split(",")[0].strip()
                            ttl = float(parts)
                        except Exception:
                            pass

                with cache_lock:
                    CACHE[cache_key] = {
                        "response": upstream_response[:header_end] + b"\r\nX-Cache: HIT\r\n\r\n" + upstream_response[header_end + 4:],
                        "expires_at": time.time() + ttl,
                    }

            client_sock.sendall(response_to_send)

        except (socket.gaierror, ConnectionRefusedError, socket.timeout) as err:
            logger.error(f"Cannot reach upstream server {host}:{port}: {err}")
            err_msg = f"HTTP/1.1 502 Bad Gateway\r\n\r\nCould not reach upstream host '{host}:{port}': {err}"
            client_sock.sendall(err_msg.encode("utf-8"))

    except Exception as e:
        logger.error(f"Error handling client {client_ip}: {e}")
    finally:
        try:
            client_sock.close()
        except Exception:
            pass


def start_proxy_server(host: str = PROXY_HOST, port: int = PROXY_PORT):
    """
    Main loop: Binds socket and spawns a thread for each incoming connection.
    """
    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_sock.bind((host, port))
    server_sock.listen(50)

    print("=" * 60)
    print(f"   Simple Multi-Threaded Proxy Server Running")
    print(f"   Listening on: http://{host}:{port}")
    print(f"   Cache TTL: {CACHE_DEFAULT_TTL} seconds")
    print(f"   Blocked Domains: {', '.join(BLOCKED_DOMAINS[:3])}...")
    print("=" * 60)
    logger.info(f"Proxy server listening at {host}:{port}")

    try:
        while True:
            client_sock, client_addr = server_sock.accept()
            # Handle each client in a separate thread (concurrency)
            t = threading.Thread(target=handle_client, args=(client_sock, client_addr))
            t.daemon = True
            t.start()
    except KeyboardInterrupt:
        logger.info("Stopping proxy server...")
    finally:
        server_sock.close()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Simple Multi-Threaded HTTP/HTTPS Proxy")
    parser.add_argument("-p", "--port", type=int, default=8888, help="Proxy port (default: 8888)")
    parser.add_argument("-b", "--bind", type=str, default="127.0.0.1", help="Proxy bind host")
    args = parser.parse_args()
    start_proxy_server(args.bind, args.port)
