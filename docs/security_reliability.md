# Step 13 — Security and reliability

Implemented safeguards are distinct from deployment acceptance. No local setup, historical audit, backup restore, forward campaign, safety unlock or real-money execution is implied. Follow the consolidated setup at the end of implementation.

## Request and execution boundaries

Both API entry points install mutation authentication. Registered write routes require the caller's `x-ops-key`; only the three existing stateless Module A, position-size and regime calculators are exempt. Missing server keys fail closed. Configure a random private `OPS_API_KEY` of at least 32 characters (maximum 256), outside source control. Each operator supplies their own request key; the web server does not lend visitors a stored key. Rotate by replacing the server environment secret and restarting API instances, then distribute through the existing private channel. Never put it in `NEXT_PUBLIC_*`, URLs, screenshots or logs.

Read-only dashboards remain accessible to anyone who can reach the service. Restrict network access when portfolio data is private. This phase does not introduce user accounts, per-user roles, a penetration test or a dependency-vulnerability certification.

Mutation bodies are bounded to 1 MiB and 15 seconds in the API and operator proxy. Errors omit raw SQL, connection strings and validation input. Responses have generated request IDs and defensive headers. The frontend CSP restricts frames, objects, base URLs and form destinations; it is not a complete script/nonce CSP. Serve remote operator connections over HTTPS. The server refuses to forward keys over remote HTTP unless `PMS_ALLOW_PRIVATE_HTTP=1` is explicitly configured for a reviewed private network. Loopback development remains supported.

Mode changes disable new orders and preserve existing stops and locks. Enabling orders uses a conditional database update; safety state and its audit entry commit together. Readiness controls mode promotion. Sandbox orders require idempotency keys and reject changed replay payloads. Quantities, prices and identifiers are validated before execution. Approval/session locks serialize capital reservations and approvals; cumulative observations cannot exceed approved quantities. These are evidence-recording safeguards: the real broker adapter remains disabled.

Reconciliation is scoped to PAPER/PAPER_SANDBOX and checks order identities/status, positions and cash. Mismatches or failures lock execution. Internal paper consistency is not independent live-broker evidence.

## Availability and workers

`/health` reports liveness; `/ready` probes the database and returns a safe 503 when unavailable. Neither grants trading permission. Online API/worker connections use small bounded pools, connection/lock/statement timeouts and pre-ping. Remote connections require TLS; `sslmode=require` provides encryption, while `verify-full` with the provider's trusted CA should be configured for hostname verification. Use dedicated least-privilege application roles, reviewed schema grants and a separate migration role. The read-only posture command reports effective anon/authenticated access without changing grants or RLS.

Celery accepts JSON only, prefetches one task and has bounded startup retries. Soft/hard task limits are 1800/1860 seconds. Only the durable daily task uses late acknowledgement and automatic transient database retries (three retries, fixed session date); other jobs require operator review after failure. Data/configuration blockers are not blindly retried. Use authenticated TLS Redis for remote brokers, restrict its network access and protect result storage. JSON serialization alone does not secure broker access.

The Operations page and `/v1/execution/reliability` show posture and recent worker journals. `OBSERVED` means recent bounded evidence, not a heartbeat, complete schedule coverage or execution readiness. Review FAILED/PARTIAL jobs and RUNNING jobs older than 32 minutes. After a crash, confirm no worker still owns the run, inspect its durable journal and reconcile partial paper effects before retrying the supported current session. Do not delete journals, force old-session fills, reset locks or promote modes to bypass a blocker.

Deferred read-only commands:

```powershell
python scripts/check_security_reliability.py --plan
python scripts/check_security_reliability.py --check
python scripts/verify_pms_backup.py --plan
```

The posture check exits 2 for missing keys, exposed grants or worker evidence requiring attention. A zero exit is limited to implemented runtime checks; backup evidence and full acceptance remain separate.

## Recovery rehearsal

1. Quiesce writes or coordinate a consistent database snapshot. Save the finished checksummed source-integrity report for exactly that snapshot and scope.
2. Produce a trusted PostgreSQL custom-format archive, preserving its SHA256 separately. Include all dependencies: `pms.financial_filings` and `pms.financial_facts` are views over external source tables, so a `pms`-only dump is insufficient. Review required extensions and roles. Encrypt backups, restrict access and test retention.
3. Inspect the archive with `--inspect --archive PATH --sha256 HASH`. Inspection verifies checksum and table of contents, not recovery.
4. Provision a separate empty disposable database and set `PMS_RESTORE_TEST_URL` to it. Never use the populated source or an application database. Then run `--restore-check --archive PATH --sha256 HASH --source-audit REPORT.json`. Only use your trusted archive: PostgreSQL restores can execute archive SQL.
5. The tool rejects the same endpoint and nonempty targets, restores transactionally without `--clean`, excludes ownership/grant restoration, and compares restored data with the original source manifest. Credentials are not included in subprocess arguments or error output. Failed targets require investigation/disposal; the tool does not overwrite them on retry.
6. Even `RESTORE_MANIFEST_MATCHED` is not full acceptance. Independently validate roles/grants, secrets, deployment configuration, application behavior and restore duration. Agree recovery point/time objectives and measure them. Preserve rehearsal evidence off the source database.

No backup or restore was performed during implementation. Catalog grant checks and security-advisor output are point-in-time observations, not a security certificate. No production settings or database permissions were changed.
