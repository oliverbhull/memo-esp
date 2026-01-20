#!/usr/bin/env python3
"""
ESP32 Audio Transcription Server
Receives audio from ESP32-S3, transcribes with Whisper, and posts transcripts.
Keyboard controls: SPACE = START/STOP recording, Q = Quit
"""

from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from urllib.parse import urlparse, parse_qs
import datetime
import wave
import os
import tempfile
import subprocess
import json
from pathlib import Path
import threading
import sys
import select

# Configuration
WHISPER_MODEL_PATH = os.path.expanduser("~/models/whisper/ggml-small.en-q5_1.bin")
WHISPER_CPP_PATH = os.path.expanduser("~/dev/whisper.cpp/build/bin/whisper-cli")
SAVE_AUDIO = True
SAVE_DIR = "received_audio"
TRANSCRIPT_DIR = "transcripts"

# Create directories
os.makedirs(SAVE_DIR, exist_ok=True)
os.makedirs(TRANSCRIPT_DIR, exist_ok=True)

# Global state for recording control
recording_state = {
    'is_recording': False,
    'audio_chunks': [],
    'start_time': None,
    'stop_time': None,  # Track when recording was stopped
    'lock': threading.Lock()
}

# SSE clients
sse_clients = []
sse_lock = threading.Lock()


def detect_whisper_method():
    """Auto-detect available Whisper implementation"""
    # Check for whisper.cpp (newer versions use whisper-cli)
    whisper_cpp_paths = [
        os.path.expanduser("~/dev/whisper.cpp/build/bin/whisper-cli"),
        os.path.expanduser("~/dev/whisper.cpp/bin/whisper-cli"),
        os.path.expanduser("~/dev/whisper.cpp/build/bin/main"),
        os.path.expanduser("~/dev/whisper.cpp/main"),
        "/usr/local/bin/whisper-cli",
        "/usr/local/bin/whisper-cpp",
        "/opt/homebrew/bin/whisper-cli",
        "/opt/homebrew/bin/whisper-cpp"
    ]

    for path in whisper_cpp_paths:
        if os.path.exists(path):
            print(f"Found whisper.cpp at: {path}")
            return 'whisper-cpp', path

    # Check for faster-whisper
    try:
        import faster_whisper
        print("Found faster-whisper library")
        return 'faster-whisper', None
    except ImportError:
        pass

    # Check for openai-whisper
    try:
        import whisper
        print("Found openai-whisper library")
        return 'openai-whisper', None
    except ImportError:
        pass

    return None, None


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
            # whisper-cli writes to a .txt file
            output_file = "/tmp/whisper_output.txt"
            if os.path.exists(output_file):
                with open(output_file, 'r') as f:
                    text = f.read().strip()
                os.remove(output_file)
                return text
            else:
                # Fall back to stdout
                text = result.stdout.strip()
                import re
                text = re.sub(r'\[.*?\]', '', text).strip()
                return text
        else:
            print(f"whisper.cpp error: {result.stderr}")
            return None
    except Exception as e:
        print(f"Transcription error: {e}")
        return None


def transcribe_with_faster_whisper(wav_path):
    """Transcribe using faster-whisper library"""
    try:
        from faster_whisper import WhisperModel

        # Load model (will download if needed)
        model = WhisperModel("small.en", device="cpu", compute_type="int8")

        segments, info = model.transcribe(wav_path, beam_size=5)

        text = " ".join([segment.text for segment in segments])
        return text.strip()
    except Exception as e:
        print(f"Transcription error: {e}")
        return None


def transcribe_with_openai_whisper(wav_path):
    """Transcribe using openai-whisper library"""
    try:
        import whisper

        model = whisper.load_model("small.en")
        result = model.transcribe(wav_path)
        return result["text"].strip()
    except Exception as e:
        print(f"Transcription error: {e}")
        return None


