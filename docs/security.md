# Security notes

| Area | What we do | Known limits |
|---|---|---|
| Passwords | argon2id (`argon2-cffi` defaults); login does a dummy verify for unknown emails so timing doesn't reveal accounts | no lockout/2FA (rate limiting only) |
| Tokens | HS256 JWT, 15 min, algorithm pinned on decode, `exp`/`sub` required, `typ=access` | no refresh tokens or revocation list: see `production-fastapi` |
| API keys | 256-bit random secret, stored as SHA-256, prefix lookup, constant-time compare, scopes ⊆ creator's permissions, revocable, `last_used_at` | no expiry dates yet |
| Authorization | service-layer `@requires` + role table; non-members get 404 | row-level security not used (single DB role) |
| Input | Pydantic with `extra="forbid"`; body limit 1 MB; parameterised SQL only (ORM) | |
| Rate limiting | token bucket per credential/IP, atomic Lua, Redis clock | IP-based limits are weak behind shared NATs; limiter fails open if Redis is disabled |
| Webhooks | HMAC-SHA256 over `timestamp.body`, receivers reject > 5 min old (replay); secrets Fernet-encrypted at rest; SSRF guard (https only, resolve + reject private/loopback/link-local/reserved) at save **and** send time | DNS rebinding between our check and httpx's connect is still possible: a full fix pins the checked IP (custom transport) or routes through an egress proxy |
| Audit | every mutation writes `audit_logs` in the same transaction with actor + request id | audit rows are mutable by DB admins (no WORM storage) |
| Secrets | settings from env (`FLOWFORGE_*`); dev defaults exist **only** for local use | production must set `JWT_SECRET` and `SECRET_ENCRYPTION_KEY` |
| Container | non-root user, no build tools in runtime image, healthcheck | image not signed/scanned in CI yet |
