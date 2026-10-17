"""The Linux learning extension must not break imports on other platforms."""
from pathlib import Path
import subprocess
import sys
import unittest


class PlatformCompatibilityTests(unittest.TestCase):
    def test_missing_posix_directory_flags_preserves_api_import_and_blocks_cleanup(self):
        # Start a fresh interpreter so an already-imported learning module cannot
        # hide the import-time failure that the Windows legacy backend would see.
        script = """
import os
for name in ('O_DIRECTORY', 'O_NOFOLLOW', 'O_CLOEXEC'):
    if hasattr(os, name):
        delattr(os, name)
from app.learning_api import router, get_service
from app.learning_trash import FLAGS, TrashUnsafe, preview_trash
assert router.prefix == '/api/learning'
assert get_service.cache_info().currsize == 0
assert FLAGS is None
try:
    preview_trash('must-not-be-opened')
except TrashUnsafe:
    pass
else:
    raise AssertionError('unsupported platform attempted archive access')
"""
        result = subprocess.run([sys.executable, "-c", script],
                                cwd=Path(__file__).resolve().parent.parent,
                                text=True, capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
