"""CoreGuard import hook -- decrypts encrypted modules on-the-fly.

This module installs a sys.meta_path finder that intercepts imports of
CoreGuard-encrypted .py files. When Python tries to import an encrypted
module, the hook:
1. Checks if a .py.enc/.py.key pair exists alongside the .py stub
2. Decrypts the .py.enc file using the stored key
3. Creates a proper Python module from the decrypted source
4. Caches the decrypted module for future imports

Usage:
    import src.coreguard_hook
    # Hook is auto-installed when this module is imported.

This MUST be imported before any encrypted modules are imported.
"""
import hashlib
import hmac
import sys
import types
from importlib.util import spec_from_loader
from pathlib import Path

# -- Configuration -----------------------------------------------------------

OMNITRADER_ROOT = Path(__file__).resolve().parent.parent
SEED = "hydra_seed_2026"
ENTROPY_READ_SIZE = 32
KEY_SALT = b"ouroboros-coreguard-kdf-v1"

# Cache decrypted modules
_decrypted_cache: dict[str, types.ModuleType] = {}


def hkdf_sha256(ikm: bytes, length: int = 32, salt: bytes = b"",
                info: bytes = b"") -> bytes:
    """HKDF-SHA256 (RFC 5869)."""
    if not salt:
        salt = b"\x00" * 32
    prk = hmac.new(salt, ikm, hashlib.sha256).digest()
    t, output = b"", b""
    counter = 1
    while len(output) < length:
        t = hmac.new(prk, t + info + bytes([counter]),
                     hashlib.sha256).digest()
        output += t
        counter += 1
    return output[:length]


def _decrypt(enc_path: Path, key_hex: str) -> bytes:
    """Decrypt a .py.enc file given the hex key."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    key_bytes = bytes.fromhex(key_hex)
    ciphertext = enc_path.read_bytes()
    nonce = ciphertext[:16]
    ct = ciphertext[16:]
    return AESGCM(key_bytes).decrypt(nonce, ct, None)


def _resolve_module_path(fullname: str) -> tuple[Path, Path, Path]:
    """Resolve module name to py_path, enc_path, key_path.

    E.g. 'src.utils.logging_config' ->
      py:  /path/.../src/utils/logging_config.py
      enc: /path/.../src/utils/logging_config.py.enc
      key: /path/.../src/utils/logging_config.py.key
    """
    rel = fullname[4:]  # strip "src." prefix -> "utils.logging_config"
    parts = rel.split(".")
    # Build path: OMNITRADER_ROOT/src/utils/logging_config
    base = OMNITRADER_ROOT / "src" / parts[0]
    for part in parts[1:]:
        base = base / part
    # Add .py suffix
    py_path = base.with_suffix(".py")
    enc_path = Path(str(py_path) + ".enc")
    key_path = Path(str(py_path) + ".key")
    return py_path, enc_path, key_path


class _CoreGuardFinder:
    """sys.meta_path finder for CoreGuard-encrypted modules."""

    def find_spec(self, fullname, path=None, target=None):
        """Return a ModuleSpec that decrypts encrypted source on load."""
        if not fullname.startswith("src."):
            return None
        py_path, enc_path, key_path = _resolve_module_path(fullname)
        if enc_path.exists() and key_path.exists():
            loader = _CoreGuardLoader(py_path, enc_path, key_path, fullname)
            return spec_from_loader(fullname, loader, origin=str(enc_path),
                                    is_package=False)
        return None


class _CoreGuardLoader:
    """Loader that decrypts and executes encrypted modules."""

    def __init__(self, py_path, enc_path, key_path, module_name):
        self.py_path = py_path
        self.enc_path = enc_path
        self.key_path = key_path
        self.module_name = module_name

    def create_module(self, spec):
        if self.module_name in _decrypted_cache:
            return _decrypted_cache[self.module_name]
        try:
            # Use the stored key
            key_hex = self.key_path.read_text().strip()
            plaintext = _decrypt(self.enc_path, key_hex)
            if not plaintext or len(plaintext) < 50:
                raise ValueError(
                    f"Decrypted payload too small for {self.module_name}")
            module = types.ModuleType(self.module_name)
            module.__file__ = str(self.py_path)
            module.__loader__ = self
            pkg = self.module_name.rsplit(".", 1)[0] if "." in self.module_name else ""
            module.__package__ = pkg
            module.__path__ = []
            exec(compile(plaintext, str(self.py_path), "exec"), module.__dict__)
            _decrypted_cache[self.module_name] = module
            return module
        except Exception as e:
            print(f"[CoreGuard] Failed to decrypt {self.module_name}: {e}",
                  file=sys.stderr)
            import traceback
            traceback.print_exc(file=sys.stderr)
            return None

    def exec_module(self, module):
        pass


def install_hook():
    """Install the CoreGuard import hook into sys.meta_path."""
    finder = _CoreGuardFinder()
    sys.meta_path.insert(0, finder)
    print("[CoreGuard] Import hook installed.")


# Auto-install on import
install_hook()
