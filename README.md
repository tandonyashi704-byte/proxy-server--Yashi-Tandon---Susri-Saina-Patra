# Computer Networks Project: HTTP/HTTPS Proxy Server

A clean, multi-threaded HTTP and HTTPS forward proxy server written in Python using basic socket programming and threading. 

This project fulfills all coursework requirements:
- **Concurrent Connections:** Handles multiple clients simultaneously using threads.
- **Protocol Support:** Parses and forwards HTTP requests (GET, POST, etc.) and supports HTTPS tunneling via the HTTP `CONNECT` method.
- **In-Memory Caching:** Caches responses based on `Cache-Control` / `max-age` and adds `X-Cache: HIT` / `X-Cache: MISS` headers.
- **Access Control:** Blocks specific domains (`BLOCKED_DOMAINS`) and sensitive URL paths (`BLOCKED_PATHS`).
- **Rate Limiting:** Protects the proxy from spam requests (returns `429 Too Many Requests`).
- **FastAPI Integration:** Includes a realistic FastAPI test server and client demonstration.
- **Performance Benchmarking:** Measures latency and throughput (Direct vs. Proxy Miss vs. Cache Hit).

---

## 📁 Project Files Overview

```
.
├── proxy_server.py       # Core proxy server (sockets, threading, caching, blocklist)
├── fastapi_server.py     # FastAPI upstream server (for testing GET, POST, latency)
├── client.py             # Client script: Talks directly to FastAPI, then through Proxy
├── demo.py               # Complete automated test suite (verifies all features)
├── benchmark.py          # Latency & throughput benchmark script
├── run_all.sh            # One-click script to run client demo, test suite, and benchmark
├── DESIGN.md             # Project design & viva explanation write-up
├── BENCHMARK_RESULTS.md  # Benchmark numbers and performance report
└── README.md             # Project documentation and guide
```

---

## 🚀 Quickstart Guide

### 1. Requirements
- Python 3.8+
- FastAPI, Uvicorn, and Requests:
  ```bash
  pip install fastapi uvicorn requests
  ```

### 2. Run the FastAPI Client Demo (Direct vs. Proxied)
This is the primary demonstration showing the client talking directly to FastAPI first, and then through the proxy:
```bash
python3 client.py
```
**What this does:**
1. Starts FastAPI on port `8000` and Proxy on port `8888`.
2. **Phase 1 (Direct):** Client sends requests directly to `http://127.0.0.1:8000`.
3. **Phase 2 (Proxied):** Client sends the exact same requests through `http://127.0.0.1:8888`.
4. Shows how **Cache HIT** drops response time on the slow endpoint from **150ms down to ~1.5ms**!
5. Shows that `/restricted-admin` is blocked with **403 Forbidden**.

### 3. Run the Automated Test Suite
To verify all 8 features (GET, POST, Caching, Domain Block, Path Block, Rate Limiter, 502/400 errors, HTTPS Tunneling):
```bash
python3 demo.py
```

### 4. Run the Performance Benchmark
To measure latency and requests-per-second:
```bash
python3 benchmark.py
```

### 5. Run Everything with One Command
```bash
./run_all.sh
```

---

## 🖥️ Manual Testing Guide

### Step 1: Start the Proxy Server
Open a terminal and run:
```bash
python3 proxy_server.py --port 8888
```

### Step 2: Test with `curl` or Browser

**1. Normal HTTP Request:**
```bash
curl -i -x http://127.0.0.1:8888 http://httpforever.com/
```

**2. Test Caching (Cache Miss vs. Cache Hit):**
```bash
# 1st call -> X-Cache: MISS (fetches from origin)
curl -i -x http://127.0.0.1:8888 http://127.0.0.1:8000/cacheable

# 2nd call -> X-Cache: HIT (instant response from proxy memory)
curl -i -x http://127.0.0.1:8888 http://127.0.0.1:8000/cacheable
```

**3. Test HTTPS Tunneling:**
```bash
curl -i -x http://127.0.0.1:8888 https://www.google.com
```

**4. Test Domain Blocking (403 Forbidden):**
```bash
curl -i -x http://127.0.0.1:8888 http://blocked.com/
```

**5. Test Rate Limiting (429 Too Many Requests):**
Send a rapid burst of requests:
```bash
for i in {1..30}; do curl -s -x http://127.0.0.1:8888 http://127.0.0.1:8000/ & done
```

---

## 💡 How the Code Works (For Viva & Evaluation)

### 1. Concurrency (Handling Multiple Clients)
In `proxy_server.py`, the server runs a standard `while True` accept loop:
```python
client_sock, client_addr = server_sock.accept()
t = threading.Thread(target=handle_client, args=(client_sock, client_addr))
t.daemon = True
t.start()
```
Whenever a client connects, a new lightweight thread is spawned to handle that client's request independently, so one slow client never blocks other clients.

### 2. Request Parsing
The proxy reads raw bytes from the socket, decodes the HTTP text, and splits lines by `\r\n`:
- Line 1 gives: `METHOD`, `TARGET (URL)`, and `VERSION`.
- Remaining lines are parsed into a `headers = {}` dictionary.

### 3. HTTPS Tunneling (`CONNECT` Method)
When a browser asks for HTTPS (`CONNECT example.com:443 HTTP/1.1`), the proxy cannot inspect encrypted traffic directly. Instead:
1. The proxy connects to `example.com:443`.
2. Sends `HTTP/1.1 200 Connection Established` back to the client.
3. Uses `select.select()` to blindly forward raw bytes in both directions between client and server.

### 4. In-Memory Caching
The cache is a simple Python dictionary:
```python
CACHE = {
    "key": {
        "response": bytes,
        "expires_at": float
    }
}
```
Protected by a `threading.Lock()`. If a `GET` request comes in and `now < expires_at`, the proxy immediately returns the cached bytes with `X-Cache: HIT` without making any upstream network connection.

### 5. Access Control & Rate Limiting
- **Domain Blocking:** Checks destination host against `BLOCKED_DOMAINS = ["blocked.com", ...]` &rarr; returns `403 Forbidden`.
- **Path Blocking:** Checks URL path against `BLOCKED_PATHS = ["/restricted-admin", ...]` &rarr; returns `403 Forbidden`.
- **Rate Limiting:** Keeps a list of timestamps for each client IP. If a client exceeds 25 requests in 2 seconds, returns `429 Too Many Requests`.

---

## 📊 Summary Comparison: Direct vs. Proxied

| Request Type | Direct to FastAPI | Through Proxy | Notes |
| :--- | :---: | :---: | :--- |
| **GET / (Health Check)** | ~20 ms | ~3 ms | Forwarded properly |
| **POST /items (JSON)** | ~1.5 ms | ~2.0 ms | Body payload forwarded |
| **GET /cacheable (Miss)** | ~1.5 ms | ~2.0 ms | `X-Cache: MISS` header |
| **GET /cacheable (Hit)** | ~1.5 ms | **~0.9 ms** | `X-Cache: HIT` from memory |
| **GET /slow (150ms query)** | ~156 ms | **~1.7 ms** | **90x speedup via cache!** |
| **GET /restricted-admin** | 200 OK | **403 Forbidden** | Blocked by proxy filter |
| **GET http://blocked.com/** | N/A | **403 Forbidden** | Blocked by domain filter |
