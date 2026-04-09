"""
AES-256-CBC encryption / decryption.

Uses pycryptodome. Key (32 bytes) and IV (16 bytes) come from AppConfig,
which validates them at startup. Output is base64-encoded ciphertext so it
is safe to embed in plaintext output or transmit over HTTP.

Usage:
    from security.env_loader import load_config
    from security.encryption import Encryptor

    config = load_config()
    enc = Encryptor(config.aes_secret_key, config.aes_iv)

    ciphertext = enc.encrypt("sensitive price data")
    plaintext  = enc.decrypt(ciphertext)
"""

from __future__ import annotations

import base64

from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad


BLOCK_SIZE = AES.block_size  # 16 bytes


class Encryptor:
    """AES-256-CBC encryptor / decryptor.

    Thread-safe: a new Cipher object is created per operation because
    AES-CBC cipher objects are stateful and must not be reused.
    """

    def __init__(self, key: bytes, iv: bytes) -> None:
        if len(key) != 32:
            raise ValueError(f"AES key must be 32 bytes, got {len(key)}")
        if len(iv) != 16:
            raise ValueError(f"AES IV must be 16 bytes, got {len(iv)}")
        self._key = key
        self._iv = iv

    def encrypt(self, plaintext: str) -> str:
        """Encrypt a UTF-8 string and return a base64-encoded ciphertext.

        Args:
            plaintext: The string to encrypt.

        Returns:
            Base64-encoded ciphertext string.

        Raises:
            ValueError: If plaintext is not a string.
        """
        if not isinstance(plaintext, str):
            raise ValueError("plaintext must be a str")

        cipher = AES.new(self._key, AES.MODE_CBC, self._iv)
        padded = pad(plaintext.encode("utf-8"), BLOCK_SIZE)
        ciphertext = cipher.encrypt(padded)
        return base64.b64encode(ciphertext).decode("ascii")

    def decrypt(self, ciphertext_b64: str) -> str:
        """Decrypt a base64-encoded AES-256-CBC ciphertext.

        Args:
            ciphertext_b64: Base64-encoded ciphertext produced by encrypt().

        Returns:
            Decrypted UTF-8 string.

        Raises:
            ValueError: If the ciphertext is malformed or padding is invalid.
        """
        if not isinstance(ciphertext_b64, str):
            raise ValueError("ciphertext must be a str")

        try:
            raw = base64.b64decode(ciphertext_b64)
        except Exception as exc:
            raise ValueError(f"Invalid base64 ciphertext: {exc}") from exc

        cipher = AES.new(self._key, AES.MODE_CBC, self._iv)

        try:
            padded = cipher.decrypt(raw)
            plaintext_bytes = unpad(padded, BLOCK_SIZE)
        except (ValueError, KeyError) as exc:
            raise ValueError(f"Decryption failed (bad padding or corrupt data): {exc}") from exc

        return plaintext_bytes.decode("utf-8")
