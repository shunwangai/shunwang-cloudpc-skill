from __future__ import annotations

import hashlib
import json
import time
import uuid
from pathlib import Path


FILE_TOO_LARGE_CODE = "FILE_TOO_LARGE"
DEFAULT_CHUNK_SIZE_BYTES = 45 * 1024 * 1024


def ps_literal(value: str) -> str:
    return value.replace("'", "''")


def remote_api_path(value: str) -> str:
    return value.replace("\\", "/")


def md5_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.md5()
    with path.open("rb") as handle:
        while True:
            block = handle.read(chunk_size)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def merge_parts(part_paths: list[Path], output_path: Path, buffer_size: int = 1024 * 1024) -> None:
    with output_path.open("wb") as destination:
        for part_path in part_paths:
            with part_path.open("rb") as source:
                while True:
                    block = source.read(buffer_size)
                    if not block:
                        break
                    destination.write(block)


def wait_for_local_file(path: Path, expected_size: int | None = None, attempts: int = 240, delay_seconds: float = 1.0) -> None:
    last_error: Exception | None = None
    for _ in range(attempts):
        try:
            size = path.stat().st_size
            if expected_size is not None and size != int(expected_size):
                raise RuntimeError(f"expected {expected_size} bytes, got {size}")
            with path.open("rb") as handle:
                handle.read(1)
            return
        except (FileNotFoundError, PermissionError, OSError, RuntimeError) as exc:
            last_error = exc
            time.sleep(delay_seconds)
    if last_error is not None:
        raise RuntimeError(f"local chunk is not ready: {path}: {last_error}") from last_error
    raise RuntimeError(f"local chunk is not ready: {path}")


def split_remote_file(
    exec_ps,
    remote_path: str,
    chunk_size_bytes: int = DEFAULT_CHUNK_SIZE_BYTES,
    timeout: int = 300,
) -> dict:
    command = (
        f"$source='{ps_literal(remote_path)}'; "
        f"$chunkSize={int(chunk_size_bytes)}; "
        "$item = Get-Item -LiteralPath $source -Force; "
        "if ($item.PSIsContainer) { throw 'chunked download only supports files' } "
        "$stagingDir = Join-Path $item.DirectoryName ($item.Name + '.codex-download-' + [guid]::NewGuid().ToString('N')); "
        "New-Item -ItemType Directory -Force -Path $stagingDir | Out-Null; "
        "$buffer = New-Object byte[] $chunkSize; "
        "$stream = [System.IO.File]::OpenRead($item.FullName); "
        "$chunks = @(); "
        "try { "
        "  $index = 0; "
        "  while (($read = $stream.Read($buffer, 0, $buffer.Length)) -gt 0) { "
        "    $chunkPath = Join-Path $stagingDir ('part-{0:D4}.bin' -f $index); "
        "    $chunkStream = [System.IO.File]::Open($chunkPath, [System.IO.FileMode]::Create, [System.IO.FileAccess]::Write, [System.IO.FileShare]::None); "
        "    try { $chunkStream.Write($buffer, 0, $read) } finally { $chunkStream.Dispose() } "
        "    $chunks += [pscustomobject]@{ path = $chunkPath; bytes = $read; index = $index }; "
        "    $index += 1 "
        "  } "
        "} finally { "
        "  $stream.Dispose() "
        "} "
        "[pscustomobject]@{ "
        "  original_path = $item.FullName; "
        "  original_size = $item.Length; "
        "  staging_dir = $stagingDir; "
        "  chunk_size = $chunkSize; "
        "  chunks = $chunks "
        "} | ConvertTo-Json -Depth 4 -Compress"
    )
    stdout = exec_ps(command, timeout=timeout).strip()
    payload = json.loads(stdout) if stdout else {}
    chunks = payload.get("chunks")
    if isinstance(chunks, dict):
        payload["chunks"] = [chunks]
    elif not isinstance(chunks, list):
        payload["chunks"] = []
    return payload


def cleanup_remote_chunks(exec_ps, staging_dir: str | None, timeout: int = 300) -> None:
    if not staging_dir:
        return
    command = (
        f"$path='{ps_literal(staging_dir)}'; "
        "if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Recurse -Force }"
    )
    exec_ps(command, timeout=timeout)


def download_with_chunk_fallback(
    download_request,
    exec_ps,
    remote_path: str,
    local_path: str | Path,
    timeout: int = 300,
    chunk_size_bytes: int = DEFAULT_CHUNK_SIZE_BYTES,
) -> dict:
    destination = Path(local_path).expanduser()
    destination.parent.mkdir(parents=True, exist_ok=True)

    direct_result = download_request(remote_path, str(destination), 0, timeout)
    if direct_result.get("status") != "error" or direct_result.get("code") != FILE_TOO_LARGE_CODE:
        return direct_result

    split_info: dict | None = None
    local_parts: list[Path] = []
    try:
        split_info = split_remote_file(exec_ps, remote_path, chunk_size_bytes=chunk_size_bytes, timeout=timeout)
        chunks = split_info.get("chunks") or []
        if not chunks:
            raise RuntimeError(f"failed to split remote file for download: {remote_path}")

        token = uuid.uuid4().hex[:8]

        for chunk in chunks:
            remote_chunk_path = str(chunk.get("path") or "").strip()
            if not remote_chunk_path:
                raise RuntimeError(f"chunk metadata missing path for remote file: {remote_path}")
            chunk_index = int(chunk.get("index", len(local_parts)))
            local_part_path = destination.parent / f"{destination.name}.codex-part-{token}-{chunk_index:04d}"
            chunk_result = download_request(remote_chunk_path, str(local_part_path), 0, timeout)
            if chunk_result.get("status") == "error":
                raise RuntimeError(
                    f"chunk download failed for {remote_chunk_path}: "
                    f"[{chunk_result.get('code')}] {chunk_result.get('message')}"
                )
            expected_size = chunk.get("bytes")
            wait_for_local_file(local_part_path, expected_size=int(expected_size) if expected_size is not None else None)
            local_parts.append(local_part_path)

        merge_parts(local_parts, destination)

        expected_total_size = split_info.get("original_size")
        actual_size = destination.stat().st_size
        if expected_total_size is not None and actual_size != int(expected_total_size):
            raise RuntimeError(
                f"reassembled file size mismatch for {remote_path}: "
                f"expected {expected_total_size}, got {actual_size}"
            )

        return {
            "status": "ok",
            "remote_path": remote_path,
            "local_path": str(destination),
            "bytes_transferred": actual_size,
            "md5": md5_file(destination),
            "download_mode": "chunked",
            "chunk_count": len(local_parts),
            "chunk_size_bytes": int(chunk_size_bytes),
        }
    finally:
        for local_part in local_parts:
            try:
                local_part.unlink(missing_ok=True)
            except OSError:
                pass
        if split_info is not None:
            cleanup_remote_chunks(exec_ps, split_info.get("staging_dir"), timeout=timeout)
