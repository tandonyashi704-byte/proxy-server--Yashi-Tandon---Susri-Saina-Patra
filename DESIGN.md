# Architecture & Design Document: PyProxy

## 1. System Overview

**PyProxy** is a concurrent, application-layer HTTP/HTTPS forward proxy designed to intercept, analyze, filter, cache, and tunnel web traffic between clients (such as web browsers or command-line utilities like `curl`) and upstream origin servers.

### High-Level Architecture Diagram

```
                 +-------------------------------------------------+
                 |                  PyProxy Core                   |
                 |                                                 |
  +--------+     |   +---------------+     +--------------------+  |     +------------------+
  | Client | ===>|   | Connection    |====>| Access Control &   |--|====>| Upstream Origin  |
  | (curl/ |     |   | Listener /    |     | Security Evaluator |  |     | Server (HTTP)    |
  |Browser)|<=== |   | Thread Pool   |<====| (Domain/Path/Rate) |  |     +------------------+
  +--------+     |   +-------+-------+     +---------+----------+  |
                 |           |                       |             |
                 |           v                       v             |
                 |   +---------------+     +--------------------+  |     +------------------+
                 |   | HTTPS CONNECT |     | Thread-Safe LRU    |  |     | Upstream Origin  |
                 |   | Tunnel Engine |     | Cache Subsystem    |  |====>| Server (HTTPS)   |
                 |   +-------+-------+     +---------+----------+  |     +------------------+
                 |           |                       |             |
                 |           +---------> Telemetry <-+             |
                 |                       Logger                    |
                 +-------------------------------------------------+
```

---

## 2. Protocol Engineering & Request Parsing

### A. HTTP/1.1 Request Parsing (RFC 9112)
Unlike naive proxy implementations that split headers purely on strings, PyProxy implements a robust stream parser:
1. **Framing & Demarcation:** The proxy reads bytes from the client socket until encountering the canonical header terminator sequence `\r\n\r\n` (or fallback `\n\n`).
2. **Request Line Extraction:** Parses `METHOD TARGET HTTP/VERSION`.
   - Handles both absolute URI targets (e.g., `GET http://example.com/index.html HTTP/1.1`) as sent by forward-proxy-aware clients, and relative targets (e.g., `GET /index.html HTTP/1.1`) with mandatory `Host:` header resolution.
3. **Header Normalization:** Stores header keys in lowercase to ensure case-insensitive lookups (RFC 7230 §3.2).
4. **Upstream Request Reconstruction:**
   - Strips proxy-specific headers (such as `Proxy-Connection`).
   - Normalizes target URI to a relative path on the request line as expected by origin servers.
   - Sets `Connection: close` to prevent connection hanging on upstream keep-alive streams.
   - Forwards request body payloads when `Content-Length` is present (supporting `POST`, `PUT`, `PATCH`).

### B. HTTPS Tunneling via the HTTP `CONNECT` Method (RFC 2817 / RFC 9110)
Standard HTTP proxies cannot inspect or decrypt end-to-end TLS/SSL traffic without acting as a Man-in-the-Middle (MITM) with forged root certificates (which triggers security warnings in browsers). Instead, PyProxy implements **Blind TCP Tunneling**:
1. The client issues a `CONNECT destination.com:443 HTTP/1.1`.
2. PyProxy validates `destination.com` against its Access Control list (blocking malicious or prohibited domains before any TCP connection is opened).
3. If allowed, PyProxy establishes a raw TCP socket to `destination.com:443`.
4. PyProxy sends `HTTP/1.1 200 Connection Established\r\n\r\n` to the client.
5. Sockets on both ends are set to non-blocking mode. PyProxy enters an asynchronous bidirectional streaming loop using `select.select()`, relaying raw encrypted TLS handshakes and application records back and forth until either peer terminates the connection.

---

## 3. Forward Proxy vs. Reverse Proxy: Trade-Off Analysis

