# iMessage Attachment Dedupe

Reclaim disk space from duplicate iMessage attachments on macOS — safely.

## The problem

iMessage stores every attachment under `~/Library/Messages/Attachments`. The same
photo or video sent in multiple chats (or sent more than once) is saved as a
separate full copy each time. Over the years, this quietly eats gigabytes.

## How it works

1. Walks the Attachments folder and hashes every file (SHA-256; files with a
   unique size are skipped, so only plausible duplicates are hashed).
2. Finds byte-identical duplicates.
3. Collapses each duplicate set with **hard links**: every message keeps
   working — each path still opens the file — but all copies share a single
   copy's disk space.

What it deliberately does **not** do:

- Never deletes a file.
- Never modifies `chat.db` (your message database).
- Refuses to run while the Messages app is open.
- Re-verifies each file is unchanged between hashing and linking, and skips
  anything that changed mid-run.

## Requirements

- macOS, Python 3 (preinstalled — no dependencies to install)

**To collapse duplicates in place**, macOS requires two one-time steps:

1. Quit the Messages app.
2. Give Terminal Full Disk Access: System Settings → Privacy & Security →
   Full Disk Access → turn on Terminal, then quit and reopen Terminal.

Apple offers no narrower permission for the Messages folder — not even `sudo`
bypasses this. You can revoke Full Disk Access when you're done.

## Usage

```sh
# Report only — always start here. Changes nothing.
python3 imessage_dedupe.py --dry-run

# Collapse duplicates. Deletes nothing, moves nothing to Trash.
python3 imessage_dedupe.py
```

The dry run reports the number of duplicate groups, how many are photos/videos,
and the estimated space reclaimable (both logical and on-disk).

## Limitations

- This is maintenance, not prevention: new duplicates accumulate as new
  messages arrive. Re-run every few months.
- With iCloud Messages' optimize-storage, an evicted attachment that gets
  re-downloaded can come back as a fresh separate copy.
- It dedupes the Messages attachment store only — not your Photos library
  (macOS Ventura and later already finds duplicates there, under
  Photos → Duplicates).

## FAQ

**Will this break my message threads?**
No. Every attachment path keeps working; the files just share disk space
behind the scenes.

**Why not just delete the duplicates?**
Each message references its own copy. Deleting would leave broken attachments
in old conversations.

**How much space will I get back?**
Run `--dry-run` — it tells you before changing anything.

## License

MIT — see [LICENSE](LICENSE).
