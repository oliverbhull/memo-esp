use std::fs::File;
use std::io::Write;
use std::path::Path;
use anyhow::Result;

#[derive(Debug, Clone)]
pub struct AudioQuality {
    pub num_samples: usize,
    pub duration_sec: f32,
}


/// Save PCM data as WAV file
pub fn save_wav_file(
    path: &Path,
    pcm_data: &[i16],
    sample_rate: u32,
    channels: u16,
) -> Result<()> {
    let mut file = File::create(path)?;
    
    let bits_per_sample = 16u16;
    let pcm_data_len = pcm_data.len() * 2; // 16-bit = 2 bytes per sample
    let _wav_size = 44 + pcm_data_len;
    
    // RIFF header
    file.write_all(b"RIFF")?;
    file.write_all(&(36u32 + pcm_data_len as u32).to_le_bytes())?;
    file.write_all(b"WAVE")?;
    
    // fmt chunk
    file.write_all(b"fmt ")?;
    file.write_all(&16u32.to_le_bytes())?; // fmt chunk size
    file.write_all(&1u16.to_le_bytes())?; // audio format (PCM)
    file.write_all(&channels.to_le_bytes())?;
    file.write_all(&sample_rate.to_le_bytes())?;
    file.write_all(&(sample_rate as u32 * channels as u32 * (bits_per_sample as u32 / 8)).to_le_bytes())?; // byte rate
    file.write_all(&(channels * (bits_per_sample / 8)).to_le_bytes())?; // block align
    file.write_all(&bits_per_sample.to_le_bytes())?;
    
    // data chunk
    file.write_all(b"data")?;
    file.write_all(&(pcm_data_len as u32).to_le_bytes())?;
    
    // PCM data (16-bit little-endian)
    for &sample in pcm_data {
        file.write_all(&sample.to_le_bytes())?;
    }
    
    Ok(())
}

/// Get basic audio info (minimal - no complex analysis)
pub fn analyze_audio_quality(pcm_data: &[i16], sample_rate: u32) -> AudioQuality {
    AudioQuality {
        num_samples: pcm_data.len(),
        duration_sec: if sample_rate > 0 {
            pcm_data.len() as f32 / sample_rate as f32
        } else {
            0.0
        },
    }
}
