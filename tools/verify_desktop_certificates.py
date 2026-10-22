"""Verify the certifi data shipped by desktop builds, without changing OS trust."""

from __future__ import annotations

import argparse
import hashlib
import re
import ssl
from importlib import metadata
from pathlib import Path


MIN_CERTIFI_VERSION = (2026, 10, 19)
# emSign ECC Root CA - G3, removed by upstream certifi PR #437.
DISTRUSTED_CA_SHA256 = frozenset({
    "86a1ecba089c4a8d3bbe2734c612ba341d813e043cf9e8a862cd5c57a36bbe6b",
})


def _require_current_version(version: str) -> None:
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(?:\.post\d+)?", version)
    if not match or tuple(map(int, match.groups())) < MIN_CERTIFI_VERSION:
        raise ValueError(f"certifi {version} is unsupported; install certifi>=2026.10.19")


def _check_roots(path: Path) -> int:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.load_verify_locations(cafile=str(path))
    roots = context.get_ca_certs(binary_form=True)
    if not roots:
        raise ValueError("CA bundle contains no CA certificates")
    if any(hashlib.sha256(root).hexdigest() in DISTRUSTED_CA_SHA256 for root in roots):
        raise ValueError("CA bundle contains a distrusted emSign G3 root")
    return len(roots)


def verify_certificates(bundle: Path | None = None) -> dict[str, str | int]:
    import certifi

    version = metadata.version("certifi")
    _require_current_version(version)
    source = Path(certifi.where())
    count = _check_roots(source)
    source_bytes = source.read_bytes()
    if bundle is not None:
        distributions = [
            dist for dist in metadata.distributions(path=[str(bundle)])
            if dist.metadata.get("Name", "").lower() == "certifi"
        ]
        if len(distributions) != 1:
            raise ValueError("Packaged certifi distribution metadata is missing or ambiguous")
        packaged_version = distributions[0].version
        _require_current_version(packaged_version)
        if packaged_version != version:
            raise ValueError("Packaged certifi version differs from the build environment")
        packaged = bundle / "certifi" / "cacert.pem"
        count = _check_roots(packaged)
        if packaged.read_bytes() != source_bytes:
            raise ValueError("Packaged CA data differs from the build environment")
    return {"version": version, "roots": count, "sha256": hashlib.sha256(source_bytes).hexdigest()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, help="Built desktop bundle directory")
    args = parser.parse_args()
    try:
        result = verify_certificates(args.bundle)
    except (OSError, ValueError, ImportError) as exc:
        parser.exit(1, f"Certificate verification failed: {exc}\n")
    print(f"certifi {result['version']}: {result['roots']} CA roots; SHA-256 {result['sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
