"""Object storage for raw payloads and gridded fields (PLAN.md Phase 1.10).

Two different things live in the object store, and conflating them causes pain later:

* **Raw payloads** — bytes exactly as an upstream returned them, content-addressed, for
  replay and dispute resolution. Handled by ``archive.py``; this module provides the S3
  client it uses.
* **Subsetted gridded fields** — the same data reshaped for analysis, written as Zarr so
  xarray can open a region without downloading the whole field.

**Scope, stated honestly:** Zarr is implemented and tested. **COG is not implemented** —
writing Cloud-Optimized GeoTIFF needs ``rasterio``/GDAL, which is a heavy native
dependency that nothing in ORCA consumes yet. The `GridStore` interface is shaped so a
COG writer drops in beside ``write_zarr`` when a raster consumer actually exists. It is
better to ship one working format than two half-working ones.
"""

from __future__ import annotations

from typing import Any

DEFAULT_REGION = "us-east-1"


def build_s3_client(
    *,
    endpoint_url: str,
    access_key: str,
    secret_key: str,
    region: str = DEFAULT_REGION,
) -> Any:
    """Create a boto3 S3 client pointed at MinIO (dev) or S3 (production).

    Path-style addressing is required for MinIO, which does not do virtual-host buckets.
    """
    import boto3
    from botocore.config import Config

    return boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name=region,
        config=Config(s3={"addressing_style": "path"}, signature_version="s3v4"),
    )


def ensure_bucket(client: Any, bucket: str) -> None:
    """Create the bucket when absent. Idempotent."""
    from botocore.exceptions import ClientError

    try:
        client.head_bucket(Bucket=bucket)
    except ClientError:
        client.create_bucket(Bucket=bucket)


class GridStore:
    """Writes subsetted gridded fields to object storage as Zarr.

    Requires the ``grids`` extra (``xarray``, ``zarr``, ``numpy``). The ingest core runs
    without it; only gridded persistence needs it, so the import is deferred and the
    absence is reported clearly rather than crashing at import time.
    """

    def __init__(self, *, endpoint_url: str, access_key: str, secret_key: str, bucket: str) -> None:
        self._endpoint_url = endpoint_url
        self._access_key = access_key
        self._secret_key = secret_key
        self._bucket = bucket

    @staticmethod
    def available() -> bool:
        """Whether the optional gridded stack is installed."""
        try:
            import xarray  # noqa: F401
            import zarr  # noqa: F401
        except ImportError:
            return False
        return True

    def write_zarr(self, dataset: Any, key: str) -> str:
        """Write an ``xarray.Dataset`` to ``s3://<bucket>/<key>`` as Zarr.

        Returns the URI. Raises ``RuntimeError`` when the optional stack is missing,
        naming the extra to install.
        """
        if not self.available():
            msg = (
                "gridded persistence needs the 'grids' extra: "
                "pip install -e 'services/ingest[grids]'"
            )
            raise RuntimeError(msg)

        import s3fs

        fs = s3fs.S3FileSystem(
            key=self._access_key,
            secret=self._secret_key,
            client_kwargs={"endpoint_url": self._endpoint_url},
        )
        uri = f"s3://{self._bucket}/{key}"
        dataset.to_zarr(store=s3fs.S3Map(root=uri, s3=fs), mode="w", consolidated=True)
        return uri

    def write_zarr_local(self, dataset: Any, path: str) -> str:
        """Write Zarr to a local path.

        Used by tests and by the offline/degraded path, where object storage may not be
        reachable but the cached field still has to be readable.
        """
        if not self.available():
            msg = (
                "gridded persistence needs the 'grids' extra: "
                "pip install -e 'services/ingest[grids]'"
            )
            raise RuntimeError(msg)
        dataset.to_zarr(path, mode="w", consolidated=True)
        return path

    def write_cog(self, dataset: Any, key: str) -> str:
        """Not implemented.

        Cloud-Optimized GeoTIFF output needs rasterio/GDAL and has no consumer in ORCA
        yet. Raising is deliberate: a stub that silently wrote something else, or
        returned a URI to a file that is not a COG, would be worse than an honest gap.
        """
        msg = (
            "COG output is not implemented: it requires rasterio/GDAL and no ORCA component "
            "consumes rasters yet. Use write_zarr for gridded fields."
        )
        raise NotImplementedError(msg)
