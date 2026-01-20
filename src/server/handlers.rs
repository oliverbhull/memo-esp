use crate::server::audio::{analyze_audio_quality, save_wav_file};
use crate::server::state::{ServerState, Transcript};
use axum::{
    body::Bytes,
    extract::{Query, State, ConnectInfo},
    http::{HeaderMap, StatusCode},
    response::{IntoResponse, Sse},
    Json,
};
use std::net::SocketAddr;
use chrono::Utc;
use serde::Deserialize;
use std::collections::HashMap;
use std::fs;
use std::path::PathBuf;
use std::sync::Arc;
use tokio::sync::mpsc;
use tokio_stream::{wrappers::UnboundedReceiverStream, Stream, StreamExt};

#[derive(Deserialize)]
pub struct AudioQuery {
    device: String,
    rate: u32,
    bits: u16,
    channels: u16,
}


/// Handle POST /audio - receive audio from ESP32
pub async fn handle_audio(
    State(state): State<Arc<ServerState>>,
    Query(params): Query<AudioQuery>,
    headers: HeaderMap,
    body: Bytes,
) -> Result<impl IntoResponse, StatusCode> {
    let device_id = params.device.clone();
    let sample_rate = params.rate;
    let bits_per_sample = params.bits;
    let channels = params.channels;

    println!("\n📥 Received audio from {}: {} bytes", device_id, body.len());

    // Update device info
    {
        let mut devices = state.devices.lock().unwrap();
        devices.insert(
            device_id.clone(),
            crate::server::state::DeviceInfo {
                device_id: device_id.clone(),
                last_seen: Utc::now(),
                ip_address: None, // Could extract from request if needed
            },
        );
    }

    // Convert bytes to i16 samples
    let pcm_samples: Vec<i16> = if bits_per_sample == 16 {
        body.chunks_exact(2)
            .map(|chunk| i16::from_le_bytes([chunk[0], chunk[1]]))
            .collect()
    } else {
        return Err(StatusCode::BAD_REQUEST);
    };

    if pcm_samples.is_empty() {
        return Err(StatusCode::BAD_REQUEST);
    }

    // Get basic audio info (minimal - no complex analysis)
    let quality = analyze_audio_quality(&pcm_samples, sample_rate);
    println!("  Audio: {} samples, {:.2}s", quality.num_samples, quality.duration_sec);

    // Save WAV file directly (no processing)
    let timestamp = Utc::now().format("%Y%m%d_%H%M%S");
    let wav_filename = format!("{}_{}.wav", device_id, timestamp);
    let wav_path = PathBuf::from("received_audio").join(&wav_filename);
    
    fs::create_dir_all("received_audio").map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)?;
    save_wav_file(&wav_path, &pcm_samples, sample_rate, channels)
        .map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)?;
    
    println!("  Saved: {}", wav_path.display());

    // Extract audio quality metrics from headers (from ESP32)
    let mut audio_quality_json = serde_json::json!({});
    for (key, value) in headers.iter() {
        if let Some(header_name) = key.as_str().to_lowercase().strip_prefix("x-audio-") {
            if let Ok(header_value) = value.to_str() {
                if let Ok(num_value) = header_value.parse::<f64>() {
                    audio_quality_json[header_name] = serde_json::json!(num_value);
                }
            }
        }
    }

    // Minimal server-side info
    let server_analysis = serde_json::json!({
        "num_samples": quality.num_samples,
        "duration_sec": quality.duration_sec,
    });

    // Queue transcription
    let state_clone = state.clone();
    let samples_clone = pcm_samples.clone();
    let device_id_clone = device_id.clone();
    let wav_filename_clone = wav_filename.clone();
    let audio_quality_clone = audio_quality_json.clone();
    let server_analysis_clone = server_analysis.clone();

    tokio::spawn(async move {
        println!("🔄 Transcribing...");
        let mut engine = state_clone.engine.lock().unwrap();
        
        match engine.transcribe(&samples_clone) {
            Ok(text) => {
                // Calculate duration from server_analysis before moving it
                let duration = server_analysis_clone.get("duration_sec")
                    .and_then(|v| v.as_f64())
                    .unwrap_or(0.0);
                
                let transcript = Transcript {
                    device_id: device_id_clone.clone(),
                    timestamp: Utc::now(),
                    text: text.clone(),
                    audio_file: Some(format!("received_audio/{}", wav_filename_clone)),
                    audio_quality: Some(audio_quality_clone.clone()),
                    server_analysis: Some(server_analysis_clone.clone()),
                };

                // Save transcript to disk
                let transcript_dir = PathBuf::from("transcripts");
                fs::create_dir_all(&transcript_dir).ok();
                let transcript_filename = format!("{}_{}.json", 
                    device_id_clone, 
                    Utc::now().format("%Y%m%d_%H%M%S"));
                let transcript_path = transcript_dir.join(&transcript_filename);
                
                let transcript_json = serde_json::json!({
                    "device_id": transcript.device_id,
                    "timestamp": transcript.timestamp.to_rfc3339(),
                    "transcript": transcript.text,  // UI expects "transcript" field
                    "text": transcript.text,  // Keep both for compatibility
                    "audio_file": transcript.audio_file,
                    "duration": duration,  // UI expects duration field
                    "audio_quality": transcript.audio_quality,
                    "server_analysis": transcript.server_analysis,
                });

                if let Ok(json_str) = serde_json::to_string_pretty(&transcript_json) {
                    fs::write(&transcript_path, json_str).ok();
                    println!("📝 Transcript saved: {}", transcript_path.display());
                }

                // Save text file
                let txt_path = transcript_dir.join(
                    transcript_filename.replace(".json", ".txt"));
                fs::write(&txt_path, &text).ok();

                // Add to in-memory transcripts
                {
                    let mut transcripts = state_clone.transcripts.lock().unwrap();
                    transcripts.push(transcript.clone());
                    if transcripts.len() > 1000 {
                        transcripts.remove(0);
                    }
                }

                // Broadcast via SSE
                state_clone.broadcast_sse("transcript", &transcript_json);
                
                println!("📝 Transcript: {}", text);
                println!("{}", "=".repeat(60));
            }
            Err(e) => {
                eprintln!("❌ Transcription error: {}", e);
            }
        }
    });

    Ok(StatusCode::OK)
}

