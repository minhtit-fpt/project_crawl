"""Tests for security/encryption.py — AES-256-CBC Encryptor."""

import base64
import pytest

from security.encryption import Encryptor

# 32-byte key and 16-byte IV for testing (never use in production)
VALID_KEY = bytes.fromhex("0" * 64)   # 32 zero bytes
VALID_IV  = bytes.fromhex("0" * 32)   # 16 zero bytes


@pytest.fixture()
def enc() -> Encryptor:
    return Encryptor(VALID_KEY, VALID_IV)


class TestEncryptorInit:
    def test_accepts_valid_key_and_iv(self):
        Encryptor(VALID_KEY, VALID_IV)  # should not raise

    def test_rejects_short_key(self):
        with pytest.raises(ValueError, match="32 bytes"):
            Encryptor(b"short", VALID_IV)

    def test_rejects_short_iv(self):
        with pytest.raises(ValueError, match="16 bytes"):
            Encryptor(VALID_KEY, b"short")


class TestEncrypt:
    def test_returns_string(self, enc):
        result = enc.encrypt("hello")
        assert isinstance(result, str)

    def test_returns_valid_base64(self, enc):
        result = enc.encrypt("hello")
        # Should not raise
        decoded = base64.b64decode(result)
        assert len(decoded) > 0

    def test_non_string_raises(self, enc):
        with pytest.raises(ValueError):
            enc.encrypt(12345)  # type: ignore

    def test_empty_string(self, enc):
        result = enc.encrypt("")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_unicode_string(self, enc):
        result = enc.encrypt("giá: 1.234.567đ")
        assert isinstance(result, str)

    def test_different_plaintexts_produce_different_ciphertexts(self, enc):
        c1 = enc.encrypt("price: 100")
        c2 = enc.encrypt("price: 200")
        assert c1 != c2


class TestDecrypt:
    def test_roundtrip_ascii(self, enc):
        plaintext = "SKU-001 price: 99.99"
        assert enc.decrypt(enc.encrypt(plaintext)) == plaintext

    def test_roundtrip_unicode(self, enc):
        plaintext = "giá sản phẩm: 1.500.000đ"
        assert enc.decrypt(enc.encrypt(plaintext)) == plaintext

    def test_roundtrip_empty_string(self, enc):
        assert enc.decrypt(enc.encrypt("")) == ""

    def test_invalid_base64_raises(self, enc):
        with pytest.raises(ValueError, match="base64"):
            enc.decrypt("not-valid-base64!!!")

    def test_non_string_raises(self, enc):
        with pytest.raises(ValueError):
            enc.decrypt(12345)  # type: ignore

    def test_corrupt_ciphertext_raises(self, enc):
        # Valid base64 but wrong content → bad padding
        garbage = base64.b64encode(b"x" * 17).decode()
        with pytest.raises(ValueError, match="Decryption failed"):
            enc.decrypt(garbage)


class TestThreadSafety:
    def test_multiple_encryptions_are_independent(self, enc):
        """Each encrypt call must create a new cipher — results must be consistent."""
        results = [enc.encrypt("same text") for _ in range(10)]
        # All should decrypt correctly (same IV means same ciphertext each time)
        for r in results:
            assert enc.decrypt(r) == "same text"
