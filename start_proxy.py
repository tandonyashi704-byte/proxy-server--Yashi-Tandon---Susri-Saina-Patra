#!/usr/bin/env python3
"""
Simple launcher for the Proxy Server.
"""

import argparse
from proxy_server import start_proxy_server


def main():
    parser = argparse.ArgumentParser(description="Multi-Threaded HTTP/HTTPS Proxy Server")
    parser.add_argument("-p", "--port", type=int, default=8888, help="Listening port (default: 8888)")
    parser.add_argument("-b", "--bind", type=str, default="127.0.0.1", help="Listening address (default: 127.0.0.1)")
    args, _ = parser.parse_known_args()

    start_proxy_server(host=args.bind, port=args.port)


if __name__ == "__main__":
    main()
