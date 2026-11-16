# minimal-reproducer reducer CLI

A minimal-reproducer reducer CLI written in Python for Python test files, driving `unittest` or `pytest`.
Given a test file with a known failing test, it produces the smallest version of that file that still fails using the Delta Debugging algorithm (`ddmin`).

---

## Usage

```bash
./reduce.py <path-to-test-file> [--max-iterations 300] [--timeout 5] [--localize] [--runner unittest|pytest]
```

Or invoke directly with `python3`:

```bash
python3 reduce.py <path-to-test-file> [--max-iterations 300] [--timeout 5] [--localize] [--runner unittest|pytest]
```

### CLI Contract
- **Exit code `0`**: Reduction succeeded; writes the minimal reproducer to `<path>.min`.
- **Exit code `1`**: If the test file already passes on the unmodified copy (or does not exhibit a genuine test failure).
- **Never modifies the input file**: The source file is strictly read-only.
- **Output statistics**: Prints original line count, final line count, lines removed, and iterations used.
- **Progress reporting**: Prints one line per iteration with iteration number, lines left, and `keep` or `revert`.

---

## Algorithm

1. **Copy input**: Copy the target test file to an isolated temporary directory.
2. **Baseline check**: Run the test once on the unmodified copy. If it passes, exit 1.
3. **Partition & Sweep**: Split the current reducible candidate lines into $N$ equal chunks (starting at $N = 2$). For each chunk:
   - Delete the chunk.
   - Re-run the test runner inside the temporary environment.
   - If it still fails with a genuine test failure exit code, keep the deletion.
   - If it passes, times out, or triggers syntax errors, revert the chunk.
4. **Adaptive Granularity**:
   - If a sweep had no successful deletions, double $N$ ($N \leftarrow 2N$) and sweep again with finer granularity.
   - If a sweep had successful deletions, adjust $N \leftarrow \max(N - 1, 2)$ to continue isolating minimal chunks.
5. **Termination**: Stop when $N$ exceeds the candidate line count, or `--max-iterations` is hit.
6. **Output**: Write the minimized candidate to `<path>.min`.

---

## Web Studio UI

A beautiful, interactive web interface is included in `web/`:

```bash
# Start the local server:
cd web
python3 server.py
# Open http://127.0.0.1:8765 in your browser
```

Alternatively, open `web/index.html` directly in any browser — it includes an in-browser Delta Debugging simulation engine with live visualization, presets, line metrics, and diff viewer.

---

## Correctness & Safety

- **Genuine Failure Exit Code**: A test run only counts as "still failing" if the runner exits with code 1 and produces an authentic test failure. Timeouts and syntax errors count as not-failing and are immediately reverted.
- **Timeout Protection**: Each run is killed at `--timeout` seconds (default 5s) via process termination.
- **Import Header Preservation**: The first contiguous run of `import` / `from` / `#include` lines is protected and never deleted, guaranteeing valid dependencies.
- **Iteration Cap**: Every test run (whether keep or revert) counts against `--max-iterations`.
- **Zero External Dependencies**: Standard library only (`argparse`, `subprocess`, `tempfile`, `sys`, `os`, `shutil`). No configuration files or color codes.

---

## Before & After Example

The repository includes a sample fixture with 50 lines containing helper functions, setup, and multiple passing tests with one failing test (`fixtures/sample_test.py`).

### Command
```bash
./reduce.py fixtures/sample_test.py
```

### Output Progress & Statistics
```text
Iteration 1: 50 lines left, revert
Iteration 2: 50 lines left, revert
Iteration 3: 37 lines left, keep
Iteration 4: 37 lines left, revert
Iteration 5: 25 lines left, keep
...
Iteration 35: 6 lines left, keep
Iteration 36: 6 lines left, revert
Iteration 37: 6 lines left, revert
Iteration 38: 6 lines left, revert
Iteration 39: 6 lines left, revert
Original line count: 50
Final line count: 6
Lines removed: 44
Iterations used: 39
```

### Before: `fixtures/sample_test.py` (50 lines)
```python
import unittest


def add(a, b):
    return a + b


def subtract(a, b):
    return a - b


def multiply(a, b):
    return a * b


def buggy_percentage(part, total):
    # Bug: returns wrong calculation
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
```

### After: `fixtures/sample_test.py.min` (6 lines)
```python
import unittest
def buggy_percentage(part, total):
    return (part / total) * 10
class TestMathModule(unittest.TestCase):
    def test_add_zero(self):
        self.assertEqual(buggy_percentage(50, 100), 50.0)
```

Notice that `reduce` stripped out all 7 unrelated passing tests, unused helper functions, comments, and boilerplate, preserving only the necessary import, function under test, and failing assertion.

---

## Running Tests

Run the test suite using Python's standard library `unittest`:

```bash
python3 -m unittest test_reducer.py -v
```

Tests cover:
- Already-passing input (returns exit code 1)
- Single-line file (handles edge case without crash)
- Reduction to near-nothing (40+ lines reduced to < 10 lines)
- Timeout handling (aborts slow runs and reverts)
- Iteration cap enforcement (`--max-iterations` respected)
- Import header block preservation

---

## Limitations

- Timeouts kill the direct child process only; tests that spawn subprocesses
  can leave stragglers.
- The import-header guard protects any contiguous top-level import run,
  including imports that appear mid-file.
- Test method headers are grouped with their bodies so names stay attached,
  but other statements reduce at line granularity and the result can still be
  syntactically valid while semantically odd.

---

## Failure-signature preservation

Reduction that only checks for a nonzero exit will happily drift onto a
different bug. After the baseline run, the exception type and message are
captured, and every candidate must reproduce both exactly. Deleting a test
that fails with `AssertionError: 50 != 5` cannot be replaced by one failing
with `AssertionError: 25 != 5`.

## Localisation (`--localize`)

After minimisation the reproducer is executed under the standard library
tracer and the project source lines it drives are reported in source order.
This converts the output from "here is a small thing that fails" into "here
is the code the failure runs through". Only non-test project modules are
shown; if there are none, the reproducer itself is reported.

Counts are execution frequency, not blame — a line run once can be the
culprit while a line run eight times is innocent.

### Limitations of localisation

- The tracer is per-process. Anything spawned as a subprocess is not covered.
- Only lines actually executed appear. A defect on a branch the repro never
  takes will not show up.
- The driver imports the reproducer as a module, so a reproducer with
  side effects at import time will run them.

---

## Runners (`--runner`)

`unittest` (default) and `pytest`. The runner controls the invocation, the
exit code treated as failure, the "no tests ran" markers, and the output
normalisation applied before signature matching. pytest output is stripped
of its `E   ` prefixes so the same signature logic applies to both.

pytest is optional — `unittest` needs nothing beyond the standard library.
With pytest, run the tool from the environment where pytest is installed,
since it is invoked via `sys.executable`.

The localiser's pytest driver runs the reproducer from a temporary
directory, so a `conftest.py` in the original directory is not applied.

---

## Web UI

Run `python3 web/server.py` and open http://127.0.0.1:8765. Upload a test
file and the module it imports, then press Reduce. The original shows on the
left with executed lines highlighted; the minimal reproducer replaces the
progress log on the right. The target is picked by `test_` prefix, so the
test file must be named that way.

The server is a stdlib http.server bound to loopback only. It is a local
tool, not a deployment.
