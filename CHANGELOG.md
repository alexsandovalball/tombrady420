# Changelog

All notable changes to the Telegram Media Catalog Bot are documented in this file.

## [Latest] - 2026-01-31

### Added
- **View Catalog Button**: Added "📋 View Catalog" button to notification messages that remains visible after subscribe/unsubscribe actions
- **Resubscribe Button**: Added "🔔 Resubscribe to Notifications" button for users who previously unsubscribed
- **Admin Confirmation Prompts**: Added "Are you sure?" confirmation prompts for:
  - Block User action
  - Disable Notifications (toggle) action
- **Non-blocking Notification Processing**: Notifications now process in background without blocking user interactions
- **Intelligent Back Navigation**: Products viewed from notifications now correctly return to the product's category
- **Channel Scanning Utility**: Added `scan_channel.py` for importing existing channel messages after database loss

### Changed
- **Notification Message Layout**: Updated to include View Catalog button alongside View Product and Unsubscribe buttons
- **Subscribe/Unsubscribe Buttons**: Now persist after toggling, allowing easy re-subscription
- **Stagger Interval**: Reduced notification group delay from 30s to 10s for faster delivery
- **Media Group Processing**: Reduced delay from 1.5s to 0.5s for better responsiveness

### Fixed
- **Performance**: Bot no longer freezes during notification delivery (reduced from 68s to 22s for 23 users)
- **Navigation**: Back button from notification-viewed products now redirects to correct category
- **Unsubscribe Flow**: Buttons no longer disappear after unsubscribing, making it easier to resubscribe
- **Notification Filtering**: Excluded categories (DATEDPROOFS, CLIENTTOUCHDOWNS) no longer trigger user notifications

## Previous Changes

### Database & Persistence
- Removed automatic ignore list additions on product deletion
- Products can now be re-uploaded after deletion
- Added comprehensive database persistence documentation
- Support for PostgreSQL migration

### Notification System
- Category filtering for excluded categories
- Rate limiting to prevent spam
- Staggered delivery for large user bases
- Anti-spam policies compliance

### Category & Subcategory System
- Hierarchical category/subcategory support
- Organized product browsing
- Intelligent pagination
- Category-based filtering

### Admin Features
- User management interface
- Notification control per user
- Block/unblock functionality
- Product recategorization
- Custom messaging and broadcasts
- Statistics dashboard

### Search & Navigation
- Fuzzy search with typo tolerance
- Natural language search
- Smart pagination
- Back button navigation across all contexts

## Notes

- All changes maintain backward compatibility
- No database migrations required for updates
- Security scans show 0 vulnerabilities
- Comprehensive test coverage for all new features
