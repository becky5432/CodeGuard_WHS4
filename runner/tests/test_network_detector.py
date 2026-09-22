import unittest
from unittest.mock import MagicMock, patch

from runner.metrics.network_detector import detect_network_block


class NetworkDetectorTests(unittest.TestCase):
    @patch("runner.metrics.network_detector.subprocess.run")
    def test_returns_true_when_matching_block_log_exists(
        self,
        run_mock,
    ) -> None:
        run_mock.return_value = MagicMock(
            stdout=(
                "CG_NETBLOCK "
                "SRC=172.30.0.2 "
                "DST=169.254.169.254 "
                "PROTO=TCP DPT=80\n"
            )
        )

        result = detect_network_block(
            "172.30.0.2",
            1000.0,
        )

        self.assertTrue(result)

    @patch("runner.metrics.network_detector.subprocess.run")
    def test_returns_false_when_src_does_not_match(
        self,
        run_mock,
    ) -> None:
        run_mock.return_value = MagicMock(
            stdout=(
                "CG_NETBLOCK "
                "SRC=172.30.0.3 "
                "DST=169.254.169.254 "
                "PROTO=TCP DPT=80\n"
            )
        )

        result = detect_network_block(
            "172.30.0.2",
            1000.0,
        )

        self.assertFalse(result)

    @patch("runner.metrics.network_detector.subprocess.run")
    def test_returns_false_when_no_block_log_exists(
        self,
        run_mock,
    ) -> None:
        run_mock.return_value = MagicMock(
            stdout="normal kernel message\n"
        )

        result = detect_network_block(
            "172.30.0.2",
            1000.0,
        )

        self.assertFalse(result)


if __name__ == "__main__":
    unittest.main()