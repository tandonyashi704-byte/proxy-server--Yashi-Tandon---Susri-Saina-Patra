#!/bin/bash
# Convenience script to run all tests, FastAPI client demo, and benchmarks for PyProxy

set -e

echo "=========================================================="
echo "          PyProxy All-in-One Runner Script                "
echo "=========================================================="
echo ""

echo "Step 1: Running FastAPI Direct vs Proxied Client Demo..."
python3 client.py

echo ""
echo "Step 2: Running Protocol & Security Verification Test Suite..."
python3 demo.py

echo ""
echo "Step 3: Running Performance & Overhead Benchmark Suite..."
python3 benchmark.py

echo ""
echo "=========================================================="
echo "✔ All tests and benchmarks executed successfully!"
echo "• See DESIGN.md for architecture write-up"
echo "• See BENCHMARK_RESULTS.md for performance report"
echo "• Run 'python3 start_proxy.py' to launch proxy for browser"
echo "=========================================================="
