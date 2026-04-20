---
name: shunwang-cloudpc
description: Operate a remote cloud Windows PC through the local swcloud client. 
---

# Shunwang Cloud PC

Use this skill for remote operations on the cloud Windows machine exposed by the local `swcloud` client.

**Remote environment:** The cloud PC is **Windows** (not Linux/macOS). Every command you send via `exec` / `async-run` runs **on that Windows VM**. Use **Windows shell syntax**: default **`cmd`** (`dir`, `type`, `copy`, `move`, `del`, `where`, backslash paths like `Z:\...` or forward-slash API style `Z:/...`), or **`powershell`** when you need PowerShell (`Get-ChildItem`, `Get-Content`, `Invoke-RestMethod`, etc.). **Do not** assume Unix tools (`ls`, `cat`, `grep`, `cp`, `/tmp`, POSIX paths) exist on the remote side unless you have verified a specific toolchain.

**Local files (agent / developer machine):** Reading, writing, or inspecting files **on this machine** does not need a cloud-pc workflow—use normal local tools (editor, `read_file`, a single local shell command, etc.). Reserve `cloud_pc_api.py` (`upload`, `download`, `exec`, `async-run`) for **cloud** lifecycle and **VM** paths. Do not overcomplicate local file work with upload/download choreography when the file is already local.

Prefer the bundled scripts over ad hoc inline API calls:

- `scripts/cloud_pc_api.py` for lifecycle, remote shell execution, **`async-run`** (异步 POST `/api/run` via `run_flask_api.py`; success = pid + response only—**poll `GET /api/lock`** to detect non-zero exit / failure), upload, and download

All `scripts/...` paths below are relative to this skill directory, so do not hardcode a user-specific home path.

## Cloud skills roots (authoritative)

On the cloud Windows VM there are **exactly two** skill roots—**an exhaustive list**. **Do not** search elsewhere for skill manifests (not under `AIProject`, `tools`, `Desktop`, or any other path). Runtimes and assets may live outside these folders, but **listing, resolving, and reading app manifests** happens **only** here.

Remote discovery is **filesystem-based**—there is no separate registry API. **Both** directories below always matter (same manifest layout per child folder; see Registry Layout):

1. **`Z:/aicloud/astart/ai-service/skills/`** — platform / bundled tree; the directory name is **`skills`** (plural) because it contains many **skill** subfolders (one app per child).
2. **`Q:/skills/`** — **user-customized** skills; same naming rule.

Browse and open manifests via remote `dir`, `Get-ChildItem`, `type` / `Get-Content`, etc. If a name is not under **either** path above, treat the skill as **not installed** on the VM—do not hunt other drives or trees.

## When To Use

Use this skill when the user asks to:

- start, stop, or inspect the cloud PC
- run commands on the cloud machine
- upload or download files
- inspect GPU, drivers, or environment details
- list available AI apps on the cloud machine
- run an installed AI app by name
- satisfy a natural-language request by choosing and executing the right installed app

## Core Rules

