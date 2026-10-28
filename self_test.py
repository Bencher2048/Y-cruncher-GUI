"""The same unittest suite in source mode and inside the single-file EXE."""
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
import sys
import traceback
import unittest

from utils import get_app_root


def run_tests():
    resources = (Path(sys._MEIPASS) if getattr(sys, 'frozen', False)
                 else Path(__file__).resolve().parents[1])
    tests = resources / 'tests'
    log = get_app_root() / 'self_test.log'
    original_path = sys.path[:]
    try:
        with log.open('w', encoding='utf-8') as stream:
            with redirect_stdout(stream), redirect_stderr(stream):
                try:
                    sys.path.insert(0, str(tests))
                    suite = unittest.defaultTestLoader.discover(str(tests), pattern='test_*.py')
                    if not suite.countTestCases():
                        raise RuntimeError('No embedded tests were found')
                    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
                    return 0 if result.wasSuccessful() else 1
                except Exception:
                    traceback.print_exc(file=stream)
                    return 1
    finally:
        sys.path[:] = original_path


if __name__ == '__main__':
    raise SystemExit(run_tests())
