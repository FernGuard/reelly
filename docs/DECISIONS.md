# Decision Log

Public architectural decisions only. Organization-specific evidence, budgets, accounts, and campaign results belong outside this repository.

| Decision | Rationale |
|---|---|
| Reelly never publishes automatically | A human reviews and publishes every output. |
| Local-first media processing | Source media and deterministic processing stay local when possible; cloud use is explicit. |
| Bring your own API keys | Reelly ships no credentials and names the key required for each cloud capability. |
| Rendered MP4 plus editor handoff | Users get an immediate output and a path for manual refinement. |
| Versioned editing rules | Decisions should be inspectable and reproducible. |
| MIT license | Users may use, modify, and redistribute the software. |
| Cross-platform where practical | Tools resolve from environment variables and PATH; Apple-only acceleration remains optional. |
