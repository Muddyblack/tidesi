"""The session-cache key must describe the code, not the Nix build.

The packages substitute libsecret's absolute store path into
``providers/antigravity_remote.py`` and ``providers/zed.py``. A nixpkgs update
moves that path, which used to change the fingerprint of every provider module
and drop the whole session cache on a redeploy that changed no parser at all.
"""

import unittest

from _support import REPO  # noqa: F401
from tidesi import session_index_storage

_PINNED = b'SECRET_TOOL = "/nix/store/7km66b7savmk626jgwfaca35kck4jvwm-libsecret-0.21.7/bin/secret-tool"\n'
_PLAIN = b'SECRET_TOOL = "secret-tool"\n'


class CacheKeyStabilityTest(unittest.TestCase):
    def test_pinned_secret_tool_path_does_not_change_the_key(self):
        self.assertEqual(
            session_index_storage._normalized_source(_PLAIN),
            session_index_storage._normalized_source(_PINNED),
        )

    def test_a_different_store_tool_still_changes_the_key(self):
        other = b'SECRET_TOOL = "/nix/store/7km66b7savmk626jgwfaca35kck4jvwm-libsecret-0.21.7/bin/kwallet-query"\n'
        self.assertNotEqual(
            session_index_storage._normalized_source(_PINNED),
            session_index_storage._normalized_source(other),
        )

    def test_an_ordinary_parser_change_still_changes_the_key(self):
        self.assertNotEqual(
            session_index_storage._normalized_source(b"rows = parse(source)\n"),
            session_index_storage._normalized_source(b"rows = parse_all(source)\n"),
        )


if __name__ == "__main__":
    unittest.main()
