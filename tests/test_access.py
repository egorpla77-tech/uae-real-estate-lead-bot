import tempfile
import unittest
from pathlib import Path

from storage import AccessStore


class AccessStoreTests(unittest.TestCase):
    def test_only_first_two_distinct_accounts_are_admitted(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "access.json"
            access = AccessStore(path, limit=2)

            self.assertEqual(("claimed", 1), access.claim(101, "101", "first"))
            self.assertEqual(("claimed", 2), access.claim(202, "202", "second"))
            self.assertEqual(("full", 0), access.claim(303, "303", "third"))
            self.assertTrue(access.is_authorized(101, "101"))
            self.assertTrue(access.is_authorized(202, "202"))
            self.assertFalse(access.is_authorized(303, "303"))
            self.assertEqual(1, access.slot_for(101, "101"))
            self.assertEqual(2, access.slot_for(202, "202"))
            self.assertEqual(["101", "202"], access.recipients())

            reloaded = AccessStore(path, limit=2)
            self.assertEqual(("existing", 1), reloaded.claim(101, "101", "first"))
            self.assertEqual(2, reloaded.count())


if __name__ == "__main__":
    unittest.main()
