"""Cocoa ownership errors must fail a subprocess, not kill the pytest process."""

import os
import subprocess
import sys

import pytest


@pytest.mark.skipif(sys.platform != 'darwin', reason='Cocoa accessibility is macOS-only')
def test_native_table_accessibility_survives_selection_and_model_resets():
    result = subprocess.run(
        [sys.executable, '-m', 'csv_workbench.main', '--accessibility-smoke-test'],
        env={**os.environ, 'QT_QPA_PLATFORM': 'cocoa'},
        capture_output=True, text=True, timeout=60, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
