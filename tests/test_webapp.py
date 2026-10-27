import base64
import os
import unittest
from unittest.mock import patch

import webapp


class DashboardAuthTests(unittest.TestCase):
    def test_password_required_for_auth(self):
        with patch.object(webapp, "PASSWORD", "secret"):
            token = base64.b64encode(b"user:secret").decode()
            self.assertTrue(webapp.authorized("Basic " + token))
            bad = base64.b64encode(b"user:wrong").decode()
            self.assertFalse(webapp.authorized("Basic " + bad))

    def test_rejects_missing_or_wrong_scheme(self):
        with patch.object(webapp, "PASSWORD", "secret"):
            self.assertFalse(webapp.authorized(""))
            self.assertFalse(webapp.authorized("Bearer token"))

    def test_worker_state_shape(self):
        with patch.object(webapp, "worker", None):
            self.assertEqual(webapp.worker_state(), {"running": False, "pid": None})


if __name__ == "__main__":
    unittest.main()
