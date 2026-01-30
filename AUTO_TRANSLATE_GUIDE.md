# Auto-Translate Feature Guide

## Overview

The bot now includes automatic translation capabilities, allowing users to view product captions in their preferred language.

**Note**: Currently, only product captions are translated. Bot UI elements (menus, buttons, system messages) remain in English for consistency and performance.

## Features

### Supported Languages

The bot supports translation to the following languages:

- 🇬🇧 English (default)
- 🇪🇸 Spanish
- 🇫🇷 French
- 🇩🇪 German
- 🇮🇹 Italian
- 🇵🇹 Portuguese
- 🇷🇺 Russian
- 🇨🇳 Chinese (Simplified)
- 🇯🇵 Japanese
- 🇰🇷 Korean
- 🇸🇦 Arabic
- 🇮🇳 Hindi

### How It Works

1. **Language Selection**: Users can select their preferred language using the `/language` command or the language button in the start menu
2. **Automatic Translation**: Once a language is selected, all product captions are automatically translated
3. **Persistent Preference**: Language preference is stored in the database and persists across sessions
4. **Graceful Fallback**: If translation fails (e.g., network issues), the original text is displayed

## User Commands

### `/language`
Opens the language selection menu where users can:
- View their current language setting
- Select a new preferred language
- See all available language options

### `/start`
The start menu now includes a "🌐 Language Settings" button for quick access to language preferences.

## Implementation Details

### Architecture

The translation feature consists of several components:

1. **Translation Utility** (`utils/translation.py`)
   - Provides `translate_text()` function for translating text
   - Uses Google Translate API via the `deep-translator` library
   - Handles errors gracefully and returns None on failure

2. **Language Handler** (`handlers/language.py`)
   - Manages language selection UI
   - Handles user language preference updates
   - Provides translation helper for product captions
   - Validates language codes for security

3. **Database Schema**
   - Added `preferred_language` column to `bot_users` table
   - Defaults to 'en' (English) for new users
   - Methods: `get_user_language()`, `set_user_language()`

4. **Product View Integration**
   - Product captions are automatically translated when displayed
   - Translation note appended: "🌐 [Auto-translated from original]"

### Translation Library

The implementation uses [`deep-translator`](https://github.com/nidhaloff/deep-translator), which:
- Provides a simple, reliable interface to Google Translate
- Supports auto-detection of source language
- Has no API key requirement for basic usage
- Handles rate limiting gracefully

### Error Handling

The translation feature is designed to fail gracefully:
- If translation fails, the original text is shown
- Network errors are logged but don't prevent bot operation
- Users are not notified of translation failures (seamless experience)

## Testing

### Unit Tests

Run the translation tests:
```bash
python test_translation.py
```

Tests cover:
- Basic translation functionality
- Language support validation
- Database language preference storage
- Edge cases (empty strings, None values)
- Offline/network error handling

### Manual Testing

1. Start the bot and send `/language`
2. Select a language (e.g., Spanish)
3. Browse products - captions should be translated
4. Change language and verify translations update
5. Set language back to English - no translation should occur

## Configuration

No additional configuration is required. The feature works out of the box once the `deep-translator` dependency is installed.

### Requirements

Add to `requirements.txt`:
```
deep-translator>=1.11.4
```

Install with:
```bash
pip install deep-translator>=1.11.4
```

## Limitations

1. **Translation Quality**: Depends on Google Translate API quality
2. **Rate Limiting**: Heavy usage may encounter rate limits (handled gracefully)
3. **Character Limits**: Very long captions may hit translation API limits
4. **Network Dependency**: Requires internet access to translation service
5. **Source Language**: Auto-detection may not always be accurate

## Future Enhancements

Potential improvements for the translation feature:

- [ ] Add language auto-detection based on user's Telegram language
- [ ] Cache translations to reduce API calls and improve performance
- [ ] Support for custom translation services (e.g., DeepL, AWS Translate)
- [ ] Translation of UI elements (buttons, menu text, system messages)
- [ ] User-configurable translation preferences (show original, disable for specific categories)
- [ ] Translation quality feedback mechanism
- [ ] Reuse translator instances for better performance

## Troubleshooting

### Translation Not Working

1. **Check Network Connectivity**: Ensure the bot server can access translate.google.com
2. **Verify Dependencies**: Ensure `deep-translator` is installed correctly
3. **Check Logs**: Look for translation errors in bot logs
4. **Test Manually**: Use the test script to verify translation functionality

### Original Text Still Showing

- Language preference defaults to English ('en')
- Verify language is set using `/language` command
- Check database for user's language preference
- English text is not translated (source and target are the same)

### Database Errors

If you encounter errors related to the `preferred_language` column:
- The database migration adds this column automatically
- If using an existing database, restart the bot to apply migrations
- Check that the bot has write permissions to the database file

## Security Considerations

- **No API Keys Stored**: Free Google Translate API requires no authentication
- **Input Sanitization**: Text is passed directly to translation API (Google handles sanitization)
- **User Privacy**: Language preferences are stored locally in the database
- **No External Data**: Original captions are not sent to third parties (only translation service)

## Performance

- **Translation Speed**: Typically 100-300ms per translation
- **Caching**: Not currently implemented (each view translates anew)
- **Database Impact**: Minimal (single query per user interaction)
- **Network Usage**: One API call per product view (when not English)

## API Usage

The bot uses Google Translate's free tier which:
- Has no hard limits for basic usage
- May rate limit aggressive usage
- Does not require API key setup
- Is suitable for small to medium bot usage

For high-volume deployments, consider:
- Implementing translation caching
- Using Google Cloud Translation API with authentication
- Implementing rate limiting on the bot side
