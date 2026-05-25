# Media Streaming Hardening Summary

## Root Cause

Rapid catalog navigation and normal browser `206 Partial Content` requests could trigger repeated Telegram downloads because reusable temp media was cleaned too quickly. Emergency cleanup also treated fresh reusable media like orphaned temp files, so short navigation bursts regenerated the same video repeatedly.

## Fixes Implemented

- Added a short-lived reusable temp media layer for catalog media.
- Added per-media async generation locks so concurrent requests share one generated temp file.
- Refresh last-access timestamps on every cache reuse and stream completion.
- Track active stream readers and skip cleanup for files currently being streamed.
- Added TTL and size-based media temp eviction instead of immediate deletion.
- Added adaptive emergency cleanup cooldown and high/low watermark behavior.
- Added structured logs for cache hits, misses, reuse, expiration, generation, active streams, and cleanup skip decisions.
- Improved webhook 401 diagnostics while preserving strict secret validation.

## Operational Recommendations

- Keep video media below the existing streaming size limit where possible.
- Monitor `[media_cache] generation_started` versus `cache_reused` to confirm reduced regeneration.
- Monitor `[WEBHOOK_401] reason=...` to distinguish missing headers from incorrect secrets.

## Suggested Production Defaults

- `MEDIA_CACHE_ENABLED=true`
- `MEDIA_CACHE_TTL_SECONDS=300`
- `MEDIA_CACHE_CLEANUP_INTERVAL_SECONDS=60`
- `MEDIA_CACHE_MAX_SIZE_MB=250`
- `EMERGENCY_CLEANUP_COOLDOWN_SECONDS=120`
