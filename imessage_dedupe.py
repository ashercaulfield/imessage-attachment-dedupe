#!/usr/bin/env python3
"""
imessage_dedupe.py -- reclaim disk space from duplicate iMessage attachments.

iMessage stores every attachment under ~/Library/Messages/Attachments, and the
same photo/video sent in multiple chats (or sent more than once) is stored as
a separate full copy each time.

This script finds byte-identical duplicates and collapses them with hard links:
every message keeps working (each path still opens the file), but all copies
share a single copy's disk space. Nothing is deleted and chat.db is never
modified, so message threads are unaffected.

Usage:
    python3 imessage_dedupe.py --dry-run   # report only, change nothing
    python3 imessage_dedupe.py             # collapse duplicates

    # Check for duplicates WITHOUT Full Disk Access: copy the Attachments
    # folder in Finder to somewhere accessible (e.g. Desktop), then:
    python3 imessage_dedupe.py --dry-run --root ~/Desktop/Attachments-copy

Setup (one time, only needed to actually collapse duplicates in place):
    1. Quit the Messages app (the script refuses to run otherwise).
    2. Give Terminal Full Disk Access: System Settings > Privacy & Security >
       Full Disk Access > turn on Terminal, then quit and reopen Terminal.
       You can revoke it afterwards.
"""

import argparse
import hashlib
import os
import stat
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

CHUNK_SIZE = 4 * 1024 * 1024

MEDIA_EXTS = {
    ".heic", ".heif", ".jpg", ".jpeg", ".png", ".gif", ".tif", ".tiff",
    ".bmp", ".webp", ".mov", ".mp4", ".m4v",
}


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(CHUNK_SIZE)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def collect_files(root):
    """Return [(path, size, dev, ino, blocks)] for regular files under root."""
    files = []
    for dirpath, _dirnames, filenames in os.walk(root, followlinks=False):
        for name in filenames:
            p = Path(dirpath) / name
            try:
                st = os.lstat(p)
            except OSError:
                continue
            if not stat.S_ISREG(st.st_mode):
                continue
            files.append((p, st.st_size, st.st_dev, st.st_ino, st.st_blocks,
                          st.st_mtime_ns))
    return files


def messages_running():
    """True if the Messages app is currently running."""
    try:
        out = subprocess.run(["pgrep", "-x", "Messages"],
                             capture_output=True, timeout=10)
        return out.returncode == 0
    except Exception:
        return False


def unchanged(rec):
    """Re-stat a file; return True if size and mtime match what we recorded."""
    try:
        st = os.lstat(rec[0])
    except OSError:
        return False
    return (st.st_size, st.st_mtime_ns) == (rec[1], rec[5])


def main():
    ap = argparse.ArgumentParser(
        description="Collapse duplicate iMessage attachments with hard links."
    )
    ap.add_argument("--dry-run", action="store_true",
                    help="Report duplicates without changing anything.")
    ap.add_argument("--root", default=None,
                    help="Scan a different folder instead of "
                         "~/Library/Messages/Attachments (e.g. a copy made "
                         "in Finder, so no Full Disk Access is needed to "
                         "check for duplicates).")
    args = ap.parse_args()

    if args.root:
        attach = Path(args.root).expanduser()
    else:
        attach = Path.home() / "Library" / "Messages" / "Attachments"
    try:
        os.listdir(attach)
    except FileNotFoundError:
        sys.exit(f"No iMessage attachments folder found at {attach}")
    except PermissionError:
        sys.exit(
            "macOS blocked access to ~/Library/Messages.\n"
            "To CHECK for duplicates without Full Disk Access: copy the\n"
            "Attachments folder in Finder to e.g. your Desktop, then run:\n"
            "  python3 imessage_dedupe.py --dry-run --root ~/Desktop/Attachments\n"
            "To COLLAPSE duplicates in place, grant Full Disk Access:\n"
            "System Settings > Privacy & Security > Full Disk Access > turn on\n"
            "Terminal, then quit and reopen Terminal. You can revoke it after."
        )

    print(f"Scanning {attach} ... (quit the Messages app first if you haven't)")
    files = collect_files(attach)
    print(f"Found {len(files)} files.")

    # Only files sharing a size with another file are worth hashing.
    by_size = defaultdict(list)
    for rec in files:
        by_size[rec[1]].append(rec)

    dup_groups = []  # (keeper_rec, [dupe_recs], size)
    hashed = 0
    for size, recs in by_size.items():
        if len({r[3] for r in recs}) < 2:  # distinct inodes
            continue
        by_hash = defaultdict(list)
        for rec in recs:
            try:
                digest = sha256_of(rec[0])
            except OSError as e:
                print(f"  warning: could not read {rec[0]}: {e}", file=sys.stderr)
                continue
            by_hash[digest].append(rec)
            hashed += 1
            if hashed % 200 == 0:
                print(f"  hashed {hashed} files...", flush=True)
        for hrecs in by_hash.values():
            uniq = {}
            for r in hrecs:
                uniq.setdefault((r[2], r[3]), r)
            if len(uniq) < 2:
                continue
            ordered = sorted(uniq.values(), key=lambda r: str(r[0]))
            dup_groups.append((ordered[0], ordered[1:], size))

    if args.dry_run:
        print("\nDRY RUN -- no changes made.\n")
    elif messages_running():
        sys.exit("The Messages app is running. Quit it first, then re-run.\n"
                 "(Use --dry-run if you just want the report.)")

    collapsed = 0
    for keeper_rec, dupe_recs, _size in dup_groups:
        keeper = keeper_rec[0]
        if not unchanged(keeper_rec):
            print(f"  warning: {keeper} changed during scan; skipping group",
                  file=sys.stderr)
            continue
        for drec in dupe_recs:
            dp = drec[0]
            if args.dry_run:
                continue
            if not unchanged(drec):
                print(f"  warning: {dp} changed during scan; skipping",
                      file=sys.stderr)
                continue
            tmp = dp.with_name(f".dedupe-{os.getpid()}-{dp.name}")
            try:
                os.link(keeper, tmp)   # tmp now shares keeper's data
                os.replace(tmp, dp)    # atomically point dp at the same data
                collapsed += 1
            except OSError as e:
                print(f"  warning: could not dedupe {dp}: {e}", file=sys.stderr)

    total_dupes = sum(len(d) for _, d, _ in dup_groups)
    logical_saved = sum(size * len(d) for _, d, size in dup_groups)
    disk_saved = sum(r[4] * 512 for _, d, _ in dup_groups for r in d)
    media_dupes = sum(
        1 for _, d, _ in dup_groups for r in d
        if r[0].suffix.lower() in MEDIA_EXTS
    )

    print(f"\nDuplicate groups : {len(dup_groups)}")
    print(f"Duplicate files  : {total_dupes} ({media_dupes} photos/videos)")
    if not args.dry_run:
        print(f"Files collapsed  : {collapsed}")
    print(f"Space reclaimed  : ~{logical_saved / 1e9:.2f} GB "
          f"(~{disk_saved / 1e9:.2f} GB on disk)")

    if args.dry_run and dup_groups:
        print("\nLargest duplicate groups:")
        top = sorted(dup_groups, key=lambda g: g[2] * len(g[1]), reverse=True)[:10]
        for keeper_rec, dupe_recs, size in top:
            n = len(dupe_recs) + 1
            print(f"  {n}x {size / 1e6:.1f} MB  e.g. {keeper_rec[0].name}")
        print("\nRun without --dry-run to collapse them.")


if __name__ == "__main__":
    main()
