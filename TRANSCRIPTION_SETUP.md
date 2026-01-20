# Whisper Transcription Setup

## Quick Start

### Install Whisper Library

You have three options. I recommend **faster-whisper** as it's the fastest and works well with your existing models:

```bash
# Option 1: faster-whisper (recommended)
pip install faster-whisper

# Option 2: Build whisper.cpp (if you want to use your .bin files directly)
cd ~/dev
git clone https://github.com/ggerganov/whisper.cpp
cd whisper.cpp
make

# Option 3: Original OpenAI Whisper
pip install openai-whisper
```

### Run the Transcription Server

```bash
cd /Users/oliverhull/dev/memo-esp
python3 transcription_server.py
```

The server will:
- Auto-detect which Whisper implementation you have installed
- Use your model at `~/models/whisper/ggml-small.en-q5_1.bin`
- Listen on port 8000
- Transcribe audio in real-time as it arrives from ESP32
- Save transcripts to `transcripts/` directory

## Expected Output

When running, you'll see:

```
============================================================
ESP32 Audio Transcription Server
============================================================
Listening on: http://0.0.0.0:8000/audio
Transcription: faster-whisper
Model: /Users/oliverhull/models/whisper/ggml-small.en-q5_1.bin
Audio saved to: /Users/oliverhull/dev/memo-esp/received_audio
Transcripts saved to: /Users/oliverhull/dev/memo-esp/transcripts

Waiting for audio chunks...

============================================================
Received: esp32-dev-01_20260119_123456_789012
Duration: 1.00s | Size: 32000 bytes
Transcribing...
Transcript: Hello, this is a test of the audio system.
Saved: transcripts/esp32-dev-01_20260119_123456_789012.txt
```

## Output Files

For each audio chunk, the server creates:

1. **WAV file** (if SAVE_AUDIO=True):
   - `received_audio/esp32-dev-01_TIMESTAMP.wav`
   - Full audio recording

2. **Transcript text**:
   - `transcripts/esp32-dev-01_TIMESTAMP.txt`
   - Plain text transcript

3. **JSON metadata**:
   - `transcripts/esp32-dev-01_TIMESTAMP.json`
   - Includes transcript + metadata (duration, sample rate, etc.)

## Configuration

Edit `transcription_server.py` to customize:

```python
# Model path (update if needed)
WHISPER_MODEL_PATH = os.path.expanduser("~/models/whisper/ggml-small.en-q5_1.bin")

# Save audio files? (set to False to save disk space)
SAVE_AUDIO = True

# Directories
SAVE_DIR = "received_audio"
TRANSCRIPT_DIR = "transcripts"
```

## Using Different Models

You have these models available:
- `ggml-small.en-q5_1.bin` (currently configured - good balance)
- `ggml-small.en-q8_0.bin` (higher quality, slower)
- `ggml-base.en-q5_1.bin` (faster, less accurate)
- `ggml-large-v3-turbo-q5_0.bin` (best quality, slowest)

To change, update `WHISPER_MODEL_PATH` in the script.

## Performance Expectations

With `small.en` model on 1-second audio chunks:
- **faster-whisper**: ~0.5-1 sec transcription time
- **openai-whisper**: ~1-2 sec transcription time
- **whisper.cpp**: ~0.3-0.8 sec transcription time

Real-time transcription is achievable with all methods!

## Troubleshooting

### "No module named 'faster_whisper'"
```bash
pip install faster-whisper
```

### Model file not found
Update the path in `transcription_server.py`:
```python
WHISPER_MODEL_PATH = os.path.expanduser("~/models/whisper/YOUR_MODEL.bin")
```

### Transcription too slow
- Use `base.en` model instead of `small.en`
- Install faster-whisper if using openai-whisper
- Use whisper.cpp for best performance

### Audio quality issues
- Check ESP32 is sending proper 16kHz, 16-bit mono audio
- Verify WAV files play correctly: `afplay received_audio/*.wav`
- Try a larger model for better accuracy

## Next Steps

Once transcription is working:
- Add POST endpoint to forward transcripts to another service
- Implement real-time streaming (transcribe on partial audio)
- Add voice activity detection to skip silence
- Buffer multiple chunks for better context
