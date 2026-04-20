from __future__ import annotations

import argparse
import base64
import json
import subprocess
import sys
import time
import urllib.request

from cloud_pc_transfer import download_with_chunk_fallback


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


PORT_RANGE = range(19830, 19840)
READY_PROBE_COMMAND = "echo codex-cloud-ready"

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Operate the local cloud PC client and remote Windows machine.")
    parser.add_argument("--auto-start-client", action="store_true")
    parser.add_argument("--auto-start-cloud", action="store_true")
    parser.add_argument("--json", action="store_true", help="Print raw JSON responses when possible.")

    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("ping")
    subparsers.add_parser("status")
    subparsers.add_parser("auth")
    subparsers.add_parser("ready")
    subparsers.add_parser("start")
    subparsers.add_parser("stop")

    exec_parser = subparsers.add_parser("exec")
    exec_parser.add_argument("--shell", choices=("cmd", "powershell"), default="cmd")
    exec_parser.add_argument("--timeout", type=int, default=30)
    exec_parser.add_argument("--detach", action="store_true")
    exec_parser.add_argument("--working-dir")
    exec_parser.add_argument("command_text", nargs="?", default=None)

    async_run_parser = subparsers.add_parser(
        "async-run",
        help=(
            "Asynchronous job start: POST JSON to the cloud PC run API /api/run (via machine exec). "
            "On success you only get the API response (e.g. pid, lock_path)—not job completion; poll GET /api/lock."
        ),
    )
    async_run_parser.add_argument("--timeout", type=int, default=30)
    async_run_parser.add_argument("--detach", action="store_true")
    async_run_parser.add_argument("--working-dir")
    async_run_parser.add_argument(
        "--run-url",
        default="http://127.0.0.1:5055/api/run",
        help="Target URL for Invoke-RestMethod POST (default: http://127.0.0.1:5055/api/run).",
    )
    async_run_parser.add_argument(
        "--api-body",
        help="Local file whose contents are the JSON POST body for /api/run.",
    )
    async_run_parser.add_argument(
        "--api-json",
        help="Inline JSON string for POST body (alternative to --api-body).",
    )
    async_run_parser.add_argument(
        "--command-part",
        action="append",
        dest="command_parts",
        metavar="PART",
        help="One argv string for command_parts in the POST body (repeat flag for each token).",
    )

    upload_parser = subparsers.add_parser("upload")
    upload_parser.add_argument("--local-path", required=True)
    upload_parser.add_argument("--remote-path", required=True)

    download_parser = subparsers.add_parser("download")
    download_parser.add_argument("--remote-path", required=True)
    download_parser.add_argument("--local-path", required=True)
    download_parser.add_argument("--tail-bytes", type=int, default=0)

    return parser.parse_args()


