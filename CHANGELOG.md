# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

### Fixed - Notification System

#### Critical Fixes
- **Mandatory Unique Index**: Changed unique index creation from warning-only to fail-fast. The system now raises `RuntimeError` if the critical unique index `idx_notification_queue_user_product_unsent` cannot be created, preventing duplicate notifications from being queued.
  
- **Pre-check Validation**: Added pre-check query in `queue_notification()` to detect existing notifications before attempting to insert, reducing unnecessary database operations and providing early duplicate detection.

- **Transaction Safety**: Fixed transaction handling to commit only on successful operations and rollback on errors, preventing corrupted data from being persisted.

#### Improvements
- **Retry Logic**: Implemented retry mechanism (3 attempts with linear backoff: 0.5s, 1.0s, 1.5s) in `_remove_duplicate_notification()` to handle transient database errors gracefully.

- **Enhanced Logging**: Added comprehensive debug logging throughout the deduplication flow:
  - Log each duplicate detected with user_id, product_id, and notification_id
  - Track and report removal failures separately
  - Success/failure statistics after batch deduplication
  - Notification ID tracking for better traceability

#### Testing
- Added comprehensive test suite with 8 passing tests:
  - 6 tests for deduplication mechanisms
  - 2 tests for error handling
  - All tests validate database integrity and duplicate prevention

### Security
- ✅ CodeQL security scan passed with 0 alerts
- No security vulnerabilities introduced

### Files Changed
- `database.py`: Critical index creation and enhanced queue_notification
- `utils/notifications.py`: Retry logic and better deduplication tracking
- `tests/test_notification_deduplication.py`: Comprehensive test suite

### Impact
- Prevents duplicate notifications from being queued
- Improves database integrity
- Better error recovery for transient failures
- Clear visibility into deduplication behavior via logging
- Guaranteed notification system reliability
