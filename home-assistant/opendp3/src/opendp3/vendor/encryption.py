"""ha-ef-ble AES codecs; OpenDP3 removed the unused developer-diagnostics crypto.

Original attribution and local changes: THIRD_PARTY_NOTICES.md.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass

from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad


@dataclass
class EncryptionStrategy(ABC):
    """Strategy for session-level AES-CBC encryption/decryption"""

    session_key: bytes
    iv: bytes

    @abstractmethod
    async def encrypt(self, plaintext: bytes) -> bytes: ...

    @abstractmethod
    async def decrypt(self, ciphertext: bytes) -> bytes: ...


class Type7Encryption(EncryptionStrategy):
    async def encrypt(self, plaintext: bytes) -> bytes:
        cipher = AES.new(self.session_key, AES.MODE_CBC, self.iv)
        return cipher.encrypt(plaintext=pad(plaintext, AES.block_size))

    async def decrypt(self, ciphertext: bytes) -> bytes:
        # native firmware decrypts only full AES blocks and discards any trailing bytes
        # that don't fill a complete block
        aligned = len(ciphertext) - len(ciphertext) % AES.block_size
        if aligned == 0:
            return ciphertext
        cipher = AES.new(self.session_key, AES.MODE_CBC, self.iv)
        decrypted = cipher.decrypt(ciphertext[:aligned])
        try:
            return unpad(decrypted, AES.block_size)
        except ValueError:
            return decrypted


class Type1Encryption(EncryptionStrategy):
    async def encrypt(self, plaintext: bytes) -> bytes:
        padded_len = (len(plaintext) + 15) // 16 * 16
        padded = plaintext + b"\x00" * (padded_len - len(plaintext))
        cipher = AES.new(self.session_key, AES.MODE_CBC, self.iv)
        return cipher.encrypt(padded)

    async def decrypt(self, ciphertext: bytes) -> bytes:
        cipher = AES.new(self.session_key, AES.MODE_CBC, self.iv)
        return cipher.decrypt(ciphertext)


