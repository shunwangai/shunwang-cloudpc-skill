Initial release of the shunwang-cloudpc skill.

- Supports remote operations on cloud Windows PCs via the local swcloud client.
- Allows remote skill discovery and launching only under Z:/aicloud/astart/ai-service/skills/ and Q:/skills/ directories on the VM.
- Provides lifecycle management, file transfer, remote execution (cmd/powershell), and async job control using cloud_pc_api.py scripts.
- Enforces correct usage of exec (for short queries) and async-run (for jobs expected to run longer than ~10 seconds).
- Specifies robust download behavior and size limits (~50 MB per transfer).
- Clarifies Windows shell syntax and platform differences for remote operations.