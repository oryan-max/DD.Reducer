#!/usr/bin/env python3
"""
Minimal-reproducer reducer CLI in Python for unittest.
Given a test file with a known failing test, produces the smallest version
of that file that still fails using Delta Debugging (ddmin).
"""

import argparse
import glob
import os
import re
import subprocess
import shutil
import sys
import tempfile


TEST_DEF_RE = re.compile(r"^(\s*)def test_")


SIG_RE = re.compile(r"^([A-Za-z_][\w.]*(?:Error|Exception))(?::\s*(.*))?$", re.M)


def capture_signature(text):
    m = SIG_RE.search(text)
    return None if not m else (m.group(1), (m.group(2) or "").strip())


def matches_signature(text, sig):
    if sig is None:
        return True
    m = SIG_RE.search(text)
    return bool(m) and m.group(1) == sig[0] and (m.group(2) or "").strip() == sig[1]


def _ident(text):
    return text


def _strip_pytest_prefix(text):
    return re.sub(r"^E\s+", "", text, flags=re.M)


RUNNERS = {
    "unittest": {
        "cmd": [sys.executable, "-m", "unittest", "{file}"],
        "driver": "unittest",
        "fail_code": 1,
        "fail_marker": "FAIL:",
        "error_marker": "ERROR:",
        "no_tests": ("Ran 0 tests", "NO TESTS RAN", "Failed to import test module",
                     "unittest.loader._FailedTest", "module = __import__(module_name)"),
        "normalize": _ident,
    },
    "pytest": {
        "cmd": [sys.executable, "-m", "pytest", "-q", "--no-header",
                "-p", "no:cacheprovider", "{file}"],
        "driver": "pytest",
        "fail_code": 1,
        "fail_marker": None,
        "error_marker": None,
        "no_tests": ("no tests ran",),
        "normalize": _strip_pytest_prefix,
    },
}


def find_protected_header_indices(lines):
    """
    Find line indices corresponding to the first contiguous run of import/include lines.
    Per correctness requirements:
    'Keep the import/include header block intact - never delete from the first
    contiguous run of import lines.'
    """
    start = None
    end = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        is_import = (
            stripped.startswith("import ")
            or stripped.startswith("import\t")
            or stripped.startswith("from ")
            or stripped.startswith("from\t")
            or stripped.startswith("#include")
        )
        if is_import:
            if start is None:
                start = i
            end = i + 1
        elif start is not None:
            break
    if start is None:
        return set()
    return set(range(start, end))


def split_chunks(items, n):
    """Split items into n approximately equal contiguous chunks."""
    if n <= 0 or not items:
        return []
    k, m = divmod(len(items), n)
    chunks = []
    start = 0
    for i in range(n):
        end = start + k + (1 if i < m else 0)
        chunks.append(items[start:end])
        start = end
    return chunks


def build_units(items):
    """
    Group lines into atomic units. A `def test_*` header plus its indented body
    (and blank lines inside it) is one unit, so headers never detach from bodies.
    Everything else is a single-line unit.
    """
    units = []
    i = 0
    while i < len(items):
        line = items[i][1]
        m = TEST_DEF_RE.match(line)
        if m:
            base = len(m.group(1))
            unit = [items[i]]
            j = i + 1
            while j < len(items):
                l2 = items[j][1]
                if l2.strip() == "":
                    unit.append(items[j])
                    j += 1
                    continue
                if len(l2) - len(l2.lstrip()) > base:
                    unit.append(items[j])
                    j += 1
                    continue
                break
            units.append(tuple(unit))
            i = j
        else:
            units.append((items[i],))
            i += 1
    return units


def is_genuine_test_failure(proc, content, test_file_name, expect_fail=False,
                            expect_error=False, sig=None, spec=None):
    """
    A candidate counts only if it fails the same way the baseline did: same
    exit code, still compiles, ran tests, same failure mode, same exception
    signature. Timeouts and syntax errors are not failures.
    """
    spec = spec or RUNNERS["unittest"]
    if proc.returncode != spec["fail_code"]:
        return False
    try:
        compile(content, test_file_name, "exec")
    except SyntaxError:
        return False

    combined = spec["normalize"](proc.stdout + "\n" + proc.stderr)

    if "SyntaxError:" in combined:
        return False
    for marker in spec["no_tests"]:
        if marker in combined:
            return False
    if spec["fail_marker"] and expect_fail and spec["fail_marker"] not in combined:
        return False
    if spec["error_marker"] and expect_error and spec["error_marker"] not in combined:
        return False
    if not matches_signature(combined, sig):
        return False

    return True


