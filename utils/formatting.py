# utils/formatting.py
from __future__ import annotations
import re
from typing import Optional

_MD_V2_SPECIAL = r'_*`\[\]()~>#+-=|{}.!'

def escape_markdown_v2(text: Optional[str]) -> str:
    if not text:
        return ""
    return re.sub(r'([{}])'.format(re.escape(_MD_V2_SPECIAL)), r'\\\1', text)