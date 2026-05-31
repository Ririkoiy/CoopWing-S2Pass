import unittest
from backend.api_models import make_success_response, make_error_response, BackendError

class TestBackendApiModels(unittest.TestCase):
    def test_success_envelope(self):
        data = {"hello": "world"}
        resp = make_success_response(data)
        self.assertTrue(resp["ok"])
        self.assertEqual(resp["data"], data)

    def test_error_envelope(self):
        resp = make_error_response(
            code="SOME_ERROR",
            message="User-friendly description",
            details={"key": "val"}
        )
        self.assertFalse(resp["ok"])
        self.assertIn("error", resp)
        self.assertEqual(resp["error"]["code"], "SOME_ERROR")
        self.assertEqual(resp["error"]["message"], "User-friendly description")
        self.assertEqual(resp["error"]["details"], {"key": "val"})

    def test_error_without_details(self):
        resp = make_error_response(code="TEST_ERROR", message="msg")
        self.assertFalse(resp["ok"])
        self.assertEqual(resp["error"]["details"], {})

    def test_backend_error_class(self):
        err = BackendError(code="INVALID_INPUT", message="Friendly message", details={"field": "test"})
        self.assertEqual(err.code, "INVALID_INPUT")
        self.assertEqual(err.message, "Friendly message")
        self.assertEqual(err.details, {"field": "test"})
        self.assertNotIn("traceback", err.message)

if __name__ == "__main__":
    unittest.main()
