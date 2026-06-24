#!/bin/bash
# Ibis Publisher — macOS build script
# Creates a standalone IbisPublisher.app in dist/
# Run from: ibis-publisher/companion-app/

set -e

echo "🦢 Ibis Publisher — macOS Build"
echo "================================"

# Install dependencies
echo "→ Installing Python dependencies..."
pip install -r requirements.txt

# Copy schema.sql into companion-app so PyInstaller can bundle it
echo "→ Copying shared files..."
cp ../shared/schema.sql ./schema.sql

# Build with PyInstaller
echo "→ Building .app bundle..."
pyinstaller \
    --name "Ibis Publisher" \
    --windowed \
    --onedir \
    --add-data "schema.sql:." \
    --hidden-import "plyer.platforms.macosx.notification" \
    --osx-bundle-identifier "com.ibispublisher.app" \
    app.py

echo "✅ Build complete: dist/Ibis Publisher.app"
echo ""
echo "To install: drag 'Ibis Publisher.app' to your Applications folder."
