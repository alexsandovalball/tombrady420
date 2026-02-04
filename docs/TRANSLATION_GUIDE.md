# Multi-Language Translation Feature

## Overview
This document describes the multi-language translation feature added to the TOM BRADY MEDIA BOT.

## Supported Languages
The bot now supports automatic translation to the following languages:
- 🇬🇧 English (default)
- 🇩🇪 German
- 🇳🇱 Dutch (used for both Dutch and Belgian users)
- 🇮🇹 Italian
- 🇪🇸 Spanish
- 🇫🇷 French

## How It Works

### User Language Selection
1. Users can change their preferred language using the `/language` command
2. The bot displays a menu with all available languages
3. The current language is marked with a checkmark (✓)
4. Once selected, the user's preference is saved in the database
5. All future bot messages will be automatically translated to the selected language

### Translation Service
- Uses the `deep-translator` library with Google Translate API
- Translations are cached to improve performance
- Graceful fallback to English if translation fails
- On-the-fly translation of all user-facing messages

### Database Schema
A new `language` column has been added to the `bot_users` table:
```sql
ALTER TABLE bot_users ADD COLUMN language TEXT DEFAULT 'en';
```

## Technical Implementation

### File Structure
```
translations/
  ├── __init__.py
  ├── language_config.py    # Language codes and display names
  ├── strings.py             # All translatable strings
  └── translator.py          # Translation service
```

### Key Components

#### 1. Language Configuration (`language_config.py`)
- Defines supported languages
- Maps language codes to display names
- Provides validation functions

#### 2. Translatable Strings (`strings.py`)
- Centralized storage of all user-facing text
- Supports string formatting (e.g., {name}, {count})
- Makes ORDER_CONTACT configurable via environment variable

#### 3. Translation Service (`translator.py`)
- `TranslationService` class handles all translation
- Implements caching for better performance
- Error handling with fallback to original text
- Helper functions: `get_translated_string()`, `translate_text()`

#### 4. Language Handler (`handlers/language.py`)
- Implements `/language` command
- Handles language selection callbacks
- Updates database with user preferences
- Provides translated confirmation messages

### Database Methods
New methods added to `Database` class:
- `get_user_language(user_id)` - Retrieves user's language preference
- `set_user_language(user_id, language)` - Updates user's language preference

## Usage Example

```python
from translations.translator import get_translated_string
from database import Database

db = Database()

# Get user's language preference
user_lang = await db.get_user_language(user_id)

# Get translated string
welcome_message = get_translated_string(
    "welcome", 
    user_lang, 
    name=user.first_name
)

# Send translated message
await update.message.reply_text(welcome_message)
```

## Configuration

### Environment Variables
Add to your `.env` file:
```env
# Optional: Customize order contact (default: @FLYAWAYPEP)
ORDER_CONTACT=@YourContactHandle
```

### Dependencies
Added to `requirements.txt`:
```
deep-translator>=1.11.0
```

## Migration Notes

### Existing Users
- Existing users will default to English (en)
- Users can change their language preference at any time using `/language`
- Language preference is stored per user in the database

### Database Migration
The database schema will be automatically updated when the bot starts:
- New `language` column added with default value 'en'
- Existing user records will have language set to 'en'

## Best Practices

### Adding New Strings
1. Add the string to `translations/strings.py` in the `STRINGS` dictionary
2. Use descriptive keys (e.g., `"welcome"`, `"error_occurred"`)
3. Support formatting with `{variable_name}` placeholders
4. Test translation with multiple languages

### Error Handling
The translation service includes robust error handling:
- Network errors: Falls back to original text
- Invalid language codes: Uses default language (English)
- Translation API failures: Returns original text with logged error

### Performance
- Translations are cached to reduce API calls
- Cache can be cleared with `translation_service.clear_cache()`
- Consider implementing persistent caching for production

## Limitations

1. **Internet Requirement**: Translation requires internet connectivity
2. **Translation Quality**: Depends on Google Translate API quality
3. **Rate Limiting**: Free tier has usage limits
4. **No Offline Mode**: Translations won't work without internet

## Future Enhancements

Potential improvements for future versions:
1. Add more languages (Portuguese, Russian, Chinese, etc.)
2. Implement persistent translation cache
3. Add user location auto-detection
4. Support custom translations for specific terms
5. Implement translation quality feedback
6. Add admin panel for managing translations
7. Support right-to-left languages (Arabic, Hebrew)

## Troubleshooting

### Translation Not Working
1. Check internet connectivity
2. Verify `deep-translator` is installed
3. Check logs for translation errors
4. Verify language code is valid

### Default Language Not English
1. Check database user language field
2. Verify `DEFAULT_LANGUAGE` in `language_config.py`
3. Check environment variable configuration

### Strings Not Translating
1. Ensure string is in `STRINGS` dictionary
2. Verify handler is using `get_translated_string()`
3. Check user's language preference in database

## Support
For issues or questions about the translation feature:
1. Check the logs for error messages
2. Verify configuration in `.env` file
3. Test with `/language` command
4. Review this documentation
