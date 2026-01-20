use memo_stt::SttEngine;
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::sync::Arc;
use std::sync::Mutex;
use tokio::sync::mpsc;
use chrono::{DateTime, Utc};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DeviceInfo {
    pub device_id: String,
    pub last_seen: DateTime<Utc>,
    pub ip_address: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Transcript {
    pub device_id: String,
    pub timestamp: DateTime<Utc>,
    pub text: String,
    pub audio_file: Option<String>,
    pub audio_quality: Option<serde_json::Value>,
    pub server_analysis: Option<serde_json::Value>,
}

pub struct ServerState {
    pub engine: Arc<Mutex<SttEngine>>,
    pub devices: Arc<Mutex<HashMap<String, DeviceInfo>>>,
    pub transcripts: Arc<Mutex<Vec<Transcript>>>,
    pub recording_state: Arc<Mutex<HashMap<String, bool>>>,
    pub sse_senders: Arc<Mutex<Vec<mpsc::UnboundedSender<String>>>>,
}

impl ServerState {
    pub fn new(engine: SttEngine) -> Self {
        Self {
            engine: Arc::new(Mutex::new(engine)),
            devices: Arc::new(Mutex::new(HashMap::new())),
            transcripts: Arc::new(Mutex::new(Vec::new())),
            recording_state: Arc::new(Mutex::new(HashMap::new())),
            sse_senders: Arc::new(Mutex::new(Vec::new())),
        }
    }

    pub fn broadcast_sse(&self, event_type: &str, data: &serde_json::Value) {
        let message = format!(
            "event: {}\ndata: {}\n\n",
            event_type,
            serde_json::to_string(data).unwrap_or_default()
        );

        let mut senders = self.sse_senders.lock().unwrap();
        senders.retain(|sender| {
            sender.send(message.clone()).is_ok()
        });
    }

    pub fn add_sse_sender(&self, sender: mpsc::UnboundedSender<String>) {
        let mut senders = self.sse_senders.lock().unwrap();
        senders.push(sender);
    }
}