| Architectural Dimension | Forward Proxy (PyProxy) | Reverse Proxy (e.g. NGINX, Cloudflare) |
| :--- | :--- | :--- |
| **Topology & Position** | Sits in front of **clients** (inside client's local network/firewall). | Sits in front of **servers** (inside origin's data center). |
| **Identity Visibility** | Clients are explicitly aware of the proxy (or transparent router); origin sees proxy IP. | Clients believe they are talking directly to the origin; origin servers are hidden. |
| **Primary Use Cases** | Corporate content filtering, egress traffic logging, bandwidth caching, bypassing geographic blocks, anonymization. | Server load balancing, SSL termination, DDoS protection, CDN edge caching, web application firewall (WAF). |
| **Routing Mechanism** | Routes to *arbitrary destinations on the entire Internet* based on client request URLs. | Routes to *pre-configured internal server clusters* based on hostnames or path prefixes. |

---

## 4. Concurrency Model & Thread Safety

### A. Thread Pool Execution (`concurrent.futures.ThreadPoolExecutor`)
- **Design Choice:** PyProxy utilizes a bounded worker thread pool (default 50–60 threads) to process client sockets concurrently.
- **Why Thread Pool over Thread-per-Client?**
  Spawning unbounded threads (`threading.Thread`) exposes the proxy to memory exhaustion and kernel thread scheduling thrashing under Denial-of-Service (DoS) conditions. A bounded thread pool guarantees bounded memory consumption while serving up to thousands of requests per second.
- **Why Multi-Threading over `asyncio`?**
  While `asyncio` is efficient for high connection counts with low compute, multi-threading provides true preemption when performing socket operations without requiring async rewriting of upstream socket pipelines, and handles DNS resolution (`getaddrinfo`) without blocking event loop threads.

### B. Synchronization & Critical Sections
1. **Cache Subsystem:** Protected by a re-entrant mutex (`threading.Lock`). Reads and writes to the underlying `OrderedDict` and metric counters are atomic.
2. **Rate Limiting Engine:** Protected by `threading.Lock` to guarantee atomic read-modify-write cycles on client token balances.
3. **Telemetry & Logging:** Synchronized thread-safe file writes and formatted stdout printing prevent log interleaving.

---

## 5. Caching Subsystem: Strategy & Trade-Offs

### A. Least Recently Used (LRU) Eviction
- Internal storage uses Python’s `collections.OrderedDict`, providing $O(1)$ lookups, insertions, and removals.
- When an entry is accessed (Cache HIT), it is moved to the end of the `OrderedDict`.
- When capacity is exceeded (`max_entries`), the oldest entry (least recently used) is evicted (`popitem(last=False)`).

### B. Time-to-Live (TTL) & Cache Invalidation Policy
- **TTL Expiration:** Each cache entry records `created_at` and `expires_at`.
- **HTTP/1.1 Header Semantics:**
  - Respects `Cache-Control: max-age=<seconds>` from the origin response.
  - Strictly respects `Cache-Control: no-store` and `Cache-Control: private` (never stored).
  - Only caches idempotent `GET` requests resulting in `200 OK`.
- **Telemetry Injection:**
  - On Cache HIT, PyProxy injects `X-Cache: HIT`, `X-Cache-Age: <seconds>`, and `X-Cache-Hits: <count>`.
  - On Cache MISS, PyProxy injects `X-Cache: MISS`.

### C. Trade-Offs in Caching Design
1. **Freshness vs. Consistency:** High TTL reduces origin load and delivers sub-millisecond response times, but risks serving stale data if the origin updates. PyProxy allows fine-grained TTL configuration.
2. **Memory vs. Disk Storage:** PyProxy uses an in-memory cache for raw speed (~0.08ms lookup latency). For multi-gigabyte video or binary caching, hybrid disk tiering would be preferred at the cost of disk I/O latency.

---

## 6. Access Control & Traffic Optimization

### A. Domain / Host Blacklisting with Wildcard Subdomains
- Evaluates destination hostnames against a blacklist.
- Supports exact matching (`blocked.com`) and wildcard domain patterns (`*.blocked.com`).
- Returns HTTP `403 Forbidden` with a custom branded HTML security notification.

