mod server;

use memo_stt::SttEngine;
use server::create_router;
use server::state::ServerState;
use std::sync::Arc;
use tokio::net::TcpListener;

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    println!("🚀 Starting memo-esp-server...");
    
    // Initialize Whisper engine (16kHz for ESP32 audio)
    println!("Loading Whisper model (16kHz for ESP32 audio)...");
    let engine = SttEngine::new_default(16000)?;
    
    println!("Warming up GPU...");
    engine.warmup()?;
    println!("✓ Model ready!");
    
    // Create server state
    let state = Arc::new(ServerState::new(engine));
    
    // Create router
    let app = create_router(state);
    
    // Create directories if they don't exist
    std::fs::create_dir_all("received_audio")?;
    std::fs::create_dir_all("transcripts")?;
    std::fs::create_dir_all("static")?;
    
    // Start server with ConnectInfo support
    let listener = TcpListener::bind("0.0.0.0:8000").await?;
    println!("📡 Server listening on http://0.0.0.0:8000");
    println!("   UI: http://localhost:8000/");
    println!("   Audio endpoint: http://localhost:8000/audio");
    println!("   Status endpoint: http://localhost:8000/status");
    println!("\n⌨️  Press Ctrl+C to stop\n");
    
    axum::serve(listener, app.into_make_service_with_connect_info::<std::net::SocketAddr>()).await?;
    
    Ok(())
}
