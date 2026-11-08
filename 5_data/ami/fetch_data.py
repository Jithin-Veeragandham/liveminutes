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
import sys
import urllib.request
import zipfile

import ami_settings

# Only these parts of the corpus are used: transcripts (words + dialogue acts), human summaries, speaker info.
USED_PARTS = ("words/", "dialogueActs/", "abstractive/", "corpusResources/meetings.xml",
              "corpusResources/participants.xml", "LICENCE.txt", "00README_MANUAL.txt")


# Return the SHA-256 hex digest of one file.
def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


# One hash for the whole extracted dataset: SHA-256 over every used file's path and hash, in sorted order.
def data_hash(root=ami_settings.AMI_DIR):
    h = hashlib.sha256()
    for path in sorted(used_files(root)):
        h.update(f"{os.path.relpath(path, root)}  {sha256_file(path)}\n".encode())
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


# Download the zip if needed, check its hash, and extract the used parts.
def fetch():
    m = load_manifest()
    os.makedirs(ami_settings.RAW_DIR, exist_ok=True)
    zip_path = os.path.join(ami_settings.RAW_DIR, m["file"])
    if not os.path.exists(zip_path):
        print(f"downloading {m['url']} ({m['bytes'] / 1e6:.0f} MB) ...", flush=True)
        urllib.request.urlretrieve(m["url"], zip_path)
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
