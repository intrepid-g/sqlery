#!/bin/bash
# Script to migrate Django files to django/ subfolder
# Usage: ./migrate_django_files.sh <source_file> <dest_file>

set -e

SOURCE="$1"
DEST="$2"

if [ -z "$SOURCE" ] || [ -z "$DEST" ]; then
    echo "Usage: $0 <source_file> <dest_file>"
    exit 1
fi

if [ ! -f "$SOURCE" ]; then
    echo "Error: Source file $SOURCE does not exist"
    exit 1
fi

# Step 1: Copy file to destination
echo "Copying $SOURCE to $DEST..."
cp "$SOURCE" "$DEST"

# Step 2: Read original file content
ORIGINAL_CONTENT=$(cat "$SOURCE")

# Step 3: Create stub in original location
echo "Creating stub in $SOURCE..."
cat > "$SOURCE" << 'STUB_HEADER'
# #CLEANUP: This file has been moved to src/sqlery/django/
# This stub exists for backward compatibility during migration.
# When django-sqlery is extracted to a separate package, this file will be removed.
#
# For now, import from the new location:
#   from sqlery.django.models import ...  (if using Django)
#
# Original code is commented out below for reference.
# ============================================================================

STUB_HEADER

# Step 4: Comment out original code
echo "$ORIGINAL_CONTENT" | sed 's/^/# /' >> "$SOURCE"

# Step 5: Add footer
cat >> "$SOURCE" << 'STUB_FOOTER'

# ============================================================================
# End of commented code
# When ready to remove: delete this entire file
STUB_FOOTER

echo "✓ Migration complete:"
echo "  - Copied to: $DEST"
echo "  - Stub created in: $SOURCE"
echo ""