def request_json(base_url: str, endpoint: str, params: dict | None = None, timeout: int = 30) -> dict:
    if params is None:
        response = urllib.request.urlopen(base_url + endpoint, timeout=timeout)
    else:
        request = urllib.request.Request(
            base_url + endpoint,
            data=json.dumps(params).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        response = urllib.request.urlopen(request, timeout=timeout)
    return json.loads(response.read().decode("utf-8", errors="replace"))


def discover_port() -> int | None:
    for port in PORT_RANGE:
        try:
            data = request_json(f"http://127.0.0.1:{port}", "/api/ping", timeout=3)
            if data.get("status") == "ok":
                return port
        except Exception:
            continue
    return None


def ensure_client(auto_start_client: bool) -> int:
    port = discover_port()
    if port is not None:
        return port

    if not auto_start_client:
        raise RuntimeError("cloud client API unreachable; rerun with --auto-start-client or start swcloud manually")

    try:
        subprocess.Popen(["swcloud", "start"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except FileNotFoundError as exc:
        raise RuntimeError("swcloud not found in PATH") from exc

    for _ in range(30):
        time.sleep(1)
        port = discover_port()
        if port is not None:
            return port

    raise RuntimeError("could not connect to cloud client API after starting swcloud")


def ensure_cloud(base_url: str, auto_start_cloud: bool) -> dict:
    status = request_json(base_url, "/api/cloud/status", timeout=5)
    if not status.get("streaming"):
        if not auto_start_cloud:
            raise RuntimeError("cloud PC is not streaming; rerun with --auto-start-cloud or start it first")

        result = request_json(base_url, "/api/cloud/start", params={"show": False}, timeout=120)
        if result.get("status") == "error":
            raise RuntimeError(f"cloud start failed: [{result.get('code')}] {result.get('message')}")

    return wait_for_cloud_ready(base_url)


def wait_for_cloud_ready(base_url: str, timeout: int = 180, poll_interval: int = 5) -> dict:
    deadline = time.time() + timeout
    last_error: Exception | None = None

    while time.time() <= deadline:
        try:
            status = request_json(base_url, "/api/cloud/status", timeout=5)
            if not status.get("streaming"):
                raise RuntimeError("cloud PC is starting but not streaming yet")

            probe = request_json(
                base_url,
                "/api/machine/exec",
                params={"command": READY_PROBE_COMMAND, "shell": "cmd", "timeout": 15},
                timeout=30,
            )
            if probe.get("status") != "ok":
                raise RuntimeError(json.dumps(probe, ensure_ascii=False))

            if READY_PROBE_COMMAND.split()[-1] not in str(probe.get("stdout", "")).lower():
                raise RuntimeError("cloud PC probe did not return the expected marker")

            return status
        except Exception as exc:
            last_error = exc
            time.sleep(poll_interval)

    if last_error is not None:
        raise RuntimeError(f"cloud PC did not become ready in time: {last_error}") from last_error
    raise RuntimeError("cloud PC did not become ready in time")


def print_result(data: dict | list | str, as_json: bool) -> None:
    if isinstance(data, str):
        print(data)
    elif as_json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(data, ensure_ascii=False, indent=2))


def handle_simple(base_url: str, endpoint: str, as_json: bool) -> int:
    data = request_json(base_url, endpoint, timeout=30)
    print_result(data, as_json)
    return 0


def handle_start(base_url: str, as_json: bool) -> int:
    data = request_json(base_url, "/api/cloud/start", params={}, timeout=120)
    print_result(data, as_json)
    return 0


def handle_stop(base_url: str, as_json: bool) -> int:
    data = request_json(base_url, "/api/cloud/stop", params={}, timeout=30)
    print_result(data, as_json)
    return 0


def handle_ready(status: dict, as_json: bool) -> int:
    payload = {
        "status": "ok",
        "streaming": bool(status.get("streaming")),
        "ready_probe": "passed",
        "cloud_status": status,
    }
    print_result(payload, as_json)
    return 0


def _async_run_payload_from_args(args: argparse.Namespace) -> dict:
    if args.command_parts and (args.api_body or args.api_json):
        raise RuntimeError("do not combine --command-part with --api-body or --api-json")

    if args.command_parts:
        body_raw = json.dumps({"command_parts": args.command_parts}, ensure_ascii=False)
    elif args.api_body:
        with open(args.api_body, encoding="utf-8") as f:
            body_raw = f.read()
        json.loads(body_raw)
    elif args.api_json:
        body_raw = args.api_json
        json.loads(body_raw)
    else:
        raise RuntimeError("async-run requires --api-body, --api-json, or at least one --command-part")
    b64 = base64.b64encode(body_raw.encode("utf-8")).decode("ascii")
    url_escaped = args.run_url.replace("'", "''")
    ps = (
        f"$bytes = [Convert]::FromBase64String('{b64}'); "
        f"$body = [Text.Encoding]::UTF8.GetString($bytes); "
        f"Invoke-RestMethod -Method Post -Uri '{url_escaped}' "
        f"-ContentType 'application/json' -Body $body | ConvertTo-Json -Compress -Depth 10"
    )
    return {
        "command": ps,
        "shell": "powershell",
        "timeout": args.timeout,
        "detach": args.detach,
    }


def handle_exec(base_url: str, args: argparse.Namespace) -> int:
    if not args.command_text:
        raise RuntimeError(
            "exec requires COMMAND_TEXT (remote shell one-liner); for asynchronous POST /api/run use async-run"
        )
    payload = {
        "command": args.command_text,
        "shell": args.shell,
        "timeout": args.timeout,
        "detach": args.detach,
    }
    if args.working_dir:
        payload["working_dir"] = args.working_dir
    result = request_json(base_url, "/api/machine/exec", params=payload, timeout=args.timeout + 15)
    if args.json:
        print_result(result, True)
        return 0

    if result.get("stdout"):
        print(result["stdout"], end="" if result["stdout"].endswith("\n") else "\n")
    if result.get("stderr"):
        print(result["stderr"], file=sys.stderr, end="" if result["stderr"].endswith("\n") else "\n")
    if not result.get("stdout") and not result.get("stderr"):
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def handle_async_run(base_url: str, args: argparse.Namespace) -> int:
    payload = _async_run_payload_from_args(args)
    if args.working_dir:
        payload["working_dir"] = args.working_dir
    result = request_json(base_url, "/api/machine/exec", params=payload, timeout=args.timeout + 15)
    if args.json:
        print_result(result, True)
        return 0

    if result.get("stdout"):
        print(result["stdout"], end="" if result["stdout"].endswith("\n") else "\n")
    if result.get("stderr"):
        print(result["stderr"], file=sys.stderr, end="" if result["stderr"].endswith("\n") else "\n")
    if not result.get("stdout") and not result.get("stderr"):
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def handle_upload(base_url: str, args: argparse.Namespace, as_json: bool) -> int:
    payload = {
        "local_path": args.local_path,
        "remote_path": args.remote_path,
    }
    result = request_json(base_url, "/api/machine/upload", params=payload, timeout=120)
    print_result(result, as_json)
    return 0


def exec_powershell(base_url: str, command: str, timeout: int = 300) -> str:
    result = request_json(
        base_url,
        "/api/machine/exec",
        params={"command": command, "shell": "powershell", "timeout": timeout},
        timeout=timeout + 15,
    )
    if result.get("status") != "ok":
        raise RuntimeError(json.dumps(result, ensure_ascii=False))
    return result.get("stdout", "")


def handle_download(base_url: str, args: argparse.Namespace, as_json: bool) -> int:
    def request_download(remote_path: str, local_path: str, tail_bytes: int, timeout: int) -> dict:
        payload = {
            "remote_path": remote_path,
            "local_path": local_path,
            "tail_bytes": tail_bytes,
        }
        return request_json(base_url, "/api/machine/download", params=payload, timeout=timeout)

    if args.tail_bytes > 0:
        result = request_download(args.remote_path, args.local_path, args.tail_bytes, 300)
    else:
        result = download_with_chunk_fallback(
            request_download,
            lambda command, timeout=300: exec_powershell(base_url, command, timeout=timeout),
            args.remote_path,
            args.local_path,
            timeout=300,
        )
    print_result(result, as_json)
    return 0


def main() -> int:
    args = parse_args()

    needs_cloud = args.command in {"exec", "async-run", "upload", "download"}
    needs_streaming_check = args.command in {"ready", "exec", "async-run", "upload", "download"}

    port = ensure_client(args.auto_start_client or args.auto_start_cloud)
    base_url = f"http://127.0.0.1:{port}"
    cloud_status = None

    if args.command == "ping":
        return handle_simple(base_url, "/api/ping", args.json)
    if args.command == "status":
        return handle_simple(base_url, "/api/cloud/status", args.json)
    if args.command == "auth":
        return handle_simple(base_url, "/api/auth/status", args.json)
    if args.command == "start":
        return handle_start(base_url, args.json)
    if args.command == "stop":
        return handle_stop(base_url, args.json)

    if needs_streaming_check:
        cloud_status = ensure_cloud(base_url, args.auto_start_cloud)

    if args.command == "ready":
        return handle_ready(cloud_status or {}, args.json)

    if args.command == "exec":
        return handle_exec(base_url, args)
    if args.command == "async-run":
        return handle_async_run(base_url, args)
    if args.command == "upload":
        return handle_upload(base_url, args, args.json)
    if args.command == "download":
        return handle_download(base_url, args, args.json)

    if needs_cloud:
        raise RuntimeError(f"unsupported command: {args.command}")
    raise RuntimeError(f"unsupported command: {args.command}")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)
