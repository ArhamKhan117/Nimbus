"""Bake the licence service's **public** key into a release build.

    python -m tools.set_licence_key --public-key <base64>       write it
    python -m tools.set_licence_key --service-url https://...   and point at a deployment
    python -m tools.set_licence_key --check                     what will this build ship with?
    python -m tools.set_licence_key --clear                     back to an unkeyed dev build

Writes ``licence_key.py`` at the repo root, which is git-ignored and read by ``licensing._baked``.
Generate the pair with ``node scripts/keygen.mjs`` inside ``web/`` -- the private half belongs in the
backend's environment and nowhere else, least of all here.

## Why a generated module rather than an environment variable at build time

PyInstaller freezes code, not the shell that invoked it. An environment variable set during the build
is gone by the time a tester double-clicks the EXE, so ``NIMBUS_LICENCE_PUBLIC_KEY`` alone produces
a bundle where every activation fails with "this build has no licence key configured". A module gets
compiled into the archive, which is the only place a shipped constant can live.

## Why this validates instead of just writing the string

A truncated paste is the failure this prevents. Base64 that decodes to 31 bytes is accepted by
nothing downstream, but without a check here the first sign of it is a tester whose valid licence
is rejected -- and the error they see says the *licence* is bad, not the build. So the key is
decoded, loaded as an Ed25519 public key, and used to verify a real signature before it is written.
"""
from __future__ import annotations

import argparse
import base64
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "licence_key.py"

HEADER = '''"""Build-time licence constants. GENERATED -- do not edit, do not commit.

Written by ``tools/set_licence_key.py``. Contains the **public** half of the licence keypair only:
it verifies signatures and cannot create them, so it is safe in a shipped binary. Git-ignored all
the same, because a key that varies per deployment does not belong in version control.
"""
'''


def _decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def validate(public_key_b64: str) -> str | None:
    """``None`` if the key is usable, otherwise the reason it is not."""
    try:
        raw = _decode(public_key_b64)
    except Exception as exc:
        return f"not valid base64url ({exc})"
    if len(raw) != 32:
        return f"decodes to {len(raw)} bytes; an Ed25519 public key is 32"

    try:
        from cryptography.hazmat.primitives.asymmetric import ed25519
    except Exception as exc:  # pragma: no cover - cryptography is a hard dependency
        return f"cryptography is not importable ({exc})"

    try:
        loaded = ed25519.Ed25519PublicKey.from_public_bytes(raw)
    except Exception as exc:
        return f"is not a valid Ed25519 public key ({exc})"

    # Round-trip a signature so the key is proven to work in the verification path, not merely to
    # be the right length. Signed with a throwaway pair, then verified with the real public key --
    # which must fail. A key that "verifies" a foreign signature is not a key.
    throwaway = ed25519.Ed25519PrivateKey.generate()
    message = b"nimbus-licence-key-check"
    try:
        loaded.verify(throwaway.sign(message), message)
    except Exception:
        return None
    return "verified a signature it did not make, which is impossible"


def existing() -> tuple[str, str]:
    """The key and service URL already baked in, so ``--service-url`` alone does not drop the key."""
    if not TARGET.is_file():
        return "", ""
    key = url = ""
    for line in TARGET.read_text(encoding="utf-8").splitlines():
        name, _, value = line.partition("=")
        value = value.strip().strip('"')
        if name.strip() == "LICENCE_PUBLIC_KEY":
            key = value
        elif name.strip() == "SERVICE_URL":
            url = value
    return key, url


def write(public_key_b64: str, service_url: str) -> None:
    lines = [*HEADER.splitlines(), "", f'LICENCE_PUBLIC_KEY = "{public_key_b64}"']
    if service_url:
        lines.append(f'SERVICE_URL = "{service_url.rstrip("/")}"')
    TARGET.write_bytes(("\r\n".join(lines) + "\r\n").encode("utf-8"))


RESERVED_DEFAULT = "https://nimbus.example"
"""``licensing``'s last-resort fallback. Reaching it means nothing configured an address.

Reserved by IANA for documentation, so it can never resolve. That is the right thing for a *default*
to be and the wrong thing for a *release* to ship, which is why it is reported as a failure here.
"""


def report() -> int:
    """What a build made right now would ship with, and whether that build is usable.

    Both halves are checked, because only one of them used to be. A released installer went out with a
    valid public key and **no** service address: the workflow passed ``--public-key`` alone, a release
    checkout has no existing module to carry an address over from, and so the generated module simply had
    no ``SERVICE_URL`` line. Nothing failed. The build log was green, the key validated, and the first
    symptom was the activation dialog opening a browser at a documentation domain.

    A missing address is exactly as fatal as a missing key -- every activation posts to a host that
    cannot exist -- so it is reported the same way.
    """
    sys.path.insert(0, str(ROOT))
    for name in ("licence_key", "licensing"):
        sys.modules.pop(name, None)
    import licensing

    ok = True
    print(f"  licence_key.py   {'present' if TARGET.is_file() else 'absent'}")

    url = licensing.SERVICE_URL
    if url.rstrip("/") == RESERVED_DEFAULT:
        print(f"  service url      MISSING -- fell through to {RESERVED_DEFAULT}, which cannot resolve")
        print("                   python -m tools.set_licence_key --service-url https://<deployment>")
        ok = False
    else:
        print(f"  service url      OK  {url}")

    key = licensing.LICENCE_PUBLIC_KEY
    if not key:
        print("  public key       MISSING -- every activation in this build will be refused")
        return 1
    problem = validate(key)
    if problem:
        print(f"  public key       INVALID: {problem}")
        return 1
    print(f"  public key       OK  {key[:12]}...{key[-6:]} ({len(key)} chars)")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--public-key",
                        help="base64url Ed25519 public key from `node scripts/keygen.mjs` in web/")
    parser.add_argument("--service-url", help="licence service base URL, e.g. https://nimbus.example")
    parser.add_argument("--check", action="store_true", help="report and exit")
    parser.add_argument("--clear", action="store_true", help="remove the baked key")
    args = parser.parse_args(argv)

    if args.clear:
        TARGET.unlink(missing_ok=True)
        print(f"Removed {TARGET.name}. This build will refuse every licence.")
        return 0

    if args.check or not (args.public_key or args.service_url):
        return report()

    baked_key, baked_url = existing()
    key = args.public_key or baked_key
    if not key:
        print("ERROR: no key to write. Pass --public-key from `node scripts/keygen.mjs` in web/.")
        return 2

    problem = validate(key)
    if problem:
        print(f"ERROR: the public key {problem}")
        return 2

    write(key, args.service_url or baked_url)
    print(f"Wrote {TARGET.name}.")
    return report()


if __name__ == "__main__":
    raise SystemExit(main())
