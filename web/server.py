#!/usr/bin/env python3
"""
High-performance local web server for the minimal-reproducer reducer studio.
Provides:
  - Web UI at http://127.0.0.1:8765
  - POST /api/reduce : Runs delta debugging with optional --localize
  - GET  /api/status/<job_id> : Streams live logs, stats, and results
  - GET  /api/presets : Returns curated test fixtures
  - GET  /api/download/zip : Serves the downloadable reducer-cli.zip archive
Zero external dependencies (Python standard library only).
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import uuid
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(HERE)
REDUCE = os.path.join(PROJECT_ROOT, "reduce.py")
ZIP_PATH = os.path.join(PROJECT_ROOT, "reducer-cli.zip")
if not os.path.exists(ZIP_PATH):
    ZIP_PATH = os.path.join(os.path.dirname(PROJECT_ROOT), "reducer-cli.zip")

JOBS = {}


def _run(job_id, files, target, runner, max_iters, timeout, localize):
    job = JOBS[job_id]
    workdir = tempfile.mkdtemp(prefix="reduce_web_")
    try:
        for name, content in files:
            target_path = os.path.join(workdir, os.path.basename(name))
            with open(target_path, "w", encoding="utf-8") as f:
                f.write(content)

        cmd = [
            sys.executable,
            REDUCE,
            target,
            "--timeout", str(timeout),
            "--max-iterations", str(max_iters),
            "--runner", runner,
        ]
        if localize:
            cmd.append("--localize")

        job["log"].append(f"$ {' '.join(cmd)}")

        proc = subprocess.Popen(
            cmd,
            cwd=workdir,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )

        suspects, stats, current, hot = [], {}, "", False
        for raw in proc.stdout:
            line = raw.rstrip("\n")
            job["log"].append(line)

            if line.startswith("Suspects"):
                hot = True
                continue
            if hot:
                if line.startswith("-- "):
                    current = line.strip("- ").strip()
                else:
                    p = line.split(None, 2)
                    if len(p) == 3 and p[0].isdigit():
                        suspects.append({
                            "file": current,
                            "line": int(p[0]),
                            "count": p[1],
                            "text": p[2],
                        })
                continue

            for k in (
                "Original line count:",
                "Final line count:",
                "Lines removed:",
                "Iterations used:",
            ):
                if line.startswith(k):
                    stats[k.rstrip(":")] = line.split(":", 1)[1].strip()

        proc.wait()
        if proc.returncode != 0:
            job["status"] = "error"
            job["error_message"] = "Test file did not fail with genuine test failure on initial run."
        else:
            min_file = os.path.join(workdir, target + ".min")
            if os.path.exists(min_file):
                with open(min_file, "r", encoding="utf-8") as f:
                    min_content = f.read()
            else:
                min_content = ""

            job["result"] = {
                "min": min_content,
                "suspects": suspects,
                "stats": stats,
            }
            job["status"] = "done"

    except Exception as exc:
        job["log"].append(f"Error: {exc}")
        job["status"] = "error"
        job["error_message"] = str(exc)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


PRESETS = [
    {
        "id": "sample-math",
        "name": "Math Operations Suite (50 lines)",
        "description": "50-line test suite with arithmetic helpers & passing tests, and 1 failing percentage test.",
        "target": "test_math.py",
        "runner": "unittest",
        "files": [
            {
                "name": "test_math.py",
                "content": '''import unittest


def add(a, b):
    return a + b


def subtract(a, b):
    return a - b


def multiply(a, b):
    return a * b


def buggy_percentage(part, total):
    # Bug: returns wrong calculation (off by 10x)
    return (part / total) * 10


class TestMathModule(unittest.TestCase):

    def test_add_positive(self):
        self.assertEqual(add(2, 3), 5)

    def test_add_zero(self):
        self.assertEqual(add(5, 0), 5)

    def test_subtract_positive(self):
        self.assertEqual(subtract(10, 4), 6)

    def test_subtract_zero(self):
        self.assertEqual(subtract(7, 0), 7)

    def test_multiply_positive(self):
        self.assertEqual(multiply(3, 4), 12)

    def test_multiply_zero(self):
        self.assertEqual(multiply(8, 0), 0)

    def test_failing_percentage(self):
        # 50 out of 100 should be 50%, but buggy_percentage returns 5.0
        self.assertEqual(buggy_percentage(50, 100), 50.0)

    def test_subtract_negative(self):
        self.assertEqual(subtract(2, 5), -3)


if __name__ == "__main__":
    unittest.main()
'''
            }
        ]
    },
    {
        "id": "string-parser",
        "name": "String Parser Suite (42 lines)",
        "description": "Text parser with slugify, titlecase, and a failing regex validation test.",
        "target": "test_parser.py",
        "runner": "unittest",
        "files": [
            {
                "name": "test_parser.py",
                "content": r'''import unittest
import re


def slugify(text):
    return re.sub(r'[^a-zA-Z0-9]+', '-', text).strip('-').lower()


def truncate(text, length):
    return text[:length] + '...' if len(text) > length else text


def parse_email(email):
    # Flawed email validator regex (fails on dot in username)
    pattern = r'^[a-zA-Z0-9]+@[a-zA-Z0-9]+\.[a-zA-Z]+$'
    return bool(re.match(pattern, email))


class TestParser(unittest.TestCase):

    def test_slugify_simple(self):
        self.assertEqual(slugify("Hello World"), "hello-world")

    def test_slugify_special(self):
        self.assertEqual(slugify("Foo & Bar"), "foo-bar")

    def test_truncate_short(self):
        self.assertEqual(truncate("Short", 10), "Short")

    def test_truncate_long(self):
        self.assertEqual(truncate("Very Long Text", 5), "Very ...")

    def test_email_valid(self):
        self.assertTrue(parse_email("user@example.com"))

    def test_email_dotted_failure(self):
        # Bug: fails because regex does not support dots in local part
        self.assertTrue(parse_email("john.doe@example.com"))


if __name__ == "__main__":
    unittest.main()
'''
            }
        ]
    },
    {
        "id": "calculator-multifile",
        "name": "Multi-File: Calculator + Test Suite",
        "description": "Multi-file project with calculator.py and test_calculator.py demonstrating localization across project files.",
        "target": "test_calculator.py",
        "runner": "unittest",
        "files": [
            {
                "name": "calculator.py",
                "content": '''def calculate_discount(price, discount_percent):
    if discount_percent < 0 or discount_percent > 100:
        raise ValueError("Invalid discount")
    # Bug: applies addition instead of subtraction
    return price + (price * (discount_percent / 100))


def format_currency(amount):
    return f"${amount:.2f}"
'''
            },
            {
                "name": "test_calculator.py",
                "content": '''import unittest
from calculator import calculate_discount, format_currency


class TestCalculator(unittest.TestCase):

    def test_format_currency(self):
        self.assertEqual(format_currency(19.99), "$19.99")

    def test_format_zero(self):
        self.assertEqual(format_currency(0), "$0.00")

    def test_discount_invalid(self):
        with self.assertRaises(ValueError):
            calculate_discount(100, -5)

    def test_discount_calculation(self):
        # $100 with 20% discount should be $80, but buggy calculate_discount returns $120
        self.assertEqual(calculate_discount(100, 20), 80.0)


if __name__ == "__main__":
    unittest.main()
'''
            }
        ]
    }
]


class StudioRequestHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Suppress noisy request logging to stdout
        pass

    def send_cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_cors_headers()
        self.end_headers()

    def send_json(self, code, payload):
        data = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_cors_headers()
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path in ("/", "/index.html"):
            index_file = os.path.join(HERE, "index.html")
            if os.path.exists(index_file):
                with open(index_file, "rb") as f:
                    content = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(content)))
                self.send_cors_headers()
                self.end_headers()
                self.wfile.write(content)
            else:
                self.send_json(404, {"error": "index.html not found"})
            return

        if path == "/api/presets":
            return self.send_json(200, PRESETS)

        if path.startswith("/api/status/"):
            job_id = path.rsplit("/", 1)[-1]
            job = JOBS.get(job_id)
            if job:
                return self.send_json(200, job)
            return self.send_json(404, {"error": "Job not found"})

        if path in ("/api/download/zip", "/download/reducer-cli.zip"):
            if os.path.exists(ZIP_PATH):
                with open(ZIP_PATH, "rb") as f:
                    zip_data = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "application/zip")
                self.send_header("Content-Disposition", 'attachment; filename="reducer-cli.zip"')
                self.send_header("Content-Length", str(len(zip_data)))
                self.send_cors_headers()
                self.end_headers()
                self.wfile.write(zip_data)
                return
            return self.send_json(404, {"error": "Archive not found on disk"})

        self.send_json(404, {"error": "Not found"})

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path != "/api/reduce":
            return self.send_json(404, {"error": "Endpoint not found"})

        try:
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length) if length > 0 else b"{}"
            payload = json.loads(body.decode("utf-8"))
        except Exception as e:
            return self.send_json(400, {"error": f"Invalid JSON: {e}"})

        files = payload.get("files") or []
        if not files:
            return self.send_json(400, {"error": "No files provided"})

        # Normalization: files can be [name, content] tuples or objects {name, content}
        norm_files = []
        for item in files:
            if isinstance(item, (list, tuple)) and len(item) == 2:
                norm_files.append((item[0], item[1]))
            elif isinstance(item, dict) and "name" in item and "content" in item:
                norm_files.append((item["name"], item["content"]))

        if not norm_files:
            return self.send_json(400, {"error": "Empty file list"})

        orig_map = {os.path.basename(name): content for name, content in norm_files}
        target = payload.get("target")
        if not target or target not in orig_map:
            # Pick first file matching test_*.py or *test*.py, else first file
            target = next((k for k in orig_map if k.startswith("test_") or "test" in k), next(iter(orig_map)))

        runner = payload.get("runner", "unittest")
        max_iters = int(payload.get("max_iterations", 200))
        timeout = float(payload.get("timeout", 3.0))
        localize = bool(payload.get("localize", True))

        job_id = uuid.uuid4().hex
        JOBS[job_id] = {
            "id": job_id,
            "status": "running",
            "log": [],
            "result": None,
            "target": target,
            "original": orig_map[target],
        }

        thread = threading.Thread(
            target=_run,
            args=(job_id, norm_files, target, runner, max_iters, timeout, localize),
            daemon=True,
        )
        thread.start()

        return self.send_json(200, {"id": job_id, "target": target})


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    server_address = ("127.0.0.1", port)
    httpd = ThreadingHTTPServer(server_address, StudioRequestHandler)
    print(f"[*] Reducer Studio web server listening at http://127.0.0.1:{port}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[*] Stopping web server...")
        httpd.server_close()


if __name__ == "__main__":
    main()
