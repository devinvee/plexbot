#!/bin/bash
# Build script for the web UI

cd "$(dirname "$0")/webui" || exit 1

echo "Building web UI..."
npm install
npm run build

if [ $? -eq 0 ]; then
    echo "Web UI built successfully!"
else
    echo "Web UI build failed!"
    exit 1
fi

