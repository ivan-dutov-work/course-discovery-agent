from __future__ import annotations

import base64
import inspect
import os
import re
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from langgraph.checkpoint.base import BaseCheckpointSaver, Checkpoint, CheckpointTuple
from langgraph.checkpoint.serde.base import CipherProtocol, SerializerProtocol
from langgraph.checkpoint.serde.encrypted import EncryptedSerializer


KEYS_ENV = "CHECKPOINT_ENCRYPTION_KEYS"
REQUIRED_ENV = "CHECKPOINT_ENCRYPTION_REQUIRED"
CIPHER_PREFIX = "aesgcm."
INLINE_PREFIX = "enc:v1:"
_KEY_ID = re.compile(r"[A-Za-z0-9_-]+")


class EncryptionConfigError(RuntimeError):
    pass


def parse_keys(spec: str) -> list[tuple[str, bytes]]:
    keys: list[tuple[str, bytes]] = []
    for item in filter(None, (part.strip() for part in spec.split(","))):
        key_id, sep, encoded = item.partition(":")
        if not sep or not _KEY_ID.fullmatch(key_id):
            raise EncryptionConfigError(f"{KEYS_ENV} entries must look like <id>:<base64 key>")
        key = base64.b64decode(encoded, validate=True)
        if len(key) != 32:
            raise EncryptionConfigError(f"key {key_id!r} must decode to 32 bytes")
        keys.append((key_id, key))
    if len({key_id for key_id, _ in keys}) != len(keys):
        raise EncryptionConfigError(f"{KEYS_ENV} contains a duplicate key id")
    return keys


def keys_from_env() -> list[tuple[str, bytes]]:
    keys = parse_keys(os.getenv(KEYS_ENV, ""))
    if not keys and os.getenv(REQUIRED_ENV, "").lower() == "true":
        raise EncryptionConfigError(f"{REQUIRED_ENV}=true but {KEYS_ENV} is not set")
    return keys


class AesGcmCipher(CipherProtocol):
    """First key encrypts; every key can decrypt, so rotation is: prepend a new key."""

    def __init__(self, keys: list[tuple[str, bytes]]) -> None:
        if not keys:
            raise EncryptionConfigError("at least one key is required")
        self._primary_id, primary = keys[0]
        self._aead = {key_id: AESGCM(key) for key_id, key in keys}
        self._primary = self._aead[self._primary_id]

    def encrypt(self, plaintext: bytes) -> tuple[str, bytes]:
        nonce = os.urandom(12)
        return CIPHER_PREFIX + self._primary_id, nonce + self._primary.encrypt(nonce, plaintext, None)

    def decrypt(self, ciphername: str, ciphertext: bytes) -> bytes:
        key_id = ciphername.removeprefix(CIPHER_PREFIX)
        aead = self._aead.get(key_id)
        if aead is None or not ciphername.startswith(CIPHER_PREFIX):
            raise EncryptionConfigError(f"no key available for cipher {ciphername!r}")
        return aead.decrypt(ciphertext[:12], ciphertext[12:], None)

    def seal_text(self, text: str) -> str:
        ciphername, blob = self.encrypt(text.encode())
        return f"{INLINE_PREFIX}{ciphername}:{base64.b64encode(blob).decode()}"

    def open_text(self, sealed: str) -> str:
        ciphername, _, encoded = sealed.removeprefix(INLINE_PREFIX).partition(":")
        return self.decrypt(ciphername, base64.b64decode(encoded)).decode()


def encrypt_serde(serde: SerializerProtocol, cipher: AesGcmCipher | None) -> SerializerProtocol:
    return serde if cipher is None else EncryptedSerializer(cipher, serde)


def _seal_checkpoint(cipher: AesGcmCipher, checkpoint: Checkpoint) -> Checkpoint:
    return {
        **checkpoint,
        "channel_values": {
            name: cipher.seal_text(value) if type(value) is str else value
            for name, value in checkpoint["channel_values"].items()
        },
    }


def _open_tuple(cipher: AesGcmCipher, item: CheckpointTuple | None) -> CheckpointTuple | None:
    if item is None:
        return None
    values = {
        name: cipher.open_text(value)
        if isinstance(value, str) and value.startswith(INLINE_PREFIX)
        else value
        for name, value in item.checkpoint["channel_values"].items()
    }
    return item._replace(checkpoint={**item.checkpoint, "channel_values": values})


class SealingSaver(BaseCheckpointSaver):
    """Seals string channel values on their way into any saver, using only its public API.

    Savers may keep primitive channel values outside the serde (the Postgres saver inlines
    them as plaintext JSON), so serde encryption alone does not cover them."""

    def __init__(self, inner: BaseCheckpointSaver, cipher: AesGcmCipher) -> None:
        super().__init__(serde=inner.serde)
        self.inner = inner
        self._cipher = cipher

    @property
    def config_specs(self) -> list:
        return self.inner.config_specs

    def put(self, config, checkpoint, metadata, new_versions):
        return self.inner.put(config, _seal_checkpoint(self._cipher, checkpoint), metadata, new_versions)

    async def aput(self, config, checkpoint, metadata, new_versions):
        return await self.inner.aput(
            config, _seal_checkpoint(self._cipher, checkpoint), metadata, new_versions
        )

    def get_tuple(self, config):
        return _open_tuple(self._cipher, self.inner.get_tuple(config))

    async def aget_tuple(self, config):
        return _open_tuple(self._cipher, await self.inner.aget_tuple(config))

    def list(self, config, *, filter=None, before=None, limit=None):
        for item in self.inner.list(config, filter=filter, before=before, limit=limit):
            yield _open_tuple(self._cipher, item)

    async def alist(self, config, *, filter=None, before=None, limit=None):
        async for item in self.inner.alist(config, filter=filter, before=before, limit=limit):
            yield _open_tuple(self._cipher, item)

    def with_allowlist(self, extra_allowlist):
        return SealingSaver(self.inner.with_allowlist(extra_allowlist), self._cipher)


TRANSFORMED = {"put", "aput", "get_tuple", "aget_tuple", "list", "alist", "with_allowlist"}
INHERITED = {"get", "aget"}


def _delegate(name: str):
    if inspect.iscoroutinefunction(getattr(BaseCheckpointSaver, name)):

        async def call(self, *args: Any, **kwargs: Any) -> Any:
            return await getattr(self.inner, name)(*args, **kwargs)

    else:

        def call(self, *args: Any, **kwargs: Any) -> Any:
            return getattr(self.inner, name)(*args, **kwargs)

    call.__name__ = name
    return call


for _name, _member in inspect.getmembers(BaseCheckpointSaver, inspect.isfunction):
    if not _name.startswith("_") and _name not in TRANSFORMED | INHERITED:
        setattr(SealingSaver, _name, _delegate(_name))
