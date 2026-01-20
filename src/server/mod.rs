pub mod audio;
pub mod handlers;
pub mod state;

use axum::{
    routing::{get, post},
    Router,
};
use handlers::{
    handle_audio, handle_audio_file, handle_devices, handle_events,
    handle_recording_start, handle_recording_stop, handle_recording_status,
    handle_status, handle_transcripts,
};
use state::ServerState;
use std::sync::Arc;
use tower::ServiceBuilder;
use tower_http::{
    cors::CorsLayer,
    services::ServeDir,
};

pub fn create_router(state: Arc<ServerState>) -> Router {
    Router::new()
        .route("/audio", post(handle_audio))
        .route("/audio-file", get(handle_audio_file))
        .route("/status", get(handle_status))
        .route("/recording-status", get(handle_recording_status))
        .route("/devices", get(handle_devices))
        .route("/transcripts", get(handle_transcripts))
        .route("/record/start", post(handle_recording_start))
        .route("/record/stop", post(handle_recording_stop))
        .route("/events", get(handle_events))
        .nest_service("/", ServeDir::new("static"))
        .layer(
            ServiceBuilder::new()
                .layer(CorsLayer::permissive())
        )
        .with_state(state)
}