class TranscriptionServer(BaseHTTPRequestHandler):
    whisper_method = None
    whisper_binary = None

    def do_GET(self):
        if self.path == '/' or self.path == '/index.html':
            self.serve_static('static/index.html', 'text/html')
        elif self.path.startswith('/status'):
            self.handle_status()
        elif self.path.startswith('/transcripts'):
            self.handle_get_transcripts()
        elif self.path.startswith('/events'):
            self.handle_sse()
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path.startswith('/audio'):
            self.handle_audio()
        else:
            self.send_error(404)

    def serve_static(self, filepath, content_type):
        """Serve static files"""
        try:
            with open(filepath, 'rb') as f:
                content = f.read()
            self.send_response(200)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        except FileNotFoundError:
            self.send_error(404)

    def handle_status(self):
        """Return current recording status"""
        with recording_state['lock']:
            is_recording = recording_state['is_recording']

        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()

        response = {"recording": is_recording}
        self.wfile.write(json.dumps(response).encode())

    def handle_get_transcripts(self):
        """Return list of all transcripts"""
        transcripts = []

        # Read all JSON files from transcript directory
        for json_file in sorted(Path(TRANSCRIPT_DIR).glob("*.json"), reverse=True):
            try:
                with open(json_file, 'r') as f:
                    data = json.load(f)
                    # Convert timestamp string to ISO format for JS
                    # Try different timestamp formats (with/without microseconds)
                    timestamp_str = data['timestamp']
                    try:
                        ts = datetime.datetime.strptime(timestamp_str, "%Y%m%d_%H%M%S_%f")
                    except ValueError:
                        ts = datetime.datetime.strptime(timestamp_str, "%Y%m%d_%H%M%S")
                    data['timestamp'] = ts.isoformat()
                    transcripts.append(data)
            except Exception as e:
                print(f"Error reading {json_file}: {e}")
                continue

        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(transcripts).encode())

    def handle_sse(self):
        """Handle Server-Sent Events connection"""
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('Connection', 'keep-alive')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()

        # Register this client
        with sse_lock:
            sse_clients.append(self.wfile)

        try:
            # Send initial status
            with recording_state['lock']:
                status_data = {"recording": recording_state['is_recording']}

            status_msg = f"event: status\ndata: {json.dumps(status_data)}\n\n"
            self.wfile.write(status_msg.encode())
            self.wfile.flush()

            # Keep connection alive
            while True:
                import time
                time.sleep(30)
                # Send keepalive
                self.wfile.write(b": keepalive\n\n")
                self.wfile.flush()
        except:
            pass
        finally:
            # Unregister this client
            with sse_lock:
                if self.wfile in sse_clients:
                    sse_clients.remove(self.wfile)

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

        with recording_state['lock']:
            is_recording = recording_state['is_recording']
            stop_time = recording_state['stop_time']

            # Accept chunks if recording OR within 2 seconds after stop (grace period for in-flight chunks)
            should_buffer = is_recording
            if not is_recording and stop_time:
                elapsed = (datetime.datetime.now() - stop_time).total_seconds()
                should_buffer = elapsed < 2.0  # 2 second grace period

            if should_buffer:
                # Buffer this chunk
                recording_state['audio_chunks'].append({
                    'data': audio_data,
                    'sample_rate': sample_rate,
                    'bits_per_sample': bits_per_sample,
                    'channels': channels,
                    'device_id': device_id
                })

                # Print progress indicator (only if actively recording)
                if is_recording:
                    duration = len(audio_data) / (sample_rate * channels * (bits_per_sample // 8))
                    total_duration = sum(len(c['data']) for c in recording_state['audio_chunks']) / (sample_rate * channels * (bits_per_sample // 8))
                    print(f"\r🔴 Recording... {total_duration:.1f}s", end='', flush=True)

        # Always send success response quickly
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()

        response = {
            "status": "success",
            "recording": is_recording
        }
        self.wfile.write(json.dumps(response).encode())

    def transcribe_audio(self, wav_path):
        """Transcribe audio file using detected method"""
        if self.whisper_method == 'whisper-cpp':
            return transcribe_with_whisper_cpp(wav_path, self.whisper_binary)
        elif self.whisper_method == 'faster-whisper':
            return transcribe_with_faster_whisper(wav_path)
        elif self.whisper_method == 'openai-whisper':
            return transcribe_with_openai_whisper(wav_path)
        else:
            print("No transcription method available!")
            return None

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


def broadcast_sse(event, data):
    """Broadcast SSE message to all connected clients"""
    message = f"event: {event}\ndata: {json.dumps(data)}\n\n"
    with sse_lock:
        dead_clients = []
        for client in sse_clients:
            try:
                client.write(message.encode())
                client.flush()
            except:
                dead_clients.append(client)

        # Remove dead clients
        for client in dead_clients:
            sse_clients.remove(client)


def broadcast_status(recording):
    """Broadcast recording status to SSE clients"""
    broadcast_sse('status', {'recording': recording})


def process_recording(method, binary):
    """Process the buffered recording and transcribe it"""
    with recording_state['lock']:
        if not recording_state['audio_chunks']:
            print("\n⚠️  No audio data recorded!")
            return

        chunks = recording_state['audio_chunks']
        recording_state['audio_chunks'] = []

    print("\n\n" + "="*60)
    print(f"Processing {len(chunks)} audio chunks...")

    # Get metadata from first chunk
    first_chunk = chunks[0]
    device_id = first_chunk['device_id']
    sample_rate = first_chunk['sample_rate']
    bits_per_sample = first_chunk['bits_per_sample']
    channels = first_chunk['channels']

    # Concatenate all audio data
    combined_audio = b''.join(chunk['data'] for chunk in chunks)
    total_duration = len(combined_audio) / (sample_rate * channels * (bits_per_sample // 8))

    print(f"Total duration: {total_duration:.2f}s | Size: {len(combined_audio)} bytes")

    # Generate filename with timestamp
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    base_filename = f"{device_id}_{timestamp}"

    # Save as WAV
    wav_path = os.path.join(SAVE_DIR, f"{base_filename}.wav")
    with wave.open(wav_path, 'wb') as wav_file:
        wav_file.setnchannels(channels)
        wav_file.setsampwidth(bits_per_sample // 8)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(combined_audio)

    print(f"Saved: {wav_path}")

    # Transcribe
    print("Transcribing...")

    if method == 'whisper-cpp':
        transcript = transcribe_with_whisper_cpp(wav_path, binary)
    elif method == 'faster-whisper':
        transcript = transcribe_with_faster_whisper(wav_path)
    elif method == 'openai-whisper':
        transcript = transcribe_with_openai_whisper(wav_path)
    else:
        transcript = None

    if transcript:
        print(f"\n📝 Transcript: {transcript}\n")

        # Save transcript
        transcript_path = os.path.join(TRANSCRIPT_DIR, f"{base_filename}.txt")
        with open(transcript_path, 'w') as f:
            f.write(transcript)

        # Save JSON with metadata
        json_path = os.path.join(TRANSCRIPT_DIR, f"{base_filename}.json")
        metadata = {
            "device_id": device_id,
            "timestamp": timestamp,
            "duration_sec": total_duration,
            "sample_rate": sample_rate,
            "bits_per_sample": bits_per_sample,
            "channels": channels,
            "transcript": transcript
        }
        with open(json_path, 'w') as f:
            json.dump(metadata, f, indent=2)

        print(f"Saved transcript: {transcript_path}")

        # Broadcast to SSE clients with ISO timestamp for JS
        ts = datetime.datetime.strptime(timestamp, "%Y%m%d_%H%M%S")
        sse_metadata = metadata.copy()
        sse_metadata['timestamp'] = ts.isoformat()
        broadcast_sse('transcript', sse_metadata)
    else:
        print("⚠️  Transcription failed")

    print("="*60)
    print("\n⌨️  Press SPACE to start recording, Q to quit\n")


def keyboard_listener(method, binary):
    """Listen for keyboard input to control recording"""
    import sys, tty, termios

    print("\n⌨️  Press SPACE to start recording, Q to quit\n")

    old_settings = termios.tcgetattr(sys.stdin)
    try:
        tty.setcbreak(sys.stdin.fileno())
        while True:
            if select.select([sys.stdin], [], [], 0.1)[0]:
                char = sys.stdin.read(1)

                if char.lower() == 'q':
                    print("\n\nQuitting...")
                    os._exit(0)

                elif char == ' ':
                    with recording_state['lock']:
                        was_recording = recording_state['is_recording']
                        recording_state['is_recording'] = not was_recording

                        if not was_recording:
                            # Starting recording
                            recording_state['audio_chunks'] = []
                            recording_state['start_time'] = datetime.datetime.now()
                            recording_state['stop_time'] = None
                            print("\n🔴 Recording started! Press SPACE to stop.\n")
                            broadcast_status(True)
                        else:
                            # Stopping recording - set stop time for grace period
                            recording_state['stop_time'] = datetime.datetime.now()
                            print("\n\n⏹️  Recording stopped. Processing...\n")
                            broadcast_status(False)

                    if was_recording:
                        # Wait 2 seconds to catch in-flight chunks before processing
                        import time
                        time.sleep(2)
                        # Process the recording in the main thread
                        process_recording(method, binary)

    finally:
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    """Handle requests in a separate thread"""
    daemon_threads = True


def main():
    # Detect Whisper method
    method, binary = detect_whisper_method()

    if method is None:
        print("\n" + "="*60)
        print("ERROR: No Whisper implementation found!")
        print("="*60)
        print("\nPlease install one of the following:\n")
        print("1. whisper.cpp:")
        print("   cd ~/dev")
        print("   git clone https://github.com/ggerganov/whisper.cpp")
        print("   cd whisper.cpp")
        print("   make")
        print()
        print("2. faster-whisper (recommended):")
        print("   pip install faster-whisper")
        print()
        print("3. openai-whisper:")
        print("   pip install openai-whisper")
        print()
        return

    # Set class variables
    TranscriptionServer.whisper_method = method
    TranscriptionServer.whisper_binary = binary

    # Check model file exists
    if method == 'whisper-cpp' and not os.path.exists(WHISPER_MODEL_PATH):
        print(f"\nERROR: Model file not found: {WHISPER_MODEL_PATH}")
        print("Please update WHISPER_MODEL_PATH in this script.")
        return

    server_address = ('', 8000)
    httpd = ThreadedHTTPServer(server_address, TranscriptionServer)

    print("=" * 60)
    print("ESP32 Audio Transcription Server with START/STOP Control")
    print("=" * 60)
    print(f"Listening on: http://0.0.0.0:8000/audio")
    print(f"Transcription: {method}")
    if method == 'whisper-cpp':
        print(f"Binary: {binary}")
        print(f"Model: {WHISPER_MODEL_PATH}")
    print(f"Audio saved to: {os.path.abspath(SAVE_DIR)}")
    print(f"Transcripts saved to: {os.path.abspath(TRANSCRIPT_DIR)}")
    print("=" * 60)

    # Start HTTP server in a separate thread
    server_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    server_thread.start()

    # Run keyboard listener in main thread
    try:
        keyboard_listener(method, binary)
    except KeyboardInterrupt:
        print("\n\nShutting down server...")
        httpd.shutdown()


if __name__ == '__main__':
    main()
