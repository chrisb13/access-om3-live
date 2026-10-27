"""Exit 0 if payu's datastore in an archive folder can be reused for that archive.

Usage: datastore_ok.py DATASTORE.json DATASTORE.csv[.gz] ARCHIVE_DIR   (called by mkfigs_live.sh)
Checks that the json's catalog_file is that CSV and that the CSV lists readable files in this archive:
a datastore copied from elsewhere (e.g. synced to /g/data from /scratch) may point at the originals.
It also checks that ocean.stats.nc is listed when the archive has it: payu's postscript builds the
datastore with analysis3-25.05, whose AccessOm3Builder excludes ocean.stats.nc, and the ocean_stats
notebook then finds 0 datasets. Such a datastore is not reused, so mkfigs_live.sh rebuilds it.
"""
import csv
import gzip
import json
import os
import sys

ds, path, archive = sys.argv[1], sys.argv[2], os.path.realpath(sys.argv[3])


def has_stats(archive):
    """Does any output folder of the archive have an ocean.stats.nc?"""
    return any(os.path.exists(os.path.join(archive, d, "ocean.stats.nc"))
               for d in sorted(os.listdir(archive)) if d.startswith("output"))


try:
    with open(ds) as f:
        catalog = json.load(f).get("catalog_file") or ""
    catalog = catalog[len("file://"):] if catalog.startswith("file://") else catalog
    same = os.path.realpath(os.path.join(os.path.dirname(ds), catalog)) == os.path.realpath(path)
    with (gzip.open if path.endswith(".gz") else open)(path, "rt") as f:
        rows = csv.DictReader(f)
        first = (next(rows, None) or {}).get("path", "")
        stats = any(r.get("filename") == "ocean.stats.nc" for r in rows) or first.endswith("/ocean.stats.nc")
    stats = stats or not has_stats(archive)
except (OSError, ValueError, csv.Error):
    same, first, stats = False, "", False
inside = bool(first) and os.path.realpath(first).startswith(archive + os.sep) and os.access(first, os.R_OK)
sys.exit(0 if same and inside and stats else 1)
