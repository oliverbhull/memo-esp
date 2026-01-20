#!/usr/bin/env python3
"""
Simple HTTP server to receive and save audio chunks from ESP32-S3.
Saves raw PCM data and can optionally convert to WAV format.
"""

from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import datetime
import struct
import wave
import os

# Configuration
SAVE_DIR = "received_audio"
SAVE_RAW_PCM = True
SAVE_WAV = True


class AudioReceiver(BaseHTTPRequestHandler):
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

        # Create output directory if needed
        os.makedirs(SAVE_DIR, exist_ok=True)

        # Save raw PCM
        if SAVE_RAW_PCM:
            pcm_path = os.path.join(SAVE_DIR, f"{base_filename}.pcm")
            with open(pcm_path, 'wb') as f:
                f.write(audio_data)
            print(f"Saved PCM: {pcm_path} ({len(audio_data)} bytes)")

        # Save as WAV
        if SAVE_WAV:
            wav_path = os.path.join(SAVE_DIR, f"{base_filename}.wav")
            self.save_wav(wav_path, audio_data, sample_rate, channels, bits_per_sample)
            print(f"Saved WAV: {wav_path}")

        # Print info
        duration_sec = len(audio_data) / (sample_rate * channels * (bits_per_sample // 8))
        print(f"Device: {device_id} | "
              f"Rate: {sample_rate}Hz | "
              f"Bits: {bits_per_sample} | "
              f"Channels: {channels} | "
              f"Duration: {duration_sec:.2f}s")

        # Send success response
        self.send_response(200)
        self.send_header('Content-Type', 'text/plain')
        self.end_headers()
        self.wfile.write(b'OK')

    def save_wav(self, filename, pcm_data, sample_rate, channels, bits_per_sample):
        """Convert raw PCM to WAV file"""
        with wave.open(filename, 'wb') as wav_file:
            wav_file.setnchannels(channels)
            wav_file.setsampwidth(bits_per_sample // 8)
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(pcm_data)

    def log_message(self, format, *args):
        # Suppress default request logging
        pass


def main():
    server_address = ('', 8000)
    httpd = HTTPServer(server_address, AudioReceiver)

    print("=" * 60)
    print("ESP32-S3 Audio Receiver Server")
    print("=" * 60)
    print(f"Listening on: http://0.0.0.0:8000/audio")
    print(f"Save directory: {os.path.abspath(SAVE_DIR)}")
    print(f"Saving RAW PCM: {SAVE_RAW_PCM}")
    print(f"Saving WAV: {SAVE_WAV}")
    print("\nWaiting for audio chunks...\n")

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n\nShutting down server...")
        httpd.shutdown()


if __name__ == '__main__':
    main()
