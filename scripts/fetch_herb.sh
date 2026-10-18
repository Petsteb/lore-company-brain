#!/bin/sh
# Download the HERB dataset (Salesforce Research, CC-BY-NC-4.0) from Hugging Face.
set -e
DEST=${1:-data-raw/herb}
BASE=https://huggingface.co/datasets/Salesforce/HERB/resolve/main
mkdir -p "$DEST/metadata" "$DEST/products"
FILES=$(curl -sfL https://huggingface.co/api/datasets/Salesforce/HERB | python3 -c "import sys,json; print('\n'.join(s['rfilename'] for s in json.load(sys.stdin)['siblings'] if s['rfilename'].endswith('.json')))")
for f in $FILES; do curl -sfL "$BASE/$f" -o "$DEST/$f"; done
echo "HERB: $(ls "$DEST/products" | wc -l) product files"
