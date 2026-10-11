"""
Mock Upstream HTTP Server for Local Offline Testing & Benchmarking.
Provides endpoints that test caching, non-caching, POST bodies, latency, and large payloads.
"""

import time
import argparse
from http.server import HTTPServer, BaseHTTPRequestHandler


class MockHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Silence default stderr spam during high-throughput tests
        pass

    def do_GET(self):
        if self.path == "/":
            body = b"Hello from Upstream Server!"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        elif self.path.startswith("/cacheable"):
            # Generates a response with dynamic timestamp and Cache-Control max-age=60
            ts = time.time()
            body = f'{{"message": "Cacheable data", "timestamp": {ts}}}'.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "public, max-age=60")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        elif self.path.startswith("/nocache"):
            ts = time.time()
            body = f'{{"message": "Strictly dynamic", "timestamp": {ts}}}'.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store, no-cache")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        elif self.path.startswith("/slow"):
            # Simulate 100ms upstream database/network delay
            time.sleep(0.1)
            body = b"Slow response finished."
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        elif self.path.startswith("/large"):
            # 512 KB payload for throughput testing
            data = b"X" * (512 * 1024)
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        elif self.path.startswith("/restricted"):
            body = b"You should not see this if proxy blocked it!"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        else:
            body = b"Not Found"
            self.send_response(404)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        post_data = self.rfile.read(length)
        resp = b"Echoed: " + post_data
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(resp)))
        self.end_headers()
        self.wfile.write(resp)


def run_mock_server(port: int = 9000):
    server_address = ("127.0.0.1", port)
    httpd = HTTPServer(server_address, MockHandler)
    print(f"[*] Mock Upstream Server running at http://127.0.0.1:{port}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=9000, help="Mock server port")
    args = parser.parse_args()
    run_mock_server(args.port)
