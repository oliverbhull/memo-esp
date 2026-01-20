#!/usr/bin/env python3
"""
ESP32 Audio Transcription Server
Receives complete audio recordings from ESP32-S3, transcribes with Whisper.
Keyboard controls: SPACE = START/STOP recording, Q = Quit
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
import threading
import sys
import select

# Configuration
# For better transcription quality, use medium.en or large-v3 models
# Options: small.en (fast, less accurate), medium.en (balanced), large-v3 (best quality)
WHISPER_MODEL_PATH = os.path.expanduser("~/models/whisper/ggml-small.en-q5_1.bin")
WHISPER_CPP_PATH = os.path.expanduser("~/dev/whisper.cpp/build/bin/whisper-cli")
SAVE_AUDIO = True
SAVE_DIR = "received_audio"
TRANSCRIPT_DIR = "transcripts"

# Create directories
os.makedirs(SAVE_DIR, exist_ok=True)
os.makedirs(TRANSCRIPT_DIR, exist_ok=True)

# Per-device recording state
# Format: {'device_id': True/False}
recording_state = {}
recording_lock = threading.Lock()

# SSE clients
sse_clients = []
sse_lock = threading.Lock()

# Device tracking - tracks last seen time for each device
# Format: {'device_id': {'last_seen': timestamp, 'ip': ip_address}}
active_devices = {}
devices_lock = threading.Lock()
DEVICE_TIMEOUT_SECONDS = 10  # Consider device offline after 10 seconds of no status checks

# Global Whisper model instances (singleton pattern)
whisper_model_faster = None
whisper_model_openai = None
whisper_model_lock = threading.Lock()

# Transcription queue to prevent concurrent transcriptions
transcription_queue = []
transcription_queue_lock = threading.Lock()
transcription_worker_running = False


def detect_whisper_method():
    """Auto-detect available Whisper implementation"""
    whisper_cpp_paths = [
        os.path.expanduser("~/dev/whisper.cpp/build/bin/whisper-cli"),
        os.path.expanduser("~/dev/whisper.cpp/bin/whisper-cli"),
        os.path.expanduser("~/dev/whisper.cpp/build/bin/main"),
        "/usr/local/bin/whisper-cli",
        "/opt/homebrew/bin/whisper-cli",
    ]

    for path in whisper_cpp_paths:
        if os.path.exists(path):
            print(f"Found whisper.cpp at: {path}")
            return 'whisper-cpp', path

    try:
        import faster_whisper
        print("Found faster-whisper library")
        return 'faster-whisper', None
    except ImportError:
        pass

    try:
        import whisper
        print("Found openai-whisper library")
        return 'openai-whisper', None
    except ImportError:
        pass

    return None, None


def transcribe_with_whisper_cpp(wav_path, whisper_binary, timeout=60):
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

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)

        if result.returncode == 0:
            txt_file = "/tmp/whisper_output.txt"
            if os.path.exists(txt_file):
                with open(txt_file, 'r') as f:
                    text = f.read().strip()
                os.remove(txt_file)
                return text
        else:
            if result.stderr:
                print(f"whisper.cpp error: {result.stderr}")
        return None
    except subprocess.TimeoutExpired:
        print(f"Transcription timeout after {timeout}s")
        return None
    except Exception as e:
        print(f"Transcription error: {e}")
        return None


def get_faster_whisper_model():
    """Get or create faster-whisper model instance (singleton)"""
    global whisper_model_faster
    with whisper_model_lock:
        if whisper_model_faster is None:
            try:
                from faster_whisper import WhisperModel
                print("Loading faster-whisper model (this may take a moment)...")
                # Use medium.en for better accuracy, or small.en for speed
                # Options: tiny.en, base.en, small.en, medium.en, large-v3
                whisper_model_faster = WhisperModel("medium.en", device="cpu", compute_type="int8")
                print("✓ faster-whisper model loaded (medium.en for better accuracy)")
            except Exception as e:
                print(f"Error loading faster-whisper model: {e}")
                print("Falling back to small.en...")
                try:
                    whisper_model_faster = WhisperModel("small.en", device="cpu", compute_type="int8")
                    print("✓ faster-whisper model loaded (small.en fallback)")
                except Exception as e2:
                    print(f"Error loading fallback model: {e2}")
                    return None
        return whisper_model_faster


def transcribe_with_faster_whisper(wav_path):
    """Transcribe using faster-whisper library"""
    try:
        model = get_faster_whisper_model()
        if model is None:
            return None
        # Improved transcription parameters for better accuracy
        segments, info = model.transcribe(
            wav_path, 
            beam_size=5,
            language="en",
            condition_on_previous_text=True,
            initial_prompt="This is a voice recording with clear speech."
        )
        text = " ".join([segment.text for segment in segments])
        return text.strip()
    except Exception as e:
        print(f"Transcription error: {e}")
        return None


def get_openai_whisper_model():
    """Get or create openai-whisper model instance (singleton)"""
    global whisper_model_openai
    with whisper_model_lock:
        if whisper_model_openai is None:
            try:
                import whisper
                print("Loading openai-whisper model (this may take a moment)...")
                # Use medium.en for better accuracy, or small.en for speed
                # Options: tiny.en, base.en, small.en, medium.en, large-v3
                whisper_model_openai = whisper.load_model("medium.en")
                print("✓ openai-whisper model loaded (medium.en for better accuracy)")
            except Exception as e:
                print(f"Error loading openai-whisper model: {e}")
                print("Falling back to small.en...")
                try:
                    whisper_model_openai = whisper.load_model("small.en")
                    print("✓ openai-whisper model loaded (small.en fallback)")
                except Exception as e2:
                    print(f"Error loading fallback model: {e2}")
                    return None
        return whisper_model_openai


def transcribe_with_openai_whisper(wav_path):
    """Transcribe using openai-whisper library"""
    try:
        model = get_openai_whisper_model()
        if model is None:
            return None
        # Improved transcription parameters for better accuracy
        result = model.transcribe(
            wav_path,
            language="en",
            condition_on_previous_text=True,
            initial_prompt="This is a voice recording with clear speech.",
            beam_size=5
        )
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
        elif self.path.startswith('/devices'):
            self.handle_get_devices()
        elif self.path.startswith('/recording-status'):
            self.handle_get_recording_status()
        elif self.path.startswith('/transcripts'):
            self.handle_get_transcripts()
        elif self.path.startswith('/events'):
            self.handle_sse()
        elif self.path.startswith('/audio-file'):
            self.handle_audio_file()
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path.startswith('/audio'):
            self.handle_audio()
        elif self.path.startswith('/record/start'):
            self.handle_start_recording()
        elif self.path.startswith('/record/stop'):
            self.handle_stop_recording()
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
        """Return current recording status and track device - optimized for low latency"""
        # Parse query parameters to get device ID
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        device_id = params.get('device', [None])[0]

        # Track this device as active (quick operation)
        if device_id:
            client_ip = self.client_address[0]
            with devices_lock:
                active_devices[device_id] = {
                    'last_seen': datetime.datetime.now(),
                    'ip': client_ip
                }

        # Prepare response quickly - minimize lock time
        if not device_id:
            # Return all device statuses (for UI)
            with recording_lock:
                status = recording_state.copy()
            response_data = {"devices": status}
            response_json = json.dumps(response_data)
        else:
            # Get recording status for this specific device (for ESP32) - most common case
            with recording_lock:
                is_recording = recording_state.get(device_id, False)
            # Use simple string formatting for faster response
            response_json = '{"recording":' + ('true' if is_recording else 'false') + '}'

        # Send response with keep-alive for faster subsequent requests
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Connection', 'keep-alive')
        self.send_header('Content-Length', str(len(response_json)))
        self.end_headers()
        self.wfile.write(response_json.encode() if isinstance(response_json, str) else response_json)

    def handle_get_devices(self):
        """Return list of currently active devices"""
        now = datetime.datetime.now()
        devices = []

        with devices_lock:
            for device_id, info in list(active_devices.items()):
                seconds_since_seen = (now - info['last_seen']).total_seconds()

                # Only include devices seen within timeout window
                if seconds_since_seen <= DEVICE_TIMEOUT_SECONDS:
                    devices.append({
                        'device_id': device_id,
                        'ip': info['ip'],
                        'last_seen': info['last_seen'].isoformat(),
                        'seconds_ago': round(seconds_since_seen, 1)
                    })
                else:
                    # Remove stale devices
                    del active_devices[device_id]

        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(devices).encode())

    def handle_get_recording_status(self):
        """Return recording status for all devices"""
        with recording_lock:
            status = recording_state.copy()

        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(status).encode())

    def handle_get_transcripts(self):
        """Return list of all transcripts"""
        transcripts = []

        # Read all JSON files from transcript directory
        for json_file in sorted(Path(TRANSCRIPT_DIR).glob("*.json"), reverse=True):
            try:
                with open(json_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    
                    # Validate required fields
                    if 'timestamp' not in data:
                        print(f"Warning: {json_file} missing timestamp, skipping")
                        continue
                    
                    # Convert timestamp string to ISO format for JS
                    timestamp_str = data['timestamp']
                    try:
                        # Try with microseconds first
                        ts = datetime.datetime.strptime(timestamp_str, "%Y%m%d_%H%M%S_%f")
                    except ValueError:
                        try:
                            # Fall back to without microseconds
                            ts = datetime.datetime.strptime(timestamp_str, "%Y%m%d_%H%M%S")
                        except ValueError:
                            # If timestamp is already ISO format, try parsing it
                            try:
                                ts = datetime.datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
                            except:
                                print(f"Warning: Could not parse timestamp '{timestamp_str}' in {json_file}, using current time")
                                ts = datetime.datetime.now()
                    
                    data['timestamp'] = ts.isoformat()
                    
                    # Clean up any NaN or Infinity values that might break JSON
                    data = clean_json_data(data)
                    
                    transcripts.append(data)
            except json.JSONDecodeError as e:
                print(f"Error: Invalid JSON in {json_file}: {e}")
                continue
            except Exception as e:
                print(f"Error reading {json_file}: {e}")
                import traceback
                traceback.print_exc()
                continue

        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        
        try:
            response_json = json.dumps(transcripts, allow_nan=False)
            self.wfile.write(response_json.encode('utf-8'))
        except (ValueError, TypeError) as e:
            print(f"Error serializing transcripts to JSON: {e}")
            # Send empty array as fallback
            self.send_response(500)
            self.end_headers()
            self.wfile.write(json.dumps({"error": "Failed to serialize transcripts"}).encode('utf-8'))
    

    def handle_audio_file(self):
        """Serve audio files for playback"""
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        filepath = params.get('path', [None])[0]

        if not filepath:
            self.send_response(400)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({"error": "path parameter required"}).encode())
            return

        # Security: ensure path is within allowed directories
        # Normalize path to prevent directory traversal
        # Remove any leading slashes or directory traversal attempts
        filepath = filepath.lstrip('/').lstrip('\\')
        
        # Remove any .. or . components for security
        if '..' in filepath or filepath.startswith('.'):
            self.send_response(403)
            self.end_headers()
            return
        
        # Handle paths that may already include SAVE_DIR or be just filenames
        save_dir_abs = os.path.abspath(SAVE_DIR)
        save_dir_name = os.path.basename(SAVE_DIR)
        original_filepath = filepath
        
        # If path already starts with SAVE_DIR name, extract just the filename
        if filepath.startswith(save_dir_name + '/'):
            filepath = filepath[len(save_dir_name) + 1:]
        elif filepath.startswith(SAVE_DIR + '/'):
            filepath = filepath[len(SAVE_DIR) + 1:]
        elif filepath.startswith(save_dir_abs + '/'):
            filepath = filepath[len(save_dir_abs) + 1:]
        
        # Join with SAVE_DIR and normalize
        normalized_path = os.path.join(SAVE_DIR, filepath)
        normalized_path = os.path.normpath(normalized_path)
        
        # Get absolute paths for comparison
        file_path_abs = os.path.abspath(normalized_path)
        
        # Ensure the file is within SAVE_DIR (security check)
        if not file_path_abs.startswith(save_dir_abs):
            print(f"Security check failed: {file_path_abs} not in {save_dir_abs}")
            self.send_response(403)
            self.end_headers()
            return

        # Check if file exists
        if not os.path.exists(normalized_path):
            # Try alternative: just the filename (in case path was malformed)
            filename_only = os.path.basename(filepath)
            alt_path = os.path.join(SAVE_DIR, filename_only)
            
            print(f"Audio file not found at: {normalized_path}")
            print(f"  Original request: {original_filepath}")
            print(f"  Processed path: {filepath}")
            print(f"  Trying filename only: {alt_path}")
            
            if os.path.exists(alt_path):
                normalized_path = alt_path
                file_path_abs = os.path.abspath(normalized_path)
                print(f"  ✓ Found at alternative path: {normalized_path}")
            else:
                # List some files in SAVE_DIR for debugging
                try:
                    files_in_dir = os.listdir(SAVE_DIR)[:5]  # First 5 files
                    print(f"  Sample files in {SAVE_DIR}: {files_in_dir}")
                except:
                    pass
                self.send_response(404)
                self.end_headers()
                return

        # Serve the file
        try:
            with open(normalized_path, 'rb') as f:
                content = f.read()
            
            # Determine content type
            if normalized_path.endswith('.wav'):
                content_type = 'audio/wav'
            elif normalized_path.endswith('.mp3'):
                content_type = 'audio/mpeg'
            else:
                content_type = 'application/octet-stream'

            self.send_response(200)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(content)))
            self.send_header('Access-Control-Allow-Origin', '*')
            self.send_header('Cache-Control', 'public, max-age=3600')
            self.end_headers()
            self.wfile.write(content)
        except Exception as e:
            print(f"Error serving audio file: {e}")
            self.send_response(500)
            self.end_headers()

    def handle_start_recording(self):
        """Start recording for a specific device"""
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        device_id = params.get('device', [None])[0]

        if not device_id:
            self.send_response(400)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({"error": "device parameter required"}).encode())
            return

        with recording_lock:
            recording_state[device_id] = True

        print(f"\n🔴 RECORDING STARTED for device: {device_id}")
        print("="*60 + "\n")

        # Broadcast status update
        broadcast_sse('device_status', {
            'device_id': device_id,
            'recording': True
        })

        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps({"status": "started", "device_id": device_id}).encode())

    def handle_stop_recording(self):
        """Stop recording for a specific device"""
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        device_id = params.get('device', [None])[0]

        if not device_id:
            self.send_response(400)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({"error": "device parameter required"}).encode())
            return

        with recording_lock:
            recording_state[device_id] = False

        print(f"\n⏹️  RECORDING STOPPED for device: {device_id}")
        print("="*60 + "\n")

        # Broadcast status update
        broadcast_sse('device_status', {
            'device_id': device_id,
            'recording': False
        })

        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps({"status": "stopped", "device_id": device_id}).encode())

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
            # Send initial status for all devices
            with recording_lock:
                status_data = {"devices": recording_state.copy()}

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
        """Handle complete audio recording upload"""
        # Parse query parameters
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        device_id = params.get('device', ['unknown'])[0]
        sample_rate = int(params.get('rate', [16000])[0])
        bits_per_sample = int(params.get('bits', [16])[0])
        channels = int(params.get('channels', [1])[0])

        # Read complete audio data
        content_length = int(self.headers['Content-Length'])
        audio_data = self.rfile.read(content_length)
        
        # Extract audio quality metrics from headers
        # Note: HTTP headers are case-insensitive, but Python's BaseHTTPRequestHandler
        # may store them with different casing. Use case-insensitive lookup.
        audio_quality = {}
        
        # Debug: Print all headers to see what we're receiving
        print(f"\n📊 Received headers for {device_id}:")
        quality_headers = [h for h in self.headers.keys() if 'audio' in h.lower() or 'x-' in h.lower()]
        if quality_headers:
            for header in quality_headers:
                print(f"  {header}: {self.headers[header]}")
        else:
            print("  No quality headers found. Available headers:", list(self.headers.keys())[:10])
        
        try:
            # Case-insensitive header lookup
            def get_header(name):
                """Get header value case-insensitively"""
                name_lower = name.lower()
                for key, value in self.headers.items():
                    if key.lower() == name_lower:
                        return value
                return None
            
            avg_db = get_header('X-Audio-AvgDb')
            if avg_db:
                audio_quality['avg_db'] = float(avg_db)
                
            max_db = get_header('X-Audio-MaxDb')
            if max_db:
                audio_quality['max_db'] = float(max_db)
                
            min_db = get_header('X-Audio-MinDb')
            if min_db:
                audio_quality['min_db'] = float(min_db)
                
            clip_count = get_header('X-Audio-ClipCount')
            if clip_count:
                audio_quality['clip_count'] = int(clip_count)
                
            silence_chunks = get_header('X-Audio-SilenceChunks')
            if silence_chunks:
                audio_quality['silence_chunks'] = int(silence_chunks)
                
            i2s_errors = get_header('X-Audio-I2SErrors')
            if i2s_errors:
                audio_quality['i2s_errors'] = int(i2s_errors)
                
            total_chunks = get_header('X-Audio-TotalChunks')
            if total_chunks:
                audio_quality['total_chunks'] = int(total_chunks)
                
            if audio_quality:
                print(f"✓ Extracted audio quality metrics: {audio_quality}")
            else:
                print("⚠️  No audio quality metrics found in headers")
                
        except (ValueError, KeyError, TypeError) as e:
            print(f"⚠️  Warning: Could not parse audio quality metrics: {e}")
            import traceback
            traceback.print_exc()

        # Send immediate response
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        response = {"status": "success", "bytes_received": len(audio_data)}
        self.wfile.write(json.dumps(response).encode())

        # Add to transcription queue instead of processing directly
        if len(audio_data) > 0:
            with transcription_queue_lock:
                transcription_queue.append({
                    'audio_data': audio_data,
                    'device_id': device_id,
                    'sample_rate': sample_rate,
                    'bits_per_sample': bits_per_sample,
                    'channels': channels,
                    'audio_quality': audio_quality
                })

    def process_recording(self, audio_data, device_id, sample_rate, bits_per_sample, channels):
        """Process and transcribe the recording (delegates to standalone function)"""
        process_recording_standalone(audio_data, device_id, sample_rate, bits_per_sample, channels)

    def save_wav(self, filename, pcm_data, sample_rate, channels, bits_per_sample):
        """Convert raw PCM to WAV file"""
        save_wav_file(filename, pcm_data, sample_rate, channels, bits_per_sample)

    def log_message(self, format, *args):
        # Suppress default request logging
        pass


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    """Handle requests in separate threads"""
    daemon_threads = True

    def handle_error(self, request, client_address):
        """Override to suppress harmless connection reset errors from browser"""
        import sys
        exc_type, exc_value = sys.exc_info()[:2]
        # Suppress ConnectionResetError and BrokenPipeError (browser SSE disconnects)
        if isinstance(exc_value, (ConnectionResetError, BrokenPipeError)):
            return
        # Print other errors normally
        super().handle_error(request, client_address)


def broadcast_sse(event, data):
    """Broadcast SSE message to all connected clients"""
    try:
        # Clean data to prevent JSON serialization errors
        clean_data = clean_json_data(data)
        message = f"event: {event}\ndata: {json.dumps(clean_data, allow_nan=False)}\n\n"
        with sse_lock:
            dead_clients = []
            for client in sse_clients:
                try:
                    client.write(message.encode('utf-8'))
                    client.flush()
                except:
                    dead_clients.append(client)

            # Remove dead clients
            for client in dead_clients:
                sse_clients.remove(client)
    except Exception as e:
        print(f"Error broadcasting SSE message: {e}")
        import traceback
        traceback.print_exc()


def process_recording_standalone(audio_data, device_id, sample_rate, bits_per_sample, channels, audio_quality=None):
    """Standalone function to process and transcribe a recording"""
    print("\n\n⏹️  Recording stopped. Processing...")
    print("\n" + "=" * 60)

    duration = len(audio_data) / (sample_rate * channels * (bits_per_sample // 8))
    print(f"Received audio:")
    print(f"  Duration: {duration:.2f}s")
    print(f"  Size: {len(audio_data)} bytes")
    print(f"  Device: {device_id}")
    
    # Display audio quality metrics if available
    if audio_quality:
        print(f"  Audio Quality (from device):")
        if 'avg_db' in audio_quality and audio_quality['avg_db'] is not None:
            try:
                print(f"    Avg Level: {float(audio_quality['avg_db']):.1f} dB")
            except (ValueError, TypeError):
                print(f"    Avg Level: N/A (invalid value)")
        if 'max_db' in audio_quality and audio_quality['max_db'] is not None:
            try:
                print(f"    Max Level: {float(audio_quality['max_db']):.1f} dB")
            except (ValueError, TypeError):
                pass
        if 'min_db' in audio_quality and audio_quality['min_db'] is not None:
            try:
                print(f"    Min Level: {float(audio_quality['min_db']):.1f} dB")
            except (ValueError, TypeError):
                pass
        if 'clip_count' in audio_quality:
            print(f"    Clipping Events: {audio_quality['clip_count']}")
        if 'silence_chunks' in audio_quality and 'total_chunks' in audio_quality:
            if audio_quality['total_chunks'] > 0:
                silence_pct = (audio_quality['silence_chunks'] / audio_quality['total_chunks'] * 100)
                print(f"    Silence: {silence_pct:.1f}% ({audio_quality['silence_chunks']}/{audio_quality['total_chunks']} chunks)")
        if 'i2s_errors' in audio_quality:
            print(f"    I2S Errors: {audio_quality['i2s_errors']}")

    # Generate timestamp filename
    timestamp = datetime.datetime.now()
    timestamp_str = timestamp.strftime("%Y%m%d_%H%M%S")
    base_filename = f"{device_id}_{timestamp_str}"

    # Analyze and save as WAV
    wav_path = os.path.join(SAVE_DIR, f"{base_filename}.wav")
    audio_analysis = save_wav_file(wav_path, audio_data, sample_rate, channels, bits_per_sample)
    print(f"Saved: {wav_path}")
    
    # Add server-side analysis to metadata
    if audio_analysis and 'error' not in audio_analysis:
        if not audio_quality:
            audio_quality = {}
        audio_quality['server_analysis'] = {
            'rms': audio_analysis.get('rms'),
            'db_level': audio_analysis.get('db_level'),
            'zero_percentage': audio_analysis.get('zero_percentage'),
            'clip_percentage': audio_analysis.get('clip_percentage'),
            'data_integrity': audio_analysis.get('data_integrity'),
            'issues': audio_analysis.get('issues', [])
        }

    # Transcribe
    print("Transcribing...")
    transcript = transcribe_audio_file(wav_path)

    if transcript:
        print(f"\n📝 Transcript: {transcript}")

        # Save transcript as text
        txt_path = os.path.join(TRANSCRIPT_DIR, f"{base_filename}.txt")
        with open(txt_path, 'w') as f:
            f.write(transcript)
        print(f"\nSaved transcript: {txt_path}")

        # Calculate network quality metrics
        network_quality = {}
        # Note: Upload speed would need to be tracked separately if we want real-time metrics
        # For now, we can estimate based on file size and duration
        
        # Save metadata as JSON
        json_path = os.path.join(TRANSCRIPT_DIR, f"{base_filename}.json")
        metadata = {
            'timestamp': timestamp_str,
            'device_id': device_id,
            'duration': duration,
            'transcript': transcript,
            'audio_file': wav_path,
            'sample_rate': sample_rate,
            'bits_per_sample': bits_per_sample,
            'channels': channels
        }
        
        # Add quality metrics to metadata
        if audio_quality:
            metadata['audio_quality'] = audio_quality
            
            # Calculate quality score (0-100)
            quality_score = 100.0
            
            # Penalize for clipping
            if 'clip_count' in audio_quality and audio_quality['clip_count']:
                quality_score -= min(audio_quality['clip_count'] * 5, 30)
            
            # Penalize for too much silence
            if 'silence_chunks' in audio_quality and 'total_chunks' in audio_quality:
                if audio_quality['total_chunks'] > 0:
                    silence_pct = audio_quality['silence_chunks'] / audio_quality['total_chunks'] * 100
                    if silence_pct > 50:
                        quality_score -= 20
                    elif silence_pct > 30:
                        quality_score -= 10
            
            # Penalize for I2S errors
            if 'i2s_errors' in audio_quality and audio_quality['i2s_errors']:
                quality_score -= min(audio_quality['i2s_errors'] * 10, 40)
            
            # Penalize for audio level issues
            if 'avg_db' in audio_quality and audio_quality['avg_db'] is not None:
                try:
                    avg_db_val = float(audio_quality['avg_db'])
                    if avg_db_val < -30:  # Too quiet
                        quality_score -= 15
                    elif avg_db_val > -6:  # Too loud (near clipping)
                        quality_score -= 10
                except (ValueError, TypeError):
                    pass  # Skip if invalid
            
            # Penalize based on server analysis
            if 'server_analysis' in audio_quality:
                server_analysis = audio_quality['server_analysis']
                if server_analysis.get('data_integrity') != 'good':
                    quality_score -= 20
                if server_analysis.get('issues'):
                    quality_score -= len(server_analysis['issues']) * 5
                if server_analysis.get('zero_percentage', 0) > 50:
                    quality_score -= 15
            
            quality_score = max(0, min(100, quality_score))
            # Ensure quality_score is a valid number (not NaN or Inf)
            import math
            if math.isnan(quality_score) or math.isinf(quality_score):
                quality_score = 0.0
            metadata['quality_score'] = round(float(quality_score), 1)
        
        # Clean metadata before saving to ensure no NaN/Inf values
        metadata = clean_json_data(metadata)
        
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(metadata, f, indent=2, ensure_ascii=False, allow_nan=False)

        # Broadcast new transcript to all connected clients
        # Ensure timestamp is ISO format and clean any NaN values
        broadcast_data = metadata.copy()
        broadcast_data['timestamp'] = timestamp.isoformat()
        
        # Clean broadcast data to prevent JSON serialization errors
        broadcast_data = clean_json_data(broadcast_data)
        broadcast_sse('transcript', broadcast_data)
    else:
        print("\n⚠️  Transcription failed")

    print("=" * 60)
    print("\n⌨️  Press SPACE to start recording, Q to quit\n")


def clean_json_data(data):
    """Remove NaN and Infinity values from data structure"""
    import math
    if isinstance(data, dict):
        return {k: clean_json_data(v) for k, v in data.items()}
    elif isinstance(data, list):
        return [clean_json_data(item) for item in data]
    elif isinstance(data, float):
        if math.isnan(data) or math.isinf(data):
            return None
        return data
    else:
        return data


def analyze_audio_quality(pcm_data, sample_rate, channels, bits_per_sample):
    """Analyze audio data to detect quality issues"""
    import struct
    import math
    
    if len(pcm_data) == 0:
        return {'error': 'Empty audio data'}
    
    # Convert bytes to samples
    bytes_per_sample = bits_per_sample // 8
    num_samples = len(pcm_data) // bytes_per_sample
    
    if num_samples == 0:
        return {'error': 'No samples in audio data'}
    
    samples = []
    sum_squares = 0
    clip_count = 0
    zero_samples = 0
    max_val = 0
    min_val = 0
    
    # Unpack samples based on bit depth
    if bits_per_sample == 16:
        format_char = 'h'  # signed short
        max_amplitude = 32767
        min_amplitude = -32768
    elif bits_per_sample == 8:
        format_char = 'b'  # signed byte
        max_amplitude = 127
        min_amplitude = -128
    else:
        return {'error': f'Unsupported bit depth: {bits_per_sample}'}
    
    try:
        for i in range(0, len(pcm_data) - bytes_per_sample + 1, bytes_per_sample):
            sample_bytes = pcm_data[i:i+bytes_per_sample]
            if len(sample_bytes) == bytes_per_sample:
                sample = struct.unpack(f'<{format_char}', sample_bytes)[0]
                samples.append(sample)
                
                # Track statistics
                sum_squares += sample * sample
                if sample == 0:
                    zero_samples += 1
                if abs(sample) > abs(max_val):
                    max_val = sample
                if abs(sample) < abs(min_val) or min_val == 0:
                    min_val = sample
                
                # Detect clipping
                if abs(sample) >= max_amplitude * 0.95:
                    clip_count += 1
    except Exception as e:
        return {'error': f'Error unpacking samples: {e}'}
    
    if len(samples) == 0:
        return {'error': 'No valid samples extracted'}
    
    # Calculate DC offset (mean value)
    dc_offset = sum(samples) / len(samples)
    
    # Calculate RMS and dB (using AC component, removing DC offset)
    ac_sum_squares = sum((s - dc_offset) ** 2 for s in samples)
    rms = math.sqrt(ac_sum_squares / len(samples))
    if rms > 0:
        db_level = 20.0 * math.log10(rms / max_amplitude)
    else:
        db_level = -100.0
    
    # Calculate statistics
    zero_percentage = (zero_samples / len(samples)) * 100
    clip_percentage = (clip_count / len(samples)) * 100
    sample_range = max_val - min_val
    
    # Detect potential issues
    issues = []
    if abs(dc_offset) > max_amplitude * 0.1:  # DC offset > 10% of max
        issues.append(f'Large DC offset: {dc_offset:.0f} (should be near 0)')
    if sample_range < 100:  # Very narrow range suggests DC bias or no signal
        issues.append(f'Very narrow sample range: {sample_range} (suggests DC bias or no audio)')
    if zero_percentage > 50:
        issues.append(f'High zero samples: {zero_percentage:.1f}%')
    if clip_percentage > 1:
        issues.append(f'Clipping detected: {clip_percentage:.1f}%')
    if db_level < -40:
        issues.append(f'Very quiet audio: {db_level:.1f} dB')
    if len(samples) < (sample_rate * 0.1):  # Less than 100ms
        issues.append(f'Very short audio: {len(samples)/sample_rate:.2f}s')
    
    return {
        'num_samples': len(samples),
        'duration_sec': len(samples) / sample_rate,
        'rms': round(rms, 2),
        'db_level': round(db_level, 1),
        'dc_offset': round(dc_offset, 1),
        'sample_range': sample_range,
        'max_amplitude': max_val,
        'min_amplitude': min_val,
        'zero_samples': zero_samples,
        'zero_percentage': round(zero_percentage, 1),
        'clip_count': clip_count,
        'clip_percentage': round(clip_percentage, 1),
        'issues': issues,
        'data_integrity': 'good' if len(issues) == 0 else 'issues_detected'
    }


def remove_dc_offset(pcm_data, bits_per_sample):
    """Remove DC offset from PCM audio data"""
    import struct
    
    if len(pcm_data) == 0:
        return pcm_data
    
    bytes_per_sample = bits_per_sample // 8
    
    # Unpack all samples
    if bits_per_sample == 16:
        samples = list(struct.unpack(f'<{len(pcm_data)//2}h', pcm_data))
    elif bits_per_sample == 8:
        samples = list(struct.unpack(f'<{len(pcm_data)}b', pcm_data))
    else:
        return pcm_data  # Can't process
    
    if len(samples) == 0:
        return pcm_data
    
    # Calculate DC offset
    dc_offset = sum(samples) / len(samples)
    
    # Remove DC offset
    corrected_samples = [int(s - dc_offset) for s in samples]
    
    # Apply simple high-pass filter to remove low-frequency noise (DC and near-DC)
    # First-order IIR high-pass filter: y[n] = x[n] - x[n-1] + α * y[n-1]
    # α = 0.95 gives ~50Hz cutoff at 16kHz sample rate
    alpha = 0.95
    filtered_samples = [0] * len(corrected_samples)
    filtered_samples[0] = corrected_samples[0]
    
    for i in range(1, len(corrected_samples)):
        filtered_samples[i] = int(corrected_samples[i] - corrected_samples[i-1] + alpha * filtered_samples[i-1])
    
    # Clamp to valid range
    if bits_per_sample == 16:
        filtered_samples = [max(-32768, min(32767, s)) for s in filtered_samples]
        corrected_data = struct.pack(f'<{len(filtered_samples)}h', *filtered_samples)
    else:  # 8-bit
        filtered_samples = [max(-128, min(127, s)) for s in filtered_samples]
        corrected_data = struct.pack(f'<{len(filtered_samples)}b', *filtered_samples)
    
    return corrected_data


def save_wav_file(filename, pcm_data, sample_rate, channels, bits_per_sample):
    """Convert raw PCM to WAV file with integrity checking and DC offset removal"""
    # Analyze audio before processing
    analysis = analyze_audio_quality(pcm_data, sample_rate, channels, bits_per_sample)
    
    if 'error' in analysis:
        print(f"⚠️  Audio analysis error: {analysis['error']}")
    else:
        print(f"  Audio Analysis:")
        print(f"    Samples: {analysis['num_samples']}, Duration: {analysis['duration_sec']:.2f}s")
        print(f"    RMS: {analysis['rms']}, dB Level: {analysis['db_level']:.1f} dB")
        print(f"    DC Offset: {analysis.get('dc_offset', 0):.1f} (should be near 0)")
        print(f"    Sample Range: {analysis.get('sample_range', 0)} (larger is better)")
        print(f"    Zero samples: {analysis['zero_percentage']:.1f}%, Clipping: {analysis['clip_percentage']:.1f}%")
        if analysis['issues']:
            print(f"    ⚠️  Issues detected: {', '.join(analysis['issues'])}")
    
    # Always remove DC offset (PDM mics often have DC bias)
    dc_offset = analysis.get('dc_offset', 0) if 'error' not in analysis else 0
    if abs(dc_offset) > 10:  # Remove any significant DC offset
        print(f"  Removing DC offset of {dc_offset:.1f}...")
        pcm_data = remove_dc_offset(pcm_data, bits_per_sample)
        # Re-analyze after DC removal
        analysis_after = analyze_audio_quality(pcm_data, sample_rate, channels, bits_per_sample)
        if 'error' not in analysis_after:
            print(f"  ✓ After DC removal: RMS: {analysis_after['rms']}, dB: {analysis_after['db_level']:.1f} dB, Range: {analysis_after.get('sample_range', 0)}")
            analysis['dc_offset_removed'] = True
            analysis['dc_offset_original'] = round(dc_offset, 1)
            # Update analysis with corrected values
            analysis.update(analysis_after)
    
    # Save WAV file
    try:
        with wave.open(filename, 'wb') as wav_file:
            wav_file.setnchannels(channels)
            wav_file.setsampwidth(bits_per_sample // 8)
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(pcm_data)
        
        # Verify file was written correctly
        file_size = os.path.getsize(filename)
        expected_size = 44 + len(pcm_data)  # WAV header (44 bytes) + data
        if abs(file_size - expected_size) > 100:  # Allow some tolerance
            print(f"⚠️  WAV file size mismatch: expected ~{expected_size} bytes, got {file_size} bytes")
        
        return analysis
    except Exception as e:
        print(f"⚠️  Error saving WAV file: {e}")
        raise


def transcribe_audio_file(wav_path):
    """Transcribe audio file using detected method"""
    # Increase timeout for longer audio files
    if TranscriptionServer.whisper_method == 'whisper-cpp':
        # For whisper-cpp, increase timeout based on file size
        try:
            file_size_mb = os.path.getsize(wav_path) / (1024 * 1024)
            timeout = max(30, int(file_size_mb * 10))  # At least 30s, more for larger files
            return transcribe_with_whisper_cpp(wav_path, TranscriptionServer.whisper_binary, timeout)
        except Exception as e:
            print(f"Transcription error: {e}")
            return None
    elif TranscriptionServer.whisper_method == 'faster-whisper':
        return transcribe_with_faster_whisper(wav_path)
    elif TranscriptionServer.whisper_method == 'openai-whisper':
        return transcribe_with_openai_whisper(wav_path)
    else:
        print("No transcription method available!")
        return None


def transcription_worker():
    """Worker thread that processes transcription queue sequentially"""
    global transcription_worker_running
    transcription_worker_running = True
    
    while True:
        item = None
        with transcription_queue_lock:
            if transcription_queue:
                item = transcription_queue.pop(0)
        
        if item is None:
            import time
            time.sleep(0.1)  # Wait a bit if queue is empty
            continue
        
        # Process the transcription
        process_recording_standalone(
            item['audio_data'],
            item['device_id'],
            item['sample_rate'],
            item['bits_per_sample'],
            item['channels'],
            item.get('audio_quality', None)
        )


def keyboard_listener(server_addr):
    """Listen for keyboard commands to control recording"""
    import time
    print("\n⌨️  Press SPACE to start recording, Q to quit\n")

    last_key_time = 0
    DEBOUNCE_DELAY = 0.3  # 300ms debounce to prevent double-triggers

    while True:
        # Check for keyboard input (Unix/Mac)
        if select.select([sys.stdin], [], [], 0.1)[0]:
            key = sys.stdin.read(1)
            current_time = time.time()

            if key == ' ':
                # Debounce: ignore if pressed too soon after last press
                if current_time - last_key_time < DEBOUNCE_DELAY:
                    continue

                last_key_time = current_time

                # Flush any remaining buffered input
                while select.select([sys.stdin], [], [], 0)[0]:
                    sys.stdin.read(1)

                # Toggle recording for all active devices
                with devices_lock:
                    device_ids = list(active_devices.keys())
                
                with recording_lock:
                    # Check if any device is recording
                    any_recording = any(recording_state.get(did, False) for did in device_ids)
                    
                    # Toggle all devices
                    new_state = not any_recording
                    for device_id in device_ids:
                        recording_state[device_id] = new_state
                        # Broadcast status update
                        broadcast_sse('device_status', {
                            'device_id': device_id,
                            'recording': new_state
                        })
                
                if new_state:
                    print("\n" + "="*60)
                    print(f"🔴 RECORDING STARTED for {len(device_ids)} device(s)")
                    print("="*60)
                    print("Press SPACE again to STOP recording")
                    print("="*60 + "\n")
                else:
                    print("\n" + "="*60)
                    print(f"⏹️  RECORDING STOPPED for {len(device_ids)} device(s)")
                    print("="*60 + "\n")

            elif key.lower() == 'q':
                print("\nShutting down server...")
                os._exit(0)


def get_local_ip_addresses():
    """Get all local IP addresses of this machine"""
    import socket
    ip_addresses = []
    
    # Get hostname
    hostname = socket.gethostname()
    
    # Get all IP addresses
    try:
        # Get primary IP
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))  # Connect to external address to determine primary interface
        primary_ip = s.getsockname()[0]
        s.close()
        ip_addresses.append(primary_ip)
    except Exception:
        pass
    
    # Get all interface IPs
    try:
        import socket
        hostname = socket.gethostname()
        local_ip = socket.gethostbyname(hostname)
        if local_ip not in ip_addresses:
            ip_addresses.append(local_ip)
    except Exception:
        pass
    
    # Try to get all network interfaces
    try:
        import netifaces
        for interface in netifaces.interfaces():
            addrs = netifaces.ifaddresses(interface)
            if netifaces.AF_INET in addrs:
                for addr_info in addrs[netifaces.AF_INET]:
                    ip = addr_info.get('addr')
                    if ip and ip != '127.0.0.1' and ip not in ip_addresses:
                        ip_addresses.append(ip)
    except ImportError:
        # netifaces not available, try alternative method
        try:
            import subprocess
            if sys.platform == "darwin":  # macOS
                result = subprocess.run(['ifconfig'], capture_output=True, text=True)
                import re
                for line in result.stdout.split('\n'):
                    match = re.search(r'inet (\d+\.\d+\.\d+\.\d+)', line)
                    if match:
                        ip = match.group(1)
                        if ip != '127.0.0.1' and ip not in ip_addresses:
                            ip_addresses.append(ip)
            elif sys.platform.startswith('linux'):  # Linux
                result = subprocess.run(['hostname', '-I'], capture_output=True, text=True)
                for ip in result.stdout.strip().split():
                    if ip != '127.0.0.1' and ip not in ip_addresses:
                        ip_addresses.append(ip)
        except Exception:
            pass
    
    return ip_addresses


def main():
    # Detect Whisper method
    whisper_method, whisper_binary = detect_whisper_method()

    if not whisper_method:
        print("ERROR: No Whisper implementation found!")
        print("Install one of:")
        print("  - whisper.cpp: https://github.com/ggerganov/whisper.cpp")
        print("  - faster-whisper: pip install faster-whisper")
        print("  - openai-whisper: pip install openai-whisper")
        return

    # Set class variables
    TranscriptionServer.whisper_method = whisper_method
    TranscriptionServer.whisper_binary = whisper_binary

    # Start HTTP server
    server_address = ('0.0.0.0', 8000)
    httpd = ThreadedHTTPServer(server_address, TranscriptionServer)

    # Get local IP addresses
    local_ips = get_local_ip_addresses()

    print("=" * 60)
    print("ESP32 Audio Transcription Server with START/STOP Control")
    print("=" * 60)
    print(f"Server listening on: 0.0.0.0:8000")
    if local_ips:
        print(f"Accessible at:")
        for ip in local_ips:
            print(f"  - http://{ip}:8000/audio")
            print(f"  - http://{ip}:8000/status")
    else:
        print("  (Could not determine local IP addresses)")
    print(f"\nTranscription: {whisper_method}")
    if whisper_binary:
        print(f"Binary: {whisper_binary}")
        print(f"Model: {WHISPER_MODEL_PATH}")
    print(f"Audio saved to: {SAVE_DIR}")
    print(f"Transcripts saved to: {TRANSCRIPT_DIR}")
    print("=" * 60)

    # Pre-load Whisper models if using library-based methods
    if whisper_method == 'faster-whisper':
        print("Pre-loading faster-whisper model...")
        get_faster_whisper_model()
    elif whisper_method == 'openai-whisper':
        print("Pre-loading openai-whisper model...")
        get_openai_whisper_model()

    # Start transcription worker thread
    transcription_thread = threading.Thread(
        target=transcription_worker,
        daemon=True
    )
    transcription_thread.start()

    # Start keyboard listener in separate thread
    keyboard_thread = threading.Thread(
        target=keyboard_listener,
        args=(server_address,),
        daemon=True
    )
    keyboard_thread.start()

    # Start server
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down server...")
        httpd.shutdown()


if __name__ == "__main__":
    # Set terminal to raw mode for immediate key detection
    import termios
    import tty

    old_settings = termios.tcgetattr(sys.stdin)
    try:
        tty.setcbreak(sys.stdin.fileno())
        main()
    finally:
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)
