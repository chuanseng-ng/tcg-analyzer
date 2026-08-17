"""The S3-compatible adapter — the only module here that knows what S3 is.

Spec §8 asks for S3-compatible storage without naming a provider, so this
adapter targets the protocol rather than AWS: MinIO locally, AWS S3 or Supabase
Storage in a deployment, configured by endpoint alone.

**Why synchronous boto3 rather than an async client.** boto3 is the reference
implementation and the one every S3-compatible provider is tested against; the
async wrappers pin a narrow botocore range and lag it. The port is async
regardless, so the difference is confined to this file: the two calls that do
I/O are offloaded to a worker thread with `anyio.to_thread.run_sync`, and URL
signing does no I/O at all — it is an HMAC over a string, so it runs inline.

Nothing here raises a botocore exception. Every failure is translated into
:mod:`tcg_shared.storage.errors`, because a caller that had to catch
`ClientError` would be coupled to the provider the port exists to hide.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Final

import anyio.to_thread
import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from tcg_shared.storage.errors import ObjectNotFound, StorageUnavailable
from tcg_shared.storage.keys import StorageKey
from tcg_shared.storage.port import SignedUrl

if TYPE_CHECKING:  # pragma: no cover - imported for typing only
    from mypy_boto3_s3.client import S3Client

__all__ = ["S3ObjectStorage", "create_s3_client"]

#: Error codes S3 uses for "that key holds nothing". `NoSuchKey` comes from
#: GetObject and `404` from HeadObject; both mean the same thing to a caller.
_NOT_FOUND_CODES: Final = frozenset({"NoSuchKey", "NoSuchBucket", "404"})


def create_s3_client(
    *,
    endpoint_url: str | None,
    region: str,
    access_key_id: str,
    secret_access_key: str,
) -> S3Client:
    """Build a boto3 S3 client aimed at any S3-compatible store.

    Args:
        endpoint_url: Where the store lives, or ``None`` for real AWS.
        region: The region to sign for. MinIO ignores it but still requires one.
        access_key_id: The access key.
        secret_access_key: The secret key.

    Returns:
        A configured client. Constructing one performs no network I/O, so this
        succeeds even when the store is down — which is what lets a readiness
        probe distinguish "misconfigured" from "unreachable".
    """
    return boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        region_name=region,
        aws_access_key_id=access_key_id,
        aws_secret_access_key=secret_access_key,
        config=Config(
            # MinIO rejects presigned URLs signed with the older algorithm, and
            # AWS has required v4 in every region since 2014.
            signature_version="s3v4",
            # MinIO addresses buckets by path. Virtual-host style would need
            # wildcard DNS pointing at the container, which local development
            # does not have.
            s3={"addressing_style": "path"},
            retries={"max_attempts": 3, "mode": "standard"},
        ),
    )


@dataclass(frozen=True, slots=True)
class S3ObjectStorage:
    """An :class:`~tcg_shared.storage.port.ObjectStorage` over an S3-compatible store.

    Args:
        client: A boto3 S3 client, e.g. from :func:`create_s3_client`.
        bucket: The bucket every key in this store is relative to. It stays
            here, not in :class:`~tcg_shared.storage.keys.StorageKey`, so that
            no caller has to know a bucket exists.
    """

    client: S3Client
    bucket: str

    async def put(self, key: StorageKey, data: bytes, *, content_type: str) -> None:
        def _put() -> None:
            self.client.put_object(
                Bucket=self.bucket,
                Key=str(key),
                Body=data,
                ContentType=content_type,
            )

        await self._offload(_put)

    async def get(self, key: StorageKey) -> bytes:
        def _get() -> bytes:
            response = self.client.get_object(Bucket=self.bucket, Key=str(key))
            body: bytes = response["Body"].read()
            return body

        return await self._offload(_get, missing_key=key)

    async def delete(self, key: StorageKey) -> None:
        def _delete() -> None:
            self.client.delete_object(Bucket=self.bucket, Key=str(key))

        # S3's DeleteObject is already idempotent: removing an absent key
        # succeeds, which is the outcome the caller wanted either way.
        await self._offload(_delete)

    async def signed_upload_url(
        self,
        key: StorageKey,
        *,
        content_type: str,
        expires_in: timedelta,
    ) -> SignedUrl:
        return self._sign(
            "put_object",
            {"Bucket": self.bucket, "Key": str(key), "ContentType": content_type},
            expires_in,
        )

    async def signed_download_url(self, key: StorageKey, *, expires_in: timedelta) -> SignedUrl:
        return self._sign(
            "get_object",
            {"Bucket": self.bucket, "Key": str(key)},
            expires_in,
        )

    # ----------------------------------------------------------------
    # Internals
    # ----------------------------------------------------------------

    def _sign(self, operation: str, params: dict[str, Any], expires_in: timedelta) -> SignedUrl:
        """Mint a presigned URL.

        Signing is an HMAC over a canonical request string — no network call —
        so this runs inline rather than paying for a thread hop. It is also why
        a signed URL can be issued while the store itself is unreachable.
        """
        expires_at = datetime.now(UTC) + expires_in
        try:
            url = self.client.generate_presigned_url(
                operation,
                Params=params,
                ExpiresIn=int(expires_in.total_seconds()),
            )
        except (BotoCoreError, ClientError) as exc:
            raise StorageUnavailable(f"could not sign a URL for {params['Key']}: {exc}") from exc
        return SignedUrl(url=url, expires_at=expires_at)

    @staticmethod
    async def _offload[T](call: Callable[[], T], missing_key: StorageKey | None = None) -> T:
        """Run a blocking boto3 call off the event loop, translating its errors.

        Args:
            call: The zero-argument callable to run.
            missing_key: When set, a "no such key" response becomes
                :class:`ObjectNotFound` naming it instead of
                :class:`StorageUnavailable`.
        """
        try:
            result = await anyio.to_thread.run_sync(call)
        except ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", ""))
            if missing_key is not None and code in _NOT_FOUND_CODES:
                raise ObjectNotFound(f"no object is stored under {missing_key}") from exc
            raise StorageUnavailable(f"the object store refused the request: {exc}") from exc
        except BotoCoreError as exc:
            # Connection, endpoint-resolution and credential failures all land
            # here. They mean the same thing to a caller: not reachable.
            raise StorageUnavailable(f"the object store could not be reached: {exc}") from exc
        return result