### B. URL Path & Keyword Filtering
- Inspects URI paths for sensitive keywords or dangerous patterns (e.g., `/restricted*`, `*.badware`).
- Prevents clients from fetching restricted origin resources.

### C. Token Bucket Rate Limiting
- Employs the **Token Bucket Algorithm**:
  $$\text{tokens} = \min\left(\text{burst}, \text{tokens} + \Delta t \times \text{rate}\right)$$
- Tracks token buckets on a per-client-IP basis.
- Sustained rate: 15 req/sec; Burst tolerance: 25 requests.
- When tokens are depleted, subsequent requests are immediately rejected with `429 Too Many Requests` and a standard `Retry-After: <seconds>` header, protecting both the proxy and upstream networks from abusive flooding.

---

## 7. Robustness & Fault Tolerance

| Failure Condition | Detection Mechanism | Proxy Behavior & Recovery | Client Response |
| :--- | :--- | :--- | :--- |
| **Malformed Client Request** | `HTTPRequest.parse()` parser failure | Caught immediately before forwarding; socket closed cleanly. | `400 Bad Request` |
| **Prohibited Domain/Path** | AccessControl rule evaluation | Blocked before any upstream TCP handshake occurs. | `403 Forbidden` |
| **Rate Limit Exceeded** | Token bucket capacity check | Intercepted instantly without consuming origin bandwidth. | `429 Too Many Requests` |
| **Unreachable Host / DNS Failure** | `socket.gaierror`, `ConnectionRefusedError` | Caught cleanly in exception handler; no stack trace crash. | `502 Bad Gateway` |
| **Upstream Server Timeout** | `socket.timeout` (15s threshold) | Upstream socket closed; memory freed; logged as error. | `504 Gateway Timeout` |
| **Client Abrupt Disconnect** | `BrokenPipeError`, `ConnectionResetError` | Handled gracefully without lingering half-open sockets. | Connection closed |

---

## 8. College Viva & Examination Q&A Cheat Sheet

**Q1: What is a Forward Proxy and how does it differ from a Reverse Proxy?**
- *Answer:* A Forward Proxy acts on behalf of the **client** (sitting in the client network/browser) to control, filter, or cache egress traffic to the internet. A Reverse Proxy acts on behalf of the **origin server** (sitting in front of web servers) to load-balance, terminate SSL, or protect origin servers.

**Q2: How does HTTPS work through an HTTP proxy without decrypting user traffic?**
- *Answer:* The client sends an HTTP `CONNECT host:443 HTTP/1.1` request. The proxy opens a raw TCP connection to `host:443`, sends back `HTTP/1.1 200 Connection Established`, and then blindly relays raw encrypted bytes between the client and server using `select.select()` without decrypting them.

**Q3: How is concurrency achieved in `proxy_server.py`?**
- *Answer:* Using Python's `threading.Thread`. When the main loop accepts an incoming socket (`server_sock.accept()`), it spawns a daemon thread executing `handle_client(client_sock, client_addr)`. This allows hundreds of clients to be handled concurrently without blocking each other.

**Q4: How does in-memory caching work in your project?**
- *Answer:* Caches are stored in a thread-safe Python dictionary `CACHE[key] = {"response": bytes, "expires_at": float}`. Only successful HTTP `GET` requests with `200 OK` (and without `Cache-Control: no-store`) are cached. On subsequent requests before expiration, the response is served instantly from memory with header `X-Cache: HIT`, bypassing the upstream trip.

**Q5: How does your rate limiting work?**
- *Answer:* It logs request timestamps per client IP. If the count of requests within a sliding time window exceeds the threshold, the proxy immediately returns `429 Too Many Requests` with a `Retry-After` header.

**Q6: Why did you use a Thread Pool instead of creating a new thread for every client?**

- **Answer:** A Thread Pool reuses a fixed number of worker threads to handle client requests. It reduces the overhead of creating new threads repeatedly and prevents excessive resource consumption when many clients connect simultaneously. This makes PyProxy more efficient and manageable under heavy traffic.