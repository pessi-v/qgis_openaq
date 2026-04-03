import threading
import time
import unittest

from ..core.rate_limiter import RateLimiter


class TestRateLimiter(unittest.TestCase):
    def test_initially_allows_request(self):
        rl = RateLimiter()
        self.assertTrue(rl.can_request())

    def test_counts_start_at_zero(self):
        rl = RateLimiter()
        self.assertEqual(rl.count_last_minute(), 0)
        self.assertEqual(rl.count_last_hour(), 0)

    def test_record_increments_counts(self):
        rl = RateLimiter()
        rl.record_request()
        rl.record_request()
        self.assertEqual(rl.count_last_minute(), 2)
        self.assertEqual(rl.count_last_hour(), 2)

    def test_blocks_at_minute_limit(self):
        rl = RateLimiter()
        for _ in range(RateLimiter.MINUTE_LIMIT):
            rl.record_request()
        self.assertFalse(rl.can_request())

    def test_blocks_at_hour_limit(self):
        rl = RateLimiter()
        # Inject timestamps directly to avoid slow loops.
        from datetime import datetime, timezone
        ts = datetime.now(tz=timezone.utc)
        for _ in range(RateLimiter.HOUR_LIMIT):
            rl._timestamps.append(ts)
        self.assertFalse(rl.can_request())

    def test_thread_safety(self):
        rl = RateLimiter()
        errors = []

        def worker():
            try:
                for _ in range(10):
                    rl.record_request()
                    rl.count_last_minute()
                    rl.can_request()
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])
        self.assertEqual(rl.count_last_minute(), 80)


if __name__ == "__main__":
    unittest.main()
