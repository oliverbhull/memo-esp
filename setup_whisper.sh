#!/bin/bash
# Setup script to install whisper.cpp for transcription

set -e

echo "Setting up whisper.cpp for ESP32 transcription..."

# Check if whisper.cpp already exists
if [ -d ~/dev/whisper.cpp ]; then
    echo "whisper.cpp already exists at ~/dev/whisper.cpp"
    echo "Rebuilding..."
    cd ~/dev/whisper.cpp
    git pull
else
    echo "Cloning whisper.cpp..."
    cd ~/dev
    git clone https://github.com/ggerganov/whisper.cpp
    cd whisper.cpp
fi

# Build whisper.cpp
echo "Building whisper.cpp..."
make clean 2>/dev/null || true
make -j

# Check build succeeded
if [ -f ./build/bin/whisper-cli ] || [ -f ./bin/whisper-cli ] || [ -f ./main ]; then
    echo ""
    echo "✅ whisper.cpp built successfully!"
    if [ -f ./build/bin/whisper-cli ]; then
        echo "   Binary: ~/dev/whisper.cpp/build/bin/whisper-cli"
    elif [ -f ./bin/whisper-cli ]; then
        echo "   Binary: ~/dev/whisper.cpp/bin/whisper-cli"
    else
        echo "   Binary: ~/dev/whisper.cpp/main"
    fi
    echo ""
    echo "Available models in ~/models/whisper/:"
    ls -lh ~/models/whisper/*.bin 2>/dev/null || echo "   No models found"
    echo ""
    echo "Ready to run transcription_server.py!"
else
    echo "❌ Build failed - check for errors above"
    exit 1
fi
