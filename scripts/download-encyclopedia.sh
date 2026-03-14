#!/bin/bash
# Download the Encyclopedia Britannica 11th Edition test corpus from Project Gutenberg.
# This creates docs/encyclopedia/merged_encyclopedia.txt (~11MB, ~2M tokens).
#
# The corpus is used for testing RLM on large reference documents.
# Source: https://www.gutenberg.org/ebooks/search/?query=encyclopaedia+britannica+11th

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
OUTPUT_DIR="$REPO_DIR/docs/encyclopedia"
OUTPUT_FILE="$OUTPUT_DIR/merged_encyclopedia.txt"

if [ -f "$OUTPUT_FILE" ]; then
    echo "Encyclopedia already exists at $OUTPUT_FILE"
    echo "Size: $(du -h "$OUTPUT_FILE" | cut -f1)"
    exit 0
fi

mkdir -p "$OUTPUT_DIR"

# Download a representative set of volumes (A-C, covering ~11MB)
# Full list: https://www.gutenberg.org/ebooks/search/?query=encyclopaedia+britannica+11th
VOLUMES=(
    "https://www.gutenberg.org/cache/epub/44589/pg44589.txt"   # Vol 1 (A)
    "https://www.gutenberg.org/cache/epub/44590/pg44590.txt"   # Vol 2 (AN-AUS)
    "https://www.gutenberg.org/cache/epub/44591/pg44591.txt"   # Vol 3 (AUS-BIS)
    "https://www.gutenberg.org/cache/epub/44592/pg44592.txt"   # Vol 4 (BIS-CAL)
)

echo "Downloading Encyclopedia Britannica 11th Edition from Project Gutenberg..."
echo "This is public domain content (1910-1911)."
echo ""

> "$OUTPUT_FILE"

for url in "${VOLUMES[@]}"; do
    filename=$(basename "$url")
    echo "  Downloading $filename..."
    curl -sL "$url" >> "$OUTPUT_FILE"
    echo "" >> "$OUTPUT_FILE"
done

echo ""
echo "Done! Merged encyclopedia saved to: $OUTPUT_FILE"
echo "Size: $(du -h "$OUTPUT_FILE" | cut -f1)"
echo "Lines: $(wc -l < "$OUTPUT_FILE" | tr -d ' ')"
