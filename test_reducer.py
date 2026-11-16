#!/usr/bin/env python3
"""
Unit and integration tests for the minimal-reproducer reducer CLI.
Tests coverage:
1. Already-passing input (exits 1)
2. Single-line file (handles gracefully, exits 0)
3. Reduces to near-nothing (shrinks 40+ lines to under 10 lines, exits 0)
4. Timeout handling (kills at --timeout, treats timeout as not-failing)
5. Iteration cap being respected (--max-iterations strictly enforced)
6. Preserves import header block
7. Never modifies input file
"""

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

try:
    import pytest  # noqa: F401
    HAVE_PYTEST = True
except ImportError:
    HAVE_PYTEST = False

REDUCE_PY = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "reduce.py")
)


def get_file_hash(filepath):
    """Compute sha256 of file content."""
    hasher = hashlib.sha256()
    with open(filepath, "rb") as f:
        hasher.update(f.read())
    return hasher.hexdigest()


class ReducerCLITests(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_already_passing_input(self):
        """Reducer exits 1 if test file already passes on unmodified run."""
        test_file = os.path.join(self.temp_dir, "test_pass.py")
        content = (
            "import unittest\n\n"
            "class TestPassing(unittest.TestCase):\n"
            "    def test_ok(self):\n"
            "        self.assertTrue(True)\n"
        )
        with open(test_file, "w", encoding="utf-8") as f:
            f.write(content)

        initial_hash = get_file_hash(test_file)

        proc = subprocess.run(
            [sys.executable, REDUCE_PY, test_file],
            capture_output=True,
            text=True,
        )

        self.assertEqual(
            proc.returncode,
            1,
            f"Expected exit code 1 for passing test, got {proc.returncode}. Output:\n{proc.stdout}\n{proc.stderr}",
        )
        self.assertFalse(
            os.path.exists(f"{test_file}.min"),
            "Expected <path>.min not to be created when file already passes",
        )
        self.assertEqual(
            get_file_hash(test_file),
            initial_hash,
            "Input file was modified!",
        )

    def test_single_line_file(self):
        """Reducer handles a single-line failing file without crashing and exits 0."""
        test_file = os.path.join(self.temp_dir, "test_single.py")
        # 1-line failing test discoverable by unittest
        content = (
            'import unittest; T = type("T", (unittest.TestCase,), '
            '{"test_fail": lambda self: self.fail("expected")})\n'
        )
        with open(test_file, "w", encoding="utf-8") as f:
            f.write(content)

        initial_hash = get_file_hash(test_file)

        proc = subprocess.run(
            [sys.executable, REDUCE_PY, test_file],
            capture_output=True,
            text=True,
        )

        self.assertEqual(
            proc.returncode,
            0,
            f"Expected exit code 0 for single-line file, got {proc.returncode}. Output:\n{proc.stdout}\n{proc.stderr}",
        )
        self.assertTrue(os.path.exists(f"{test_file}.min"))

        with open(f"{test_file}.min", "r", encoding="utf-8") as f:
            min_lines = f.readlines()

        self.assertEqual(len(min_lines), 1)
        self.assertIn("Original line count: 1", proc.stdout)
        self.assertIn("Final line count: 1", proc.stdout)
        self.assertIn("Lines removed: 0", proc.stdout)
        self.assertEqual(get_file_hash(test_file), initial_hash)

    def test_reduces_to_near_nothing(self):
        """Reducer shrinks a 40+ line test file down to under 10 lines."""
        test_file = os.path.join(self.temp_dir, "test_large.py")
        lines = [
            "import unittest\n",
            "\n",
            "def helper_func():\n",
            "    return 100\n",
            "\n",
            "class BigTestSuite(unittest.TestCase):\n",
        ]
        for i in range(1, 16):
            lines.append(f"    def test_pass_{i}(self):\n")
            lines.append(f"        self.assertEqual({i}, {i})\n\n")

        lines.append("    def test_actual_failure(self):\n")
        lines.append("        self.assertEqual(1 + 1, 3)\n\n")

        for i in range(16, 25):
            lines.append(f"    def test_pass_{i}(self):\n")
            lines.append(f"        self.assertEqual({i}, {i})\n\n")

        self.assertGreater(len(lines), 40)
        with open(test_file, "w", encoding="utf-8") as f:
            f.writelines(lines)

        initial_hash = get_file_hash(test_file)

        proc = subprocess.run(
            [sys.executable, REDUCE_PY, test_file],
            capture_output=True,
            text=True,
        )

        self.assertEqual(
            proc.returncode,
            0,
            f"Expected exit code 0, got {proc.returncode}. Output:\n{proc.stdout}\n{proc.stderr}",
        )
        self.assertTrue(os.path.exists(f"{test_file}.min"))

        with open(f"{test_file}.min", "r", encoding="utf-8") as f:
            min_lines = f.readlines()

        self.assertLess(
            len(min_lines),
            10,
            f"Expected minimal reproducer to have under 10 lines, got {len(min_lines)} lines",
        )
        self.assertEqual(get_file_hash(test_file), initial_hash)

        # Verify minimal reproducer still fails when run
        runner_file = os.path.join(self.temp_dir, "test_check_min.py")
        shutil.copy(f"{test_file}.min", runner_file)
        check_proc = subprocess.run(
            [sys.executable, "-m", "unittest", "test_check_min.py"],
            cwd=self.temp_dir,
            capture_output=True,
            text=True,
        )
        self.assertEqual(check_proc.returncode, 1)
        self.assertIn("FAIL:", check_proc.stderr)

    def test_timeout_handling(self):
        """Reducer times out candidate runs at --timeout and treats timeouts as not-failing."""
        test_file = os.path.join(self.temp_dir, "test_hang.py")
        content = (
            "import unittest\n"
            "import time\n\n"
            "STOP = False\n\n"
            "def check_stop():\n"
            "    if not STOP:\n"
            "        time.sleep(2)\n\n"
            "class TestHang(unittest.TestCase):\n"
            "    def test_a_fast(self):\n"
            "        global STOP\n"
            "        STOP = True\n\n"
            "    def test_b_conditional_hang(self):\n"
            "        check_stop()\n\n"
            "    def test_c_fail(self):\n"
            "        self.assertEqual(1, 2)\n"
        )
        with open(test_file, "w", encoding="utf-8") as f:
            f.write(content)

        # Baseline: test_a_fast sets STOP=True, so check_stop() does not sleep, test_c_fail fails quickly.
        # When test_a_fast is removed during reduction, check_stop() sleeps for 2 seconds.
        # With --timeout 0.5, that candidate times out, which must count as not-failing and revert!
        proc = subprocess.run(
            [sys.executable, REDUCE_PY, test_file, "--timeout", "0.5"],
            capture_output=True,
            text=True,
        )

        self.assertEqual(proc.returncode, 0)
        self.assertTrue(os.path.exists(f"{test_file}.min"))

        # Confirm progress lines were logged and timeout didn't crash
        self.assertIn("Iteration", proc.stdout)
        self.assertIn("revert", proc.stdout)

    def test_iteration_cap_respected(self):
        """Reducer stops when --max-iterations is hit and reports accurate iterations used."""
        test_file = os.path.join(self.temp_dir, "test_cap.py")
        lines = [
            "import unittest\n",
            "class TestCap(unittest.TestCase):\n",
        ]
        for i in range(1, 25):
            lines.append(f"    def test_pass_{i}(self):\n")
            lines.append(f"        self.assertEqual({i}, {i})\n\n")

        lines.append("    def test_fail(self):\n")
        lines.append("        self.assertEqual(1, 2)\n")

        with open(test_file, "w", encoding="utf-8") as f:
            f.writelines(lines)

        max_iter = 3
        proc = subprocess.run(
            [sys.executable, REDUCE_PY, test_file, "--max-iterations", str(max_iter)],
            capture_output=True,
            text=True,
        )

        self.assertEqual(proc.returncode, 0)
        self.assertTrue(os.path.exists(f"{test_file}.min"))

        # Count iteration progress lines printed
        iter_lines = [l for l in proc.stdout.splitlines() if l.startswith("Iteration ")]
        self.assertLessEqual(len(iter_lines), max_iter)
        self.assertIn(f"Iterations used: {len(iter_lines)}", proc.stdout)

    def test_header_block_preserved(self):
        """Header block of imports is never deleted."""
        test_file = os.path.join(self.temp_dir, "test_imports.py")
        content = (
            "import unittest\n"
            "import sys\n"
            "from math import sqrt\n\n"
            "class TestImports(unittest.TestCase):\n"
            "    def test_fail(self):\n"
            "        self.assertEqual(sqrt(4), 3)\n"
        )
        with open(test_file, "w", encoding="utf-8") as f:
            f.write(content)

        proc = subprocess.run(
            [sys.executable, REDUCE_PY, test_file],
            capture_output=True,
            text=True,
        )

        self.assertEqual(proc.returncode, 0)
        with open(f"{test_file}.min", "r", encoding="utf-8") as f:
            min_content = f.read()

        self.assertIn("import unittest", min_content)
        self.assertIn("import sys", min_content)
        self.assertIn("from math import sqrt", min_content)


    def test_signature_preserved(self):
        """A candidate must reproduce the same exception message, not a different bug."""
        f = os.path.join(self.temp_dir, "test_sig.py")
        with open(f, "w", encoding="utf-8") as fh:
            fh.write(
                "import unittest\n\n"
                "def calc(x):\n"
                "    return 100 / x\n\n"
                "class T(unittest.TestCase):\n"
                "    def test_b(self):\n"
                "        self.assertEqual(calc(2), 5)\n\n"
                "    def test_c(self):\n"
                "        self.assertEqual(calc(4), 5)\n"
            )
        proc = subprocess.run([sys.executable, REDUCE_PY, f],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        with open(f + ".min", encoding="utf-8") as mf:
            out = mf.read()
        self.assertIn("calc(2)", out)
        self.assertNotIn("calc(4)", out)

    def test_localize_finds_culprit(self):
        """--localize reports the source line the repro drives."""
        f = os.path.join(os.path.dirname(REDUCE_PY), "fixtures", "test_calc.py")
        proc = subprocess.run([sys.executable, REDUCE_PY, f, "--localize"],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("calc.py", proc.stdout)
        self.assertIn("return (part / total) * 10", proc.stdout)

    @unittest.skipUnless(HAVE_PYTEST, "pytest not installed")
    def test_pytest_runner(self):
        """--runner pytest handles pytest-style files."""
        f = os.path.join(self.temp_dir, "test_pt.py")
        lines = ["def add(a, b):\n", "    return a + b\n\n\n"]
        for i in range(1, 8):
            lines += [f"def test_pass_{i}():\n", f"    assert add({i}, {i}) == {2*i}\n\n"]
        lines += ["def test_failing():\n", "    assert add(2, 2) == 5\n"]
        with open(f, "w", encoding="utf-8") as fh:
            fh.writelines(lines)
        proc = subprocess.run([sys.executable, REDUCE_PY, f, "--runner", "pytest"],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        with open(f + ".min", encoding="utf-8") as mf:
            out = mf.read()
        self.assertLess(len(out.splitlines()), 10)
        self.assertIn("add(2, 2)", out)

if __name__ == "__main__":
    unittest.main()
