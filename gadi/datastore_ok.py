"""Exit 0 if payu's datastore in an archive folder can be reused for that archive.

Usage: datastore_ok.py DATASTORE.json DATASTORE.csv[.gz] ARCHIVE_DIR   (called by mkfigs_live.sh)
Checks that the json's catalog_file is that CSV and that the CSV lists readable files in this archive:
a datastore copied from elsewhere (e.g. synced to /g/data from /scratch) may point at the originals.
"""
import csv
import gzip
import json
import os
import sys

ds, path, archive = sys.argv[1], sys.argv[2], os.path.realpath(sys.argv[3])
try:
    with open(ds) as f:
        catalog = json.load(f).get("catalog_file") or ""
    catalog = catalog[len("file://"):] if catalog.startswith("file://") else catalog
    same = os.path.realpath(os.path.join(os.path.dirname(ds), catalog)) == os.path.realpath(path)
    with (gzip.open if path.endswith(".gz") else open)(path, "rt") as f:
        first = (next(csv.DictReader(f), None) or {}).get("path", "")
except (OSError, ValueError, csv.Error):
    same, first = False, ""
inside = bool(first) and os.path.realpath(first).startswith(archive + os.sep) and os.access(first, os.R_OK)
sys.exit(0 if same and inside else 1)