1. Always prefer the bundled scripts first.
2. Use Python or the bundled scripts for the localhost API. Do not use `curl`.
   - **`exec`** (positional `COMMAND_TEXT`) → calls `swcloud /api/machine/exec`; **blocks until the command finishes**; suitable for **quick queries** that finish in **< ~10 seconds** (readiness probes, `dir`, small `type`/`Get-Content`, `nvidia-smi`, etc.)
   - **`async-run`（异步）** → runs PowerShell on the cloud PC that POSTs JSON to `run_flask_api.py` **`POST /api/run`**. **Does not wait for the workload to finish**: a successful call only confirms the runner **accepted** the job (`pid`, `lock_path`, etc.). **Mandatory:** poll **`GET /api/lock`** on the same Flask runner until the task ends—when the child process has exited, the lock payload includes **`task_finished`**, **`last_exit_code`**, **`task_failed`**, and **`task_error`** if the exit code was non-zero. Without **`/api/lock`**, the API path gives **no stderr/traceback and no failure signal** (child output goes to the Flask process console on the VM only, unless the app writes its own log files). Suitable for **any task that may run longer than ~10 seconds** (training, inference, music generation, etc.)
   - **On `async-run`, body sources**: `--command-part` (repeat per argv token) builds `command_parts`; **`--api-json`** is an inline JSON string for the full POST body; **`--api-body`** is a local UTF-8 file with that JSON. **Do not combine** `--command-part` with `--api-body` or `--api-json`. For flags with values (`--output_path`, `--input_params_file`, etc.), prefer **`--api-json`** or **`--api-body`** with a full `command_parts` (or `command`/`cwd`) object.
   - **`--json` (global):** prints structured JSON for lifecycle/upload/download; for **`exec`** / **`async-run`**, prints the raw **`/api/machine/exec`** response instead of only `stdout`/`stderr`.
   - **Rule: use `exec` + one Windows shell line for viewing results/status and for quick file ops on the VM (`dir`, `type`, `Get-Content`, small copies); use `async-run` + `--api-json` or `--api-body` for starting long-running jobs with complex argv**
