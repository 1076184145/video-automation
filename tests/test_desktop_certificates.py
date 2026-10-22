from __future__ import annotations

import importlib.util
import ssl
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import certifi


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "verify_desktop_certificates", ROOT / "tools" / "verify_desktop_certificates.py"
)
assert SPEC is not None and SPEC.loader is not None
verifier = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verifier)

# Public CA certificate removed by certifi commit 616951fa3b70e9304c9b742d744effd0abae3c75.
# No labels/comments: detection must inspect DER bytes, not display names.
DISTRUSTED_G3_PEM = """-----BEGIN CERTIFICATE-----
MIICTjCCAdOgAwIBAgIKPPYHqWhwDtqLhDAKBggqhkjOPQQDAzBrMQswCQYDVQQG
EwJJTjETMBEGA1UECxMKZW1TaWduIFBLSTElMCMGA1UEChMcZU11ZGhyYSBUZWNo
bm9sb2dpZXMgTGltaXRlZDEgMB4GA1UEAxMXZW1TaWduIEVDQyBSb290IENBIC0g
RzMwHhcNMTgwMjE4MTgzMDAwWhcNNDMwMjE4MTgzMDAwWjBrMQswCQYDVQQGEwJJ
TjETMBEGA1UECxMKZW1TaWduIFBLSTElMCMGA1UEChMcZU11ZGhyYSBUZWNobm9s
b2dpZXMgTGltaXRlZDEgMB4GA1UEAxMXZW1TaWduIEVDQyBSb290IENBIC0gRzMw
djAQBgcqhkjOPQIBBgUrgQQAIgNiAAQjpQy4LRL1KPOxst3iAhKAnjlfSU2fySU0
WXTsuwYc58Byr+iuL+FBVIcUqEqy6HyC5ltqtdyzdc6LBtCGI79G1Y4PPwT01xyS
fvalY8L1X44uT6EYGQIrMgqCZH0Wk9GjQjBAMB0GA1UdDgQWBBR8XQKEE9TMipuB
zhccLikenEhjQjAOBgNVHQ8BAf8EBAMCAQYwDwYDVR0TAQH/BAUwAwEB/zAKBggq
hkjOPQQDAwNpADBmAjEAvvNhzwIQHWSVB7gYboiFBS+DCBeQyh+KTOgNG3qxrdWB
CUfvO6wIBHxcmbHtRwfSAjEAnbpV/KlK6O3t5nYBQnvI+GDZjVGLVTv7jHvrZQnD
+JbNR6iC8hZVdyR+EhCVBCyj
-----END CERTIFICATE-----
"""


class DesktopCertificateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.bundle = Path(self.temporary.name)
        self.pem = self.bundle / "certifi" / "cacert.pem"
        self.pem.parent.mkdir()
        self.pem.write_bytes(Path(certifi.where()).read_bytes())
        self.dist_info = self.bundle / "certifi-2026.10.19.dist-info"
        self.dist_info.mkdir()
        self.set_packaged_version("2026.10.19")
        version_patch = patch.object(verifier.metadata, "version", return_value="2026.10.19")
        version_patch.start()
        self.addCleanup(version_patch.stop)

    def set_packaged_version(self, version: str) -> None:
        (self.dist_info / "METADATA").write_text(
            f"Metadata-Version: 2.1\nName: certifi\nVersion: {version}\n", encoding="utf-8"
        )

    def test_current_bundle_matches_source(self) -> None:
        result = verifier.verify_certificates(self.bundle)
        self.assertEqual(result["version"], "2026.10.19")
        self.assertGreater(result["roots"], 0)

    def test_outdated_build_dependency_is_rejected(self) -> None:
        with patch.object(verifier.metadata, "version", return_value="2026.10.18"):
            with self.assertRaisesRegex(ValueError, "unsupported"):
                verifier.verify_certificates()

    def test_outdated_packaged_metadata_is_rejected(self) -> None:
        self.set_packaged_version("2026.10.18")
        with self.assertRaisesRegex(ValueError, "unsupported"):
            verifier.verify_certificates(self.bundle)

    def test_missing_packaged_metadata_is_rejected(self) -> None:
        (self.dist_info / "METADATA").unlink()
        with self.assertRaisesRegex(ValueError, "metadata is missing"):
            verifier.verify_certificates(self.bundle)

    def test_missing_packaged_pem_is_rejected(self) -> None:
        self.pem.unlink()
        with self.assertRaises(OSError):
            verifier.verify_certificates(self.bundle)

    def test_valid_but_different_packaged_data_is_rejected(self) -> None:
        self.pem.write_bytes(self.pem.read_bytes() + b"\n")
        with self.assertRaisesRegex(ValueError, "CA data differs"):
            verifier.verify_certificates(self.bundle)

    def test_malformed_packaged_pem_is_rejected(self) -> None:
        self.pem.write_text("invalid CA data", encoding="ascii")
        with self.assertRaises(ssl.SSLError):
            verifier.verify_certificates(self.bundle)

    def test_distrusted_root_is_rejected_without_labels(self) -> None:
        self.pem.write_text(DISTRUSTED_G3_PEM, encoding="ascii")
        with self.assertRaisesRegex(ValueError, "distrusted emSign G3"):
            verifier.verify_certificates(self.bundle)


if __name__ == "__main__":
    unittest.main()
