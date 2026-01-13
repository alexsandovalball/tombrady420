import io, os, sys
p = "handlers/product_view.py"
bak = p + ".bak"
if not os.path.exists(p):
    print("ERROR: handlers/product_view.py not found")
    sys.exit(1)
# read file
with io.open(p, "r", encoding="utf-8") as f:
    s = f.read()
# make a backup if not already present
if not os.path.exists(bak):
    with io.open(bak, "w", encoding="utf-8") as f:
        f.write(s)
    print("Backup written to", bak)
# Insert debug after fallback single-item assignment
s = s.replace(
    "media_items = [p]",
    "media_items = [p]\nlogger.debug(\"Fetched %d rows for media_group=%s\", len(media_items), media_group_id)"
)
# Insert debug after dedupe call
s = s.replace(
    "media_items = _dedupe_media_items(media_items)",
    "media_items = _dedupe_media_items(media_items)\nlogger.debug(\"After dedupe: %d items: %s\", len(media_items), [mi.get('file_unique_id') or mi.get('file_id') for mi in media_items])"
)
# Insert debug before send_media_group if present
if "msgs = await context.bot.send_media_group(chat_id=dest_chat, media=input_media)" in s:
    s = s.replace(
        "msgs = await context.bot.send_media_group(chat_id=dest_chat, media=input_media)",
        "logger.debug(\"Prepared input_media count=%d types=%s\", len(input_media), [type(im).__name__ for im in input_media])\nmsgs = await context.bot.send_media_group(chat_id=dest_chat, media=input_media)"
    )
else:
    print("Note: send_media_group call not found; Prepared input_media debug not inserted.")
# write back
with io.open(p, "w", encoding="utf-8") as f:
    f.write(s)
print("Patched", p)