/// Handle GET /audio-file - serve WAV files
pub async fn handle_audio_file(
    Query(params): Query<HashMap<String, String>>,
) -> Result<impl IntoResponse, StatusCode> {
    let filepath = params.get("path").ok_or(StatusCode::BAD_REQUEST)?;
    
    // Normalize path and prevent directory traversal
    let mut path_str = filepath.to_string();
    
    // Remove leading slashes and normalize
    path_str = path_str.trim_start_matches('/').to_string();
    
    // Remove "received_audio/" prefix if present
    if path_str.starts_with("received_audio/") {
        path_str = path_str.strip_prefix("received_audio/").unwrap_or(&path_str).to_string();
    }
    
    // Security: prevent directory traversal
    if path_str.contains("..") || path_str.starts_with('/') {
        return Err(StatusCode::FORBIDDEN);
    }
    
    let full_path = PathBuf::from("received_audio").join(&path_str);
    
    // Additional security: ensure canonical path is within received_audio
    let canonical_base = std::fs::canonicalize("received_audio")
        .map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)?;
    let canonical_file = full_path.canonicalize()
        .map_err(|_| StatusCode::NOT_FOUND)?;
    
    if !canonical_file.starts_with(&canonical_base) {
        return Err(StatusCode::FORBIDDEN);
    }

    let contents = fs::read(&full_path).map_err(|_| StatusCode::NOT_FOUND)?;
    
    Ok((
        StatusCode::OK,
        [("Content-Type", "audio/wav")],
        contents,
    ))
}

/// Handle GET /status - device status (used by ESP32 for polling)
pub async fn handle_status(
    State(state): State<Arc<ServerState>>,
    ConnectInfo(addr): ConnectInfo<SocketAddr>,
    Query(params): Query<HashMap<String, String>>,
) -> Result<impl IntoResponse, StatusCode> {
    let device_id = params.get("device").cloned().unwrap_or_default();
    
    // Track device as active
    if !device_id.is_empty() {
        let mut devices = state.devices.lock().unwrap();
        devices.insert(
            device_id.clone(),
            crate::server::state::DeviceInfo {
                device_id: device_id.clone(),
                last_seen: Utc::now(),
                ip_address: Some(addr.ip().to_string()),
            },
        );
    }
    
    let recording = state
        .recording_state
        .lock()
        .unwrap()
        .get(&device_id)
        .copied()
        .unwrap_or(false);
    
    // Return format expected by ESP32: {"recording": true/false}
    if !device_id.is_empty() {
        Ok(Json(serde_json::json!({
            "recording": recording
        })))
    } else {
        // Return all device statuses for UI
        let devices_status: HashMap<String, bool> = state
            .recording_state
            .lock()
            .unwrap()
            .clone();
        Ok(Json(serde_json::json!({
            "devices": devices_status
        })))
    }
}

/// Handle GET /recording-status - compatibility endpoint
/// Returns device recording status in format expected by UI
pub async fn handle_recording_status(
    State(state): State<Arc<ServerState>>,
    Query(_params): Query<HashMap<String, String>>,
) -> Json<serde_json::Value> {
    // Return format compatible with Python server
    let devices: HashMap<String, bool> = state
        .recording_state
        .lock()
        .unwrap()
        .clone();
    
    Json(serde_json::json!({
        "devices": devices
    }))
}

