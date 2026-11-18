"""Download the AMI manual annotations and check they are exactly the version recorded in data/MANIFEST.json.

    python 5_data/ami/fetch_data.py            # download (if missing), verify, extract
    python 5_data/ami/fetch_data.py --verify   # only check the extracted files against the manifest

Delete 5_data/raw/ami/ and run this again: you get the same files back, byte for byte,
or the script stops with a hash mismatch. Public download, no account or credentials needed.
"""
import argparse
import hashlib
import json
import os
import shutil
import sys
import time
import urllib.request
import zipfile

import ami_settings

# Only these parts of the corpus are used: transcripts (words + dialogue acts), human summaries, speaker info.
USED_PARTS = ("words/", "dialogueActs/", "abstractive/", "corpusResources/meetings.xml",
              "corpusResources/participants.xml", "LICENCE.txt", "00README_MANUAL.txt")

# The AMI server sometimes refuses or drops a download (HTTP 403, connection reset); it usually works on a
# later try. Wait 2 s after the first failure, then 4, 6, ... (at most 30 s), up to DOWNLOAD_TRIES tries.
DOWNLOAD_TRIES = 10
# It can also accept the connection and then send nothing. Without a timeout that hangs forever (it once did,
# for over 10 minutes); with it, a connect or read that waits this long fails the try, and the next one starts.
DOWNLOAD_TIMEOUT_S = 60


# Return the SHA-256 hex digest of one file.
def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


# One hash for the whole extracted dataset: SHA-256 over every used file's path and hash, in sorted order.
# Paths are written with "/" on every OS, so Windows gets the same hash as macOS/Linux.
def data_hash(root=ami_settings.AMI_DIR):
    h = hashlib.sha256()
    for rel in sorted(os.path.relpath(path, root).replace(os.sep, "/") for path in used_files(root)):
        h.update(f"{rel}  {sha256_file(os.path.join(root, rel))}\n".encode())
    return h.hexdigest()


# List the extracted files that the pipeline reads.
def used_files(root=ami_settings.AMI_DIR):
    out = []
    for dirpath, _, names in os.walk(root):
        for name in names:
            rel = os.path.relpath(os.path.join(dirpath, name), root).replace(os.sep, "/")
            if rel.startswith(USED_PARTS):
                out.append(os.path.join(dirpath, name))
    return out


# Read the committed manifest (source URL, retrieval date, expected hashes).
def load_manifest():
    with open(ami_settings.MANIFEST) as f:
        return json.load(f)


# Download url to path, trying up to DOWNLOAD_TRIES times. The file is written as <path>.part and only
# renamed to path once complete, so a failed download never leaves a half-written zip behind.
def download(url, path):
    part = path + ".part"
    for attempt in range(1, DOWNLOAD_TRIES + 1):
        try:
            with urllib.request.urlopen(url, timeout=DOWNLOAD_TIMEOUT_S) as r, open(part, "wb") as f:
                shutil.copyfileobj(r, f, 1 << 20)
                expected = r.headers.get("Content-Length")
            if expected is not None and os.path.getsize(part) != int(expected):  # the check urlretrieve did
                raise OSError(f"download ended early ({os.path.getsize(part)} of {expected} bytes)")
            os.replace(part, path)
            return
        except OSError as e:   # HTTP errors (403, 503), connection resets, timeouts (TimeoutError is an OSError)
            if os.path.exists(part):
                os.remove(part)
            if attempt == DOWNLOAD_TRIES:
                sys.exit(f"download failed {DOWNLOAD_TRIES} times (last error: {e}). "
                         "The AMI server may be busy: try again in a few minutes.")
            wait = min(2 * attempt, 30)
            print(f"  try {attempt}/{DOWNLOAD_TRIES} failed ({e}); retrying in {wait} s", flush=True)
            time.sleep(wait)


# Download the zip if needed, check its hash, and extract the used parts.
def fetch():
    m = load_manifest()
    os.makedirs(ami_settings.RAW_DIR, exist_ok=True)
    zip_path = os.path.join(ami_settings.RAW_DIR, m["file"])
    if not os.path.exists(zip_path):
        print(f"downloading {m['url']} ({m['bytes'] / 1e6:.0f} MB) ...", flush=True)
        download(m["url"], zip_path)
    got = sha256_file(zip_path)
    if got != m["sha256"]:
        os.remove(zip_path)
        sys.exit(f"zip hash mismatch: expected {m['sha256']}, got {got}. The source changed; do not use it.")
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(ami_settings.AMI_DIR, [n for n in z.namelist() if n.startswith(USED_PARTS)])
    return verify()


# Check the extracted files against the manifest's data hash; stop if they differ.
def verify():
    expected = load_manifest()["data_hash"]
    if not os.path.isdir(ami_settings.AMI_DIR):
        sys.exit("AMI data missing: run  python 5_data/ami/fetch_data.py")
    got = data_hash()
    if got != expected:
        sys.exit(f"data hash mismatch: expected {expected}, got {got}. Delete {ami_settings.RAW_DIR} and fetch again.")
    print(f"AMI data OK  (data_hash {got[:12]}...)")
    return got


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--verify", action="store_true", help="only verify the extracted files")
    p.add_argument("--print-hash", action="store_true", help="print the data hash of the extracted files")
    a = p.parse_args()
    if a.print_hash:
        print(data_hash())
    elif a.verify:
        verify()
    else:
        fetch()
