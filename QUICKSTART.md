# Quick Start Guide - ESP32 WiFi Audio Transcription

## Setup (One Time)

### 1. Build whisper.cpp

```bash
cd /Users/oliverhull/dev/memo-esp
./setup_whisper.sh
```

This will clone and build whisper.cpp to `~/dev/whisper.cpp/`.

## Running the System

### 1. Start the Transcription Server

```bash
cd /Users/oliverhull/dev/memo-esp
python3 transcription_server.py
```

You should see:
```
============================================================
ESP32 Audio Transcription Server
============================================================
Listening on: http://0.0.0.0:8000/audio
Transcription: whisper-cpp
Binary: /Users/oliverhull/dev/whisper.cpp/main
Model: /Users/oliverhull/models/whisper/ggml-small.en-q5_1.bin
...
Waiting for audio chunks...
```

### 2. Upload Firmware to ESP32

In another terminal:

```bash
cd /Users/oliverhull/dev/memo-esp

# Build and upload
pio run --target upload

# Monitor serial output
pio device monitor
```

### 3. Watch the Magic Happen!

**ESP32 Serial Monitor:**
```
WiFi connected!
IP address: 10.104.x.x
...
Captured 32000 bytes in 742 ms, sending to server...
Send successful
```

**Transcription Server:**
```
============================================================
Received: esp32-dev-01_20260119_143022_123456
Duration: 1.00s | Size: 32000 bytes
Transcribing...
Transcript: Hello, this is a test of the audio transcription system.
Saved: transcripts/esp32-dev-01_20260119_143022_123456.txt
```

## Output Files

```
transcripts/
├── esp32-dev-01_TIMESTAMP.txt   # Plain text transcript
└── esp32-dev-01_TIMESTAMP.json  # JSON with metadata

received_audio/
└── esp32-dev-01_TIMESTAMP.wav   # Audio file (if SAVE_AUDIO=True)
```

## Troubleshooting

### "No module named 'faster_whisper'" or Python errors
- Use whisper.cpp instead (run `./setup_whisper.sh`)
- Python 3.14 has compatibility issues with faster-whisper

### ESP32 "connection refused"
- Make sure transcription_server.py is running
- Check IP address in `include/config.h` matches your computer
- Find your IP: `ifconfig | grep "inet "` (look for 10.x.x.x or 192.168.x.x)

### No transcripts appearing
- Check `received_audio/` for WAV files - if present, whisper.cpp issue
- Try running whisper.cpp manually:
  ```bash
  ~/dev/whisper.cpp/main -m ~/models/whisper/ggml-small.en-q5_1.bin \
    -f received_audio/LATEST_FILE.wav
  ```

### Audio files empty or corrupted
- Check I2S pins in `config.h` match your board (GPIO 42, 41 for XIAO ESP32-S3)
- Try playing WAV file: `afplay received_audio/LATEST_FILE.wav`

## Configuration

Edit `include/config.h` to change:
- WiFi network and password
- Server IP address
- Audio sample rate
- Chunk duration

Edit `transcription_server.py` to change:
- Whisper model (`ggml-small.en-q5_1.bin` → `ggml-large-v3-turbo-q5_0.bin`)
- Save audio files on/off
- Output directories

## Performance

With `small.en` model on M-series Mac:
- Transcription time: ~0.3-0.8 sec per 1-second chunk
- Real-time capable with headroom
- For faster processing, use `base.en` model
- For better accuracy, use `large-v3-turbo` model
