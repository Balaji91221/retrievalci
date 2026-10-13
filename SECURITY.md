# Security policy

This is a local developer tool, not a multi-tenant sandbox. Python adapters are
trusted executable code. Subprocess deadlines contain hangs, not malicious code.
Only install adapters you trust. On POSIX a timeout terminates the worker process
group; on Windows it terminates the worker, not arbitrary child process trees.

Snapshots and source-diff runs can contain the full corpus. Protect artifact and
checkpoint directories according to that data's access/retention policy. The
tool never uploads corpus text itself. An explicitly configured HTTP provider
receives query text, k, and snapshot ID. Provider code and custom adapters may
perform their own I/O. Use sanitized test data where appropriate.

HTTP integration verifies TLS for HTTPS, disallows embedded URL credentials and
query strings, ignores ambient proxy settings, refuses redirects, bounds response
size, and reads bearer tokens from an explicit environment variable. Do not put
secrets in source IDs, query text, provider URLs/versions, or adapter exception
messages. Error messages are retained as diagnostic evidence.

Hashes detect accidental modification; they are not authentication signatures.
Someone who can replace artifacts and their identifiers controls those inputs.
Directory ingestion rejects symlinks and accepts only bounded UTF-8 .md/.txt files.
Reports escape source content and contain no remote scripts or network requests.

## Reporting a vulnerability

Use [GitHub private vulnerability reporting](https://github.com/Balaji91221/retrievalci/security/advisories/new) to contact the maintainers. Include the affected version, a sanitized
reproduction, and the expected and observed behavior. Do not post secrets,
credentials, or sensitive exploit details in public issues.

Security fixes target the latest development release (currently 0.2.0).
Older releases have no maintenance guarantee. No independent security
audit or response-time guarantee is claimed.
