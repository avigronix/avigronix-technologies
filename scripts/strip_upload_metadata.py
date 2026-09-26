#!/usr/bin/env python3
"""Remove EXIF/GPS and other metadata from images already in app/uploads/.

New uploads are cleaned automatically (see app/image_sanitize.py). This
one-off script cleans files uploaded before that existed. It needs no
admin key and no database: it works directly on the files.

    python scripts/strip_upload_metadata.py --dry-run   # report only
    python scripts/strip_upload_metadata.py             # clean in place

* Only files that actually contain metadata are rewritten, so running it
  again is harmless (no repeated JPEG re-compression).
* Filenames never change, so shop records keep pointing at the same files.
* Each file is written to a temporary file and atomically swapped in; the
  original modification time is kept.
* Anything that isn't a readable image is reported and left untouched.
"""
import argparse
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))

from image_sanitize import FORMAT_FOR_EXTENSION, InvalidImageError, metadata_keys, strip_metadata  # noqa: E402

UPLOAD_SUBDIRS = ("logos", "banners", "bank_qr", "payment_qr", "shop_qr")


def clean_uploads(uploads_dir: Path, dry_run: bool = False) -> dict:
    report = {"cleaned": [], "already_clean": [], "skipped": []}
    for sub in UPLOAD_SUBDIRS:
        folder = uploads_dir / sub
        if not folder.is_dir():
            continue
        for path in sorted(folder.iterdir()):
            if not path.is_file() or path.name.startswith("."):
                continue
            rel = f"{sub}/{path.name}"
            ext = path.suffix.lower()
            if ext not in FORMAT_FOR_EXTENSION:
                report["skipped"].append((rel, "not an image extension"))
                continue
            data = path.read_bytes()
            try:
                found = metadata_keys(data)
            except InvalidImageError:
                report["skipped"].append((rel, "not a readable image"))
                continue
            if not found:
                report["already_clean"].append(rel)
                continue
            if not dry_run:
                clean = strip_metadata(data, ext)
                stat = path.stat()
                fd, tmp = tempfile.mkstemp(dir=folder, prefix=".strip-", suffix=ext)
                try:
                    with os.fdopen(fd, "wb") as out:
                        out.write(clean)
                    os.chmod(tmp, stat.st_mode & 0o777)
                    os.replace(tmp, path)
                    os.utime(path, (stat.st_atime, stat.st_mtime))
                except BaseException:
                    if os.path.exists(tmp):
                        os.remove(tmp)
                    raise
            report["cleaned"].append((rel, found))
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--uploads", default=str(ROOT / "app" / "uploads"), help="uploads folder (default: app/uploads)")
    parser.add_argument("--dry-run", action="store_true", help="only report, change nothing")
    args = parser.parse_args(argv)

    uploads = Path(args.uploads)
    if not uploads.is_dir():
        print(f"No such folder: {uploads}", file=sys.stderr)
        return 1
    report = clean_uploads(uploads, dry_run=args.dry_run)
    verb = "Would clean" if args.dry_run else "Cleaned"
    for rel, found in report["cleaned"]:
        print(f"{verb}: {rel}  (removed: {', '.join(found)})")
    for rel, why in report["skipped"]:
        print(f"Skipped: {rel}  ({why})")
    print(
        f"\n{verb} {len(report['cleaned'])} file(s); "
        f"{len(report['already_clean'])} already had no metadata; {len(report['skipped'])} skipped."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
