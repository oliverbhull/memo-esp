#!/usr/bin/env python3
"""
Simple always-on ESP32 audio receiver and transcription server.
Receives audio chunks continuously and transcribes them immediately.
"""

from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from urllib.parse import urlparse, parse_qs
import datetime
import wave
import os
import subprocess
import json
from pathlib import Path

# Configuration
WHISPER_MODEL_PATH = os.path.expanduser("~/models/whisper/ggml-small.en-q5_1.bin")
WHISPER_CPP_PATH = os.path.expanduser("~/dev/whisper.cpp/build/bin/whisper-cli")
SAVE_DIR = "received_audio"
TRANSCRIPT_DIR = "transcripts"

# Create directories
os.makedirs(SAVE_DIR, exist_ok=True)
os.makedirs(TRANSCRIPT_DIR, exist_ok=True)


def transcribe_with_whisper_cpp(wav_path, whisper_binary):
    """Transcribe using whisper.cpp binary"""
    try:
        cmd = [
            whisper_binary,
            "-m", WHISPER_MODEL_PATH,
            "-f", wav_path,
            "--no-timestamps",
            "--output-txt",
            "--output-file", "/tmp/whisper_output"
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

        if result.returncode == 0:
            output_file = "/tmp/whisper_output.txt"
            if os.path.exists(output_file):
                with open(output_file, 'r') as f:
                    text = f.read().strip()
                os.remove(output_file)
                return text
        else:
            print(f"whisper.cpp error: {result.stderr}")
            return None
    except Exception as e:
        print(f"Transcription error: {e}")
        return None


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    """Handle requests in separate threads"""
    daemon_threads = True


class SimpleAudioServer(BaseHTTPRequestHandler):
    whisper_binary = None

    def do_POST(self):
        if self.path.startswith('/audio'):
            self.handle_audio()
        else:
            self.send_error(404)

    def handle_audio(self):
        # Parse query parameters
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        device_id = params.get('device', ['unknown'])[0]
        sample_rate = int(params.get('rate', [16000])[0])
        bits_per_sample = int(params.get('bits', [16])[0])
        channels = int(params.get('channels', [1])[0])

        # Read audio data
        content_length = int(self.headers['Content-Length'])
        audio_data = self.rfile.read(content_length)

        # Generate filename with timestamp
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        base_filename = f"{device_id}_{timestamp}"

        # Save as WAV
        wav_path = os.path.join(SAVE_DIR, f"{base_filename}.wav")
        with wave.open(wav_path, 'wb') as wav_file:
            wav_file.setnchannels(channels)
            wav_file.setsampwidth(bits_per_sample // 8)
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(audio_data)

        # Calculate duration
        duration = len(audio_data) / (sample_rate * channels * (bits_per_sample // 8))

        print(f"📥 Received {duration:.2f}s audio from {device_id}")

        # Transcribe
        if self.whisper_binary and duration > 0.5:  # Only transcribe if > 0.5s
            print(f"   Transcribing...")
            transcript = transcribe_with_whisper_cpp(wav_path, self.whisper_binary)

            if transcript:
                print(f"   📝 \"{transcript}\"")

                # Save transcript
                transcript_path = os.path.join(TRANSCRIPT_DIR, f"{base_filename}.txt")
                with open(transcript_path, 'w') as f:
                    f.write(transcript)

                # Save JSON with metadata
                json_path = os.path.join(TRANSCRIPT_DIR, f"{base_filename}.json")
                metadata = {
                    "device_id": device_id,
                    "timestamp": timestamp,
                    "duration_sec": duration,
                    "sample_rate": sample_rate,
                    "bits_per_sample": bits_per_sample,
                    "channels": channels,
                    "transcript": transcript
                }
                with open(json_path, 'w') as f:
                    json.dump(metadata, f, indent=2)

        # Send success response
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps({"status": "success"}).encode())

    def log_message(self, format, *args):
        # Suppress default request logging
        pass


def main():
    # Check for whisper.cpp
    if not os.path.exists(WHISPER_CPP_PATH):
        print(f"\n⚠️  Warning: whisper.cpp not found at {WHISPER_CPP_PATH}")
        print("Audio will be saved but not transcribed.")
        whisper_binary = None
    else:
        whisper_binary = WHISPER_CPP_PATH

    if whisper_binary and not os.path.exists(WHISPER_MODEL_PATH):
        print(f"\n⚠️  Warning: Model not found at {WHISPER_MODEL_PATH}")
        print("Audio will be saved but not transcribed.")
        whisper_binary = None

    SimpleAudioServer.whisper_binary = whisper_binary

    server_address = ('', 8000)
    httpd = ThreadedHTTPServer(server_address, SimpleAudioServer)

    print("=" * 60)
    print("ESP32 Simple Audio Receiver (Always On)")
    print("=" * 60)
    print(f"Listening on: http://0.0.0.0:8000/audio")
    print(f"Audio saved to: {os.path.abspath(SAVE_DIR)}")
    print(f"Transcripts saved to: {os.path.abspath(TRANSCRIPT_DIR)}")
    if whisper_binary:
        print(f"Transcription: ENABLED")
        print(f"Model: {WHISPER_MODEL_PATH}")
    else:
        print(f"Transcription: DISABLED")
    print("=" * 60)
    print("\nServer running... Press Ctrl+C to stop\n")

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n\nShutting down server...")
        httpd.shutdown()


if __name__ == '__main__':
    main()