def run_test_candidate(content, test_file_name, work_dir, timeout, env,
                       expect_fail=False, expect_error=False, runner_cmd=None,
                       sig=None, spec=None):
    """
    Write candidate content to file and execute the runner.
    Kills the run at timeout seconds.
    Returns (True, proc) only if the candidate still exhibits a genuine failure.
    """
    spec = spec or RUNNERS["unittest"]
    test_path = os.path.join(work_dir, test_file_name)
    with open(test_path, "w", encoding="utf-8") as f:
        f.write(content)

    template = runner_cmd or spec["cmd"]
    cmd = [part.format(file=test_file_name) for part in template]

    try:
        proc = subprocess.run(
            cmd,
            cwd=work_dir,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return False, None
    except Exception:
        return False, None

    success = is_genuine_test_failure(
        proc,
        content,
        test_file_name,
        expect_fail=expect_fail,
        expect_error=expect_error,
        sig=sig,
        spec=spec,
    )
    return success, proc


def reduce_file(file_path, max_iterations=300, timeout=5.0, runner_cmd=None,
                localize_min=False, runner="unittest"):
    """
    Reduces the target test file to the minimal reproducer.
    Exits 0 on success, 1 if file already passes.
    Never modifies input file. Writes minimal version to <path>.min.
    """
    if not os.path.isfile(file_path):
        sys.stderr.write(f"Error: file not found: {file_path}\n")
        return 1

    with open(file_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    original_line_count = len(lines)
    test_file_name = os.path.basename(file_path)
    spec = RUNNERS[runner]
    orig_dir = os.path.abspath(os.path.dirname(file_path))

    # Environment setup to ensure local imports from orig_dir work
    env = os.environ.copy()
    env["PYTHONPATH"] = orig_dir + os.pathsep + env.get("PYTHONPATH", "")

    # Identify protected header indices (first contiguous run of import lines)
    protected_indices = find_protected_header_indices(lines)
    items = [(i, line, i in protected_indices) for i, line in enumerate(lines)]

    with tempfile.TemporaryDirectory() as work_dir:
        # 1. Copy/write unmodified copy and test once
        unmodified_content = "".join(lines)
        baseline_fails, baseline_proc = run_test_candidate(
            unmodified_content, test_file_name, work_dir, timeout, env,
            runner_cmd=runner_cmd, spec=spec,
        )

        # 2. If it passes or does not produce genuine failure, exit 1
        if not baseline_fails or baseline_proc is None:
            sys.stderr.write("Test file already passes (or did not fail with genuine test failure).\n")
            return 1

        combined_baseline = spec["normalize"](baseline_proc.stdout + "\n" + baseline_proc.stderr)
        expect_fail = bool(spec["fail_marker"]) and spec["fail_marker"] in combined_baseline
        expect_error = (bool(spec["error_marker"])
                        and spec["error_marker"] in combined_baseline and not expect_fail)
        sig = capture_signature(combined_baseline)

        # 3. Loop: split candidate into N equal chunks, starting at N=2
        candidate = build_units(items)
        N = 2
        iterations_used = 0

        while iterations_used < max_iterations:
            reducible = [u for u in candidate if not any(it[2] for it in u)]
            if len(reducible) == 0 or N > len(reducible):
                break

            chunks = split_chunks(reducible, N)
            deleted_in_sweep = False
            i = 0

            while i < len(chunks) and iterations_used < max_iterations:
                iterations_used += 1
                to_remove = set(chunks[i])
                test_candidate = [u for u in candidate if u not in to_remove]
                test_content = "".join(it[1] for u in test_candidate for it in u)

                still_fails, _ = run_test_candidate(
                    test_content,
                    test_file_name,
                    work_dir,
                    timeout,
                    env,
                    expect_fail=expect_fail,
                    expect_error=expect_error,
                    runner_cmd=runner_cmd,
                    sig=sig,
                    spec=spec,
                )

                if still_fails:
                    candidate = test_candidate
                    chunks.pop(i)
                    deleted_in_sweep = True
                    print(f"Iteration {iterations_used}: {sum(len(u) for u in candidate)} lines left, keep")
                else:
                    print(f"Iteration {iterations_used}: {sum(len(u) for u in candidate)} lines left, revert")
                    i += 1

            if deleted_in_sweep:
                N = max(N - 1, 2)
            else:
                N = N * 2

    # 6. Write the result to <path>.min
    min_file_path = f"{file_path}.min"
    minimal_content = "".join(it[1] for u in candidate for it in u)
    with open(min_file_path, "w", encoding="utf-8") as f:
        f.write(minimal_content)

    final_line_count = sum(len(u) for u in candidate)
    lines_removed = original_line_count - final_line_count

    print(f"Original line count: {original_line_count}")
    print(f"Final line count: {final_line_count}")
    print(f"Lines removed: {lines_removed}")
    print(f"Iterations used: {iterations_used}")

    if localize_min:
        localize(min_file_path, timeout, spec)

    return 0


def localize(min_path, timeout, spec=None):
    """
    Execute the minimal reproducer under the stdlib tracer and report the
    project source lines it drives, in source order.
    """
    spec = spec or RUNNERS["unittest"]
    min_path = os.path.abspath(min_path)
    workdir = os.path.dirname(min_path)
    covdir = tempfile.mkdtemp()
    try:
        project = {os.path.splitext(os.path.basename(f))[0]
                   for f in glob.glob(os.path.join(workdir, "*.py"))}
        project = {n for n in project if not n.startswith("test_")}
        repro = os.path.join(covdir, "_repro.py")
        shutil.copy(min_path, repro)

        if spec["driver"] == "pytest":
            body = (
                "import pytest, sys\n"
                f"sys.path.insert(0, {covdir!r})\n"
                f"sys.path.insert(0, {workdir!r})\n"
                f"pytest.main(['-q', '--no-header', '-p', 'no:cacheprovider', {repro!r}])\n"
            )
        else:
            body = (
                "import unittest, sys\n"
                f"sys.path.insert(0, {covdir!r})\n"
                f"sys.path.insert(0, {workdir!r})\n"
                "unittest.main(module='_repro', argv=['driver'], exit=False)\n"
            )
        driver = os.path.join(covdir, "driver.py")
        with open(driver, "w", encoding="utf-8") as f:
            f.write(body)
        subprocess.run(
            [sys.executable, "-m", "trace", "--count", "--coverdir", covdir, driver],
            cwd=workdir, capture_output=True, text=True,
            timeout=max(timeout * 10, 30),
        )
        hits = {}
        for cf in glob.glob(os.path.join(covdir, "*.cover")):
            fname = os.path.basename(cf)[:-len(".cover")].split(".")[-1]
            if fname not in project and fname != "_repro":
                continue
            for lineno, line in enumerate(open(cf, encoding="utf-8", errors="replace"), 1):
                m = re.match(r"^\s*(\d+): (.*)$", line)
                if not m:
                    continue
                count = int(m.group(1))
                if count:
                    hits.setdefault(fname, []).append((lineno, count, m.group(2).rstrip()))
    finally:
        shutil.rmtree(covdir, ignore_errors=True)

    if not hits:
        print("\nLocalisation: no project coverage captured.")
        return

    order = sorted(project) if project else ["_repro"]
    print("\nSuspects (project code only, in source order):")
    for fname in order:
        if fname not in hits:
            continue
        label = "minimal reproducer" if fname == "_repro" else f"{fname}.py"
        print(f"\n-- {label} --")
        for lineno, count, text in sorted(hits[fname])[:40]:
            print(f"{lineno:>4}  {count:>3}x  {text}")


def main():
    parser = argparse.ArgumentParser(
        description="Minimal-reproducer reducer CLI in Python for unittest"
    )
    parser.add_argument("path", metavar="<path-to-test-file>", help="Path to test file")
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=300,
        help="Maximum iterations cap (default: 300)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=5.0,
        help="Timeout in seconds per test run (default: 5)",
    )
    parser.add_argument(
        "--localize",
        action="store_true",
        help="After reduction, report the source lines the repro exercises",
    )
    parser.add_argument(
        "--runner",
        choices=sorted(RUNNERS),
        default="unittest",
        help="Test runner to drive (default: unittest)",
    )
    args = parser.parse_args()

    exit_code = reduce_file(
        args.path,
        max_iterations=args.max_iterations,
        timeout=args.timeout,
        localize_min=args.localize,
        runner=args.runner,
    )
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