/// Handle GET /devices - list active devices
pub async fn handle_devices(
    State(state): State<Arc<ServerState>>,
) -> Json<Vec<serde_json::Value>> {
    let now = Utc::now();
    let timeout_seconds = 10.0; // DEVICE_TIMEOUT_SECONDS
    
    let mut active_devices = Vec::new();
    let mut devices = state.devices.lock().unwrap();
    
    // Filter devices seen within timeout window
    devices.retain(|device_id, info| {
        let seconds_since_seen = (now - info.last_seen).num_seconds() as f64;
        
        if seconds_since_seen <= timeout_seconds {
            active_devices.push(serde_json::json!({
                "device_id": device_id,
                "ip": info.ip_address.clone().unwrap_or_default(),
                "last_seen": info.last_seen.to_rfc3339(),
                "seconds_ago": seconds_since_seen.round()
            }));
            true
        } else {
            false // Remove stale devices
        }
    });
    
    Json(active_devices)
}

/// Handle GET /transcripts - list all transcripts
pub async fn handle_transcripts(
    State(state): State<Arc<ServerState>>,
) -> Result<Json<Vec<serde_json::Value>>, StatusCode> {
    let transcript_dir = PathBuf::from("transcripts");
    let mut transcripts = Vec::new();

    if transcript_dir.exists() {
        let entries = fs::read_dir(&transcript_dir)
            .map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)?;

        for entry in entries {
            if let Ok(entry) = entry {
                let path = entry.path();
                if path.extension().and_then(|s| s.to_str()) == Some("json") {
                    if let Ok(content) = fs::read_to_string(&path) {
                        if let Ok(mut data) = serde_json::from_str::<serde_json::Value>(&content) {
                            // Ensure timestamp is in ISO format for UI compatibility
                            if let Some(ts) = data.get("timestamp") {
                                if let Some(ts_str) = ts.as_str() {
                                    // Check if already ISO format (contains 'T')
                                    if !ts_str.contains('T') {
                                        // Old format: "20260119_172003" - try to parse and convert
                                        if ts_str.len() >= 15 && ts_str.contains('_') {
                                            // Format: YYYYMMDD_HHMMSS
                                            if let Ok(dt) = chrono::NaiveDateTime::parse_from_str(ts_str, "%Y%m%d_%H%M%S") {
                                                let dt_utc = dt.and_utc();
                                                data["timestamp"] = serde_json::json!(dt_utc.to_rfc3339());
                                            }
                                        }
                                    }
                                    // If already ISO format or parse failed, keep as-is
                                }
                            }
                            
                            // Ensure transcript field exists (UI expects it)
                            if !data.get("transcript").is_some() {
                                if let Some(text) = data.get("text") {
                                    data["transcript"] = text.clone();
                                }
                            }
                            transcripts.push(data);
                        }
                    }
                }
            }
        }
    }

    // Sort by timestamp descending
    transcripts.sort_by(|a, b| {
        let ts_a = a.get("timestamp").and_then(|t| t.as_str()).unwrap_or("");
        let ts_b = b.get("timestamp").and_then(|t| t.as_str()).unwrap_or("");
        ts_b.cmp(ts_a)
    });

    Ok(Json(transcripts))
}

/// Handle POST /record/start - start recording for device
pub async fn handle_recording_start(
    State(state): State<Arc<ServerState>>,
    Query(params): Query<HashMap<String, String>>,
) -> Result<impl IntoResponse, StatusCode> {
    let device_id = params.get("device").cloned().ok_or(StatusCode::BAD_REQUEST)?;
    
    {
        let mut recording = state.recording_state.lock().unwrap();
        recording.insert(device_id.clone(), true);
    }

    println!("\n🔴 RECORDING STARTED for device: {}", device_id);
    println!("{}", "=".repeat(60));

    state.broadcast_sse("device_status", &serde_json::json!({
        "device_id": device_id,
        "recording": true,
    }));

    Ok(Json(serde_json::json!({
        "status": "started",
        "device_id": device_id
    })))
}

/// Handle POST /record/stop - stop recording for device
pub async fn handle_recording_stop(
    State(state): State<Arc<ServerState>>,
    Query(params): Query<HashMap<String, String>>,
) -> Result<impl IntoResponse, StatusCode> {
    let device_id = params.get("device").cloned().ok_or(StatusCode::BAD_REQUEST)?;
    
    {
        let mut recording = state.recording_state.lock().unwrap();
        recording.insert(device_id.clone(), false);
    }

    println!("\n⏹️  RECORDING STOPPED for device: {}", device_id);
    println!("{}", "=".repeat(60));

    state.broadcast_sse("device_status", &serde_json::json!({
        "device_id": device_id,
        "recording": false,
    }));

    Ok(Json(serde_json::json!({
        "status": "stopped",
        "device_id": device_id
    })))
}

/// Handle GET /events - Server-Sent Events stream
pub async fn handle_events(
    State(state): State<Arc<ServerState>>,
) -> Sse<impl Stream<Item = Result<axum::response::sse::Event, axum::Error>>> {
    let (tx, rx) = mpsc::unbounded_channel();
    state.add_sse_sender(tx);

    let stream = UnboundedReceiverStream::new(rx)
        .map(|msg| Ok(axum::response::sse::Event::default().data(msg)));

    Sse::new(stream).keep_alive(
        axum::response::sse::KeepAlive::new()
            .interval(std::time::Duration::from_secs(15))
            .text("keep-alive-text"),
    )
}
