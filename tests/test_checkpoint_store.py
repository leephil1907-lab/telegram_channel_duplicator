import os
import tempfile
import unittest

from telegram_channel_duplicator.checkpoint_store import CheckpointStore


class CheckpointStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.temp_dir.name, "state.sqlite3")
        self.store = CheckpointStore(self.path)

    def tearDown(self):
        self.store.close()
        self.temp_dir.cleanup()

    def test_missing_checkpoint_is_distinct_from_zero(self):
        self.assertFalse(self.store.has_checkpoint(-100123))
        self.assertEqual(self.store.get(-100123), 0)

    def test_checkpoint_survives_reopen_and_never_moves_backwards(self):
        self.store.set(-100123, 80)
        self.store.set(-100123, 72)
        self.assertEqual(self.store.get(-100123), 80)
        self.store.close()

        self.store = CheckpointStore(self.path)
        self.assertTrue(self.store.has_checkpoint(-100123))
        self.assertEqual(self.store.get(-100123), 80)

    def test_delivery_is_scoped_to_source_message_and_destination(self):
        self.assertFalse(self.store.was_delivered(-1001, 10, -2001))
        self.store.mark_delivered(-1001, 10, -2001, 501)
        self.assertTrue(self.store.was_delivered(-1001, 10, -2001))
        self.assertFalse(self.store.was_delivered(-1001, 10, -2002))
        self.assertFalse(self.store.was_delivered(-1001, 11, -2001))

    def test_negative_checkpoint_is_rejected(self):
        with self.assertRaises(ValueError):
            self.store.set(-100123, -1)


if __name__ == "__main__":
    unittest.main()