3. Always use forward slashes for remote Windows paths passed to the API, for example `C:/Users/Administrator/Desktop` (the VM is still Windows; slashes are for JSON/API convenience).
4. Before any remote operation, first warm the cloud PC until it is actually reachable, not just "starting". Use `python3 scripts/cloud_pc_api.py --auto-start-client --auto-start-cloud ready` when you need an explicit preflight. Treat that warmup as required before `exec`, `async-run`, `upload`, and `download` when the session might be cold.
5. Default to `cmd` for simple **Windows** commands on the VM. Use `powershell` when the command needs PowerShell syntax or the app manifest already specifies PowerShell.
6. GUI apps must use detached execution.
7. **Remote skill paths:** **only** **`Z:/aicloud/astart/ai-service/skills/`** and **`Q:/skills/`**—see **[Cloud skills roots](#cloud-skills-roots-authoritative)**. Always list **both**; never add a third lookup location. Do not treat local notes (e.g. `aiapp.md`) as authoritative over what is on the cloud disks.
8. If an app manifest is malformed JSON, recover conservatively and continue when possible.
9. Do not invent required app arguments. Ask only for the missing values.
10. When an app writes outputs on the cloud PC, fetch them with `python3 scripts/cloud_pc_api.py --auto-start-cloud download --remote-path ... --local-path ...`. Default to saving under the user's Desktop unless they specify another `--local-path`.
11. **Download size limit (~50 MB):** Treat **50 MB** as the practical single-download ceiling for the localhost ↔ cloud transfer API. If a remote artifact is **larger than 50 MB** (or the API rejects the transfer), **do not** rely on a single raw download: use **remote split/chunk download** (what `cloud_pc_api.py` does—parts on the VM, download each part, reassemble locally) and/or **compress or segment** the payload on the cloud PC first so each piece stays under the limit. Prefer **lossy or lossless compression** for media when it preserves the user’s intent.
12. **Before starting or stopping the cloud PC, always ask the user for confirmation first.** Do not proceed without explicit user approval.

## File size, compression, and ffmpeg (cloud VM)

- **50 MB rule:** Single-file download/upload paths assume roughly **≤ 50 MB** per transfer. Above that, use **`cloud_pc_api.py` automatic chunking** (split on the remote side, download parts, merge locally) or **pre-shrink** the file on the VM (archive, lower bitrate, shorter clip, split into numbered segments).
- **Audio / music outputs:** When generated audio is large or you need to fit under the limit, run **`ffmpeg`** on the cloud PC to re-encode or downmix. Install root (forward slashes for API argv): **`Z:/aicloud/astart/tools/ffmpeg`** — the binary is usually **`Z:/aicloud/astart/tools/ffmpeg/bin/ffmpeg.exe`** (confirm with `dir` on the VM if layout differs; do not assume `ffmpeg` is on `PATH`). Example pattern: lower bitrate AAC/MP3, mono downmix, or trim—only as much as the user’s quality expectations allow.
- **Order of operations:** Prefer **lossless split** (chunk download) when the user needs the exact original bits; prefer **ffmpeg compression** when a smaller derivative is acceptable and faster to pull down.

## Quick Start

Lifecycle and basic remote operations:

```powershell
python3 scripts/cloud_pc_api.py --auto-start-client ping
python3 scripts/cloud_pc_api.py --auto-start-client status
python3 scripts/cloud_pc_api.py --auto-start-client start
python3 scripts/cloud_pc_api.py --auto-start-client --auto-start-cloud ready
# Quick commands (<~10s) — plain exec (blocks until done); pass the remote command as one positional string:
python3 scripts/cloud_pc_api.py --auto-start-cloud exec nvidia-smi
# Long-running jobs (≥~10s) — async-run: POST /api/run on the VM; asynchronous (success ⇒ pid + JSON only, job keeps running).
# For commands with flags (--output_path, --input_params_file, etc.), use --api-json with the full command_parts array:
python3 scripts/cloud_pc_api.py --auto-start-cloud async-run --run-url "http://127.0.0.1:5055/api/run" --api-json '{"command_parts":["Z:\\aicloud\\astart\\AIProject\\ACE-Step-V1.1\\env\\python.exe","Z:\\aicloud\\astart\\AIProject\\ACE-Step-V1.1\\infer.py","--output_path","C:\\Users\\Administrator\\Desktop\\CloudPC","--input_params_file","Z:\\aicloud\\astart\\AIProject\\ACE-Step-V1.1\\examples\\default\\input_params\\output_20250426071706_0_input_params.json"]}'
python3 scripts/cloud_pc_api.py --auto-start-cloud upload --local-path "$env:USERPROFILE\Desktop\file.txt" --remote-path C:/Users/Administrator/Desktop/CloudPC/file.txt
python3 scripts/cloud_pc_api.py --auto-start-cloud download --remote-path C:/Users/Administrator/Desktop/CloudPC/result.txt --local-path "$env:USERPROFILE\Downloads\result.txt"
python3 scripts/cloud_pc_api.py --auto-start-cloud download --remote-path C:/Users/Administrator/Desktop/CloudPC/large.wav --local-path "$env:USERPROFILE\Downloads\large.wav"
```

Warm the session before registry or file work when needed:

```powershell
python3 scripts/cloud_pc_api.py --auto-start-client --auto-start-cloud ready
```

**Async reminder (see also Core Rules §2 and `run_flask_api.py`):** `POST /api/run` returns only acceptance (`ok`, `pid`, `lock_path`; **409** if busy). **You cannot tell if the job crashed or exited non-zero from that response alone.** Poll **`GET /api/lock`** until `running` is false and inspect **`task_failed`** / **`last_exit_code`** (and **`task_error`** when failed). Long or uncertain work must use **`async-run`**, not plain **`exec`** (~10s practical limit on `/api/machine/exec`).

## CLI reference (`scripts/cloud_pc_api.py`)

Authoritative list of flags and subcommands as implemented in `parse_args()` / handlers. Subcommand is **required** (`dest="command"`).

### Global flags (before the subcommand)

| Flag | Effect |
|------|--------|
| `--auto-start-client` | If the localhost client API is unreachable, run `swcloud start` and poll ports **19830–19839** until a `/api/ping` succeeds (up to ~30s). |
| `--auto-start-cloud` | If the cloud is not streaming, call `/api/cloud/start` (long timeout), then **`wait_for_cloud_ready`** until remote `cmd` can run the probe and return the expected marker. |
| `--json` | Prefer printing **raw JSON** from the client API where applicable: `ping`, `status`, `auth`, `start`, `stop`, `ready`, `upload`, `download` use `print_result` with this flag. For **`exec`** and **`async-run`**, when set, prints the full **`/api/machine/exec`** response object as JSON instead of streaming `stdout`/`stderr` to the terminal. |

### Subcommands with no extra arguments

`ping`, `status`, `auth`, `ready`, `start`, `stop` — each invokes the corresponding localhost endpoint (see Quick Start). `ready` still requires the cloud to be streaming (and runs the readiness probe when `--auto-start-cloud` has been used or the cloud is already up).

### `exec`

| Argument | Type / default | Notes |
|----------|----------------|--------|
| `COMMAND_TEXT` | positional | **Required** at runtime. Single remote one-liner passed as `command` to `/api/machine/exec`. |
| `--shell` | `cmd` (default) or `powershell` | |
| `--timeout` | int, default **30** | Remote exec timeout (seconds). Client HTTP timeout is `timeout + 15`. |
| `--detach` | flag | Passed through on the exec payload. |
| `--working-dir` | optional string | Sent as `working_dir` in the JSON body. |

### `async-run`

Builds a PowerShell snippet that **base64-decodes** the POST body and calls **`Invoke-RestMethod -Method Post`** against `--run-url`, then returns compressed JSON from the VM. The outer call is still **`/api/machine/exec`** with `shell: powershell`.

| Argument | Type / default | Notes |
|----------|----------------|--------|
| `--run-url` | string, default `http://127.0.0.1:5055/api/run` | POST target on the **cloud** (forwarded via swcloud). |
| `--command-part` `PART` | repeatable | Each use appends one string to `command_parts` in the POST body. **Mutually exclusive** with `--api-body` and `--api-json` (script raises if combined). |
| `--api-json` | string | Inline JSON for the **entire** POST body (must be valid JSON; validated locally). |
| `--api-body` | path | UTF-8 file whose **full contents** are the POST body (must be valid JSON; validated locally). |
| `--timeout` | int, default **30** | Timeout for the wrapping machine exec (same `+15` client margin as `exec`). |
| `--detach` | flag | On the outer `/api/machine/exec` payload. |
| `--working-dir` | optional string | `working_dir` on the outer exec payload. |

**Body requirement:** exactly one of: at least one `--command-part`, or `--api-json`, or `--api-body`.

### `upload`

| Argument | Required | Notes |
|----------|----------|--------|
| `--local-path` | yes | Local file to send. |
| `--remote-path` | yes | Destination on the cloud PC (prefer forward slashes in docs/examples). |

### `download`

| Argument | Required | Notes |
|----------|----------|--------|
| `--remote-path` | yes | File on the cloud PC. |
| `--local-path` | yes | Where to write locally. |
| `--tail-bytes` | no; default **0** | If **> 0**, requests download of only the **last N bytes** via `/api/machine/download` (no chunk fallback). If **0**, uses **`download_with_chunk_fallback`** (split on VM, fetch parts, merge locally) when the single-shot transfer hits size limits. |

**Audio before download:** Raw or lossless outputs (for example **`.wav`**, **`.flac`**, long **`.mp3`**) are often **too large** for a practical single pull (~50 MB ceiling; chunking is slower). On the **cloud Windows VM**, run **`ffmpeg`** first to **re-encode or downmix** into a smaller file (AAC/MP3, lower bitrate, mono if acceptable), write to a new path, then **`download`** that compressed artifact. Binary and path conventions → **[File size, compression, and ffmpeg (cloud VM)](#file-size-compression-and-ffmpeg-cloud-vm)**.

## Natural-Language Intent Mapping

For the intents below, resolve the app **only** under **`Z:/aicloud/astart/ai-service/skills/`** or **`Q:/skills/`** (always check **both**; nowhere else), open its manifest, read `tasks` / `triggers`, and launch with **`async-run`** and a full POST body (`--api-json` / `--api-body` when argv is non-trivial). Chinese phrasing ("随机创作一首歌", "证件照", etc.) should map to the manifest’s English `triggers` where helpful.

- list available cloud PC apps
- inspect a cloud app or see its arguments
- generate music on the cloud PC
- make an ID photo on the cloud PC
- upscale or interpolate a video on the cloud PC
- any Chinese request asking which cloud AI apps are available
- any Chinese request asking to create music, an ID photo, or a processed video on the cloud PC

## App Registry Workflow

There is no helper CLI for the registry; use `cloud_pc_api.py` to list and read manifests **only** under **`Z:/aicloud/astart/ai-service/skills/`** and **`Q:/skills/`** (see **Cloud skills roots** / **Registry Layout**), then launch and download as needed.

When the user asks what apps are available:

1. Warm the cloud PC if needed: `python3 scripts/cloud_pc_api.py --auto-start-client --auto-start-cloud ready`.
2. **List both roots** with short **Windows** remote commands via `exec` only (for example `dir Z:\aicloud\astart\ai-service\skills` and `dir Q:\skills` in `cmd`, or `Get-ChildItem` on both paths in PowerShell)—no `async-run` needed for directory listing.
3. Return directory name, which root (`Z:` vs `Q:`), manifest file used (`app.json` vs `<app>.json`), and short description from JSON when useful.

When the user asks to inspect one app:

1. Warm the cloud PC if needed, then locate the app under **`Z:/aicloud/astart/ai-service/skills/<app-dir>/`** or **`Q:/skills/<app-dir>/`** and read the manifest with **`exec`** and **`cmd`** `type` or **`powershell`** `Get-Content` (Windows-only; avoid extra helpers unless unavoidable).
2. Return required fields, optional fields, `command`, `work_dir`, and `tasks` / `triggers` if present.

When the user asks for an outcome (for example generating music):

1. Resolve the app folder and manifest **only** under **`Z:/aicloud/astart/ai-service/skills/...`** or **`Q:/skills/...`** (check **both** roots; if missing in both, stop—do not search other paths); confirm arguments with the user—do not guess required inputs.
2. Build the full argv for the app (or a single shell line if the manifest uses `command` as a string) and start it with `async-run` and `--run-url` pointing at `/api/run`, using `--api-json` with a `command_parts` array.
3. After `async-run`, **poll `GET /api/lock`** until the task is no longer `running`; if **`task_failed`** is true or **`last_exit_code`** ≠ 0, treat the run as failed—do not assume success from `POST /api/run` alone.
4. When outputs exist on known paths, download with `python3 scripts/cloud_pc_api.py --auto-start-cloud download --remote-path ... --local-path ...` and give the user the local paths.
5. For deep diagnostics (Python tracebacks, stderr), the child inherits the Flask console on the VM unless the app redirects logs—**`/api/lock` still surfaces process exit status**; use app log files or VM console when you need full text.

## Registry Layout

**Same exhaustive pair as [Cloud skills roots](#cloud-skills-roots-authoritative)—no other VM paths define skills.** On-disk parent folder name is **`skills`** (plural); each child directory is typically one **skill** app unless it is a support directory (see below). Optional local mirror `cloud-pc/.cache/registry/index.json` is **not** a third discovery root on the VM; refresh by listing the two paths on the cloud PC.

- **`Z:/aicloud/astart/ai-service/skills/`** — platform / bundled apps.
- **`Q:/skills/`** — user-customized apps; same per-folder layout and manifest rules as on `Z:`.

Manifest discovery order (within each skill directory under either root):

1. `app.json`
2. `<app-name>.json`
3. first `*.json` file in the app directory

Ignore support directories such as:

- `assets/`
- `scripts/`
- `references/`
- `agents/`

## Adding Or Fixing Apps

When the user adds a new cloud app:

1. Put **user-defined** skills under **`Q:/skills/<app-dir>/`**. Use **`Z:/aicloud/astart/ai-service/skills/`** only when extending the platform tree (same conventions as today).
2. Add an app manifest, preferably `app.json`.
3. Define at least `name`, `description`, `work_dir`, and `command`.
4. Add `tasks` with `triggers` when the app supports multiple operations or natural-language routing.
5. Verify discoverability by listing **`Q:/skills/`** and **`Z:/aicloud/astart/ai-service/skills/`** on the cloud PC and opening the new manifest remotely—**only** these two roots.

When an app fails unexpectedly:

1. Inspect the manifest first.
2. Verify executable paths inside `command`.
3. Prefer absolute executable paths when relative paths are unreliable.
4. If the app is long-running, redirect logs and inspect the output file or log tail.

## Cloud VM HTTP runner (`scripts/run_flask_api.py`)

The Flask app defined in `run_flask_api.py` listens on the **cloud Windows machine** (default `127.0.0.1:5055`, overridable via `RUN_API_HOST` / `RUN_API_PORT`). Start it on the VM from `cloud-pc/scripts` (see file docstring: `pip install -r requirements-run-api.txt`, then `python run_flask_api.py`).

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/health` | Liveness |
| GET | `/api/lock` | **Required for async observability:** `locked`, `pid`, `running`; after the child exits, **`task_finished`**, **`last_exit_code`**, **`task_failed`**, **`task_error`** (non-zero exit). Without polling this, API callers do not see failure. |
| POST | `/api/run` | Start one detached task; body JSON (use for any work that may exceed ~10s—avoids synchronous `/api/machine/exec` timeouts) |

**Why this exists:** `/api/run` spawns the workload in the background and returns a `pid`. The normal remote shell path (`/api/machine/exec` via plain `exec`) blocks until the command exits and hits client timeouts; **treat ~10 seconds as the practical ceiling** for that path.

**Why `/api/lock` matters:** `POST /api/run` does **not** stream stderr or return Python tracebacks to the client. Detached children attach stdout/stderr to the **Flask server process** on the VM. The **only** structured signal on the HTTP API for “did it fail?” is **`GET /api/lock`** after the process exits (non-zero → **`task_failed`**: true, **`task_error`** set). Poll it on an interval until `running` is false (or handle **`pid_check_error`** if present).

**POST `/api/run` body (JSON):**

- `command_parts`: non-empty array of argv strings (preferred; joined for Windows with `subprocess.list2cmdline`).
- or `command`: single string to run in a shell.
- optional `cwd`: working directory string.

**Responses:** On success, HTTP 200 and JSON including `ok: true`, `pid`, `lock_path`. If a previous task's PID is still running, HTTP **409** with `busy`, `conflict_pid`. Malformed body → **400**. The child's stdout/stderr attach to the Flask process console on the VM—**not** to your `async-run` response.

**From the agent's machine:** Do not call `5055` directly unless you are on the VM. Use `python3 scripts/cloud_pc_api.py ... async-run --run-url "http://127.0.0.1:5055/api/run" ...` so the local swcloud client forwards execution to the cloud and the POST runs there. Then **poll lock status** by running a second remote step (e.g. `exec` with `Invoke-RestMethod -Uri 'http://127.0.0.1:5055/api/lock'` or equivalent) until you see a terminal state with **`task_failed`** / **`last_exit_code`** as needed—`async-run` only wraps the **POST**, not lock polling.

## Notes

- **Cross-references:** Remote OS and command style → opening **Remote environment** + **Core Rules** §2–§5; **local vs cloud file handling** → **Remote environment** (Local files paragraph); VM skill roots (**only** `Z:/aicloud/astart/ai-service/skills/` + `Q:/skills/`) and manifest order → **Cloud skills roots** + **Registry Layout**; localhost flags and subcommands → **CLI reference**; `exec` vs `async-run` and ~10s rule → **Core Rules** §2; large downloads → **Core Rules** §11 and **File size, compression, and ffmpeg**.
- Deliverables: if the user needs an artifact from the cloud PC, **download** it and give the **local path**—do not leave it only on the VM.
