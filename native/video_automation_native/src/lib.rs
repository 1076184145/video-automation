use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyDict;
use std::fs;
use std::path::Path;

pub mod cuts;

#[derive(Debug)]
struct WaveformPayload {
    sample_rate: u32,
    channels: u16,
    bits: u16,
    duration: f64,
    pixels_per_second: u32,
    data: Vec<i32>,
}

#[derive(Debug)]
struct WavInfo<'a> {
    sample_rate: u32,
    channels: u16,
    bits: u16,
    data: &'a [u8],
}

#[pyfunction]
#[pyo3(signature = (path, pixels_per_second = 20))]
fn waveform_from_wav(py: Python<'_>, path: &str, pixels_per_second: u32) -> PyResult<PyObject> {
    let payload = waveform_payload(Path::new(path), pixels_per_second)
        .map_err(|message| PyValueError::new_err(message))?;
    let dict = PyDict::new_bound(py);
    dict.set_item("status", "ready")?;
    dict.set_item("source", "rust_wave_fallback")?;
    dict.set_item("sample_rate", payload.sample_rate)?;
    dict.set_item("channels", payload.channels)?;
    dict.set_item("bits", payload.bits)?;
    dict.set_item("pixels_per_second", payload.pixels_per_second)?;
    dict.set_item("duration", payload.duration)?;
    dict.set_item("data", payload.data)?;
    Ok(dict.into())
}

#[pymodule]
fn video_automation_native(py: Python<'_>, module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(waveform_from_wav, module)?)?;
    let cuts_module = PyModule::new_bound(py, "cuts")?;
    cuts::register_submodule(&cuts_module)?;
    module.add_submodule(&cuts_module)?;
    Ok(())
}

fn waveform_payload(path: &Path, pixels_per_second: u32) -> Result<WaveformPayload, String> {
    if pixels_per_second == 0 {
        return Err("pixels_per_second must be greater than zero".to_string());
    }
    let bytes = fs::read(path).map_err(|error| error.to_string())?;
    let wav = parse_wav(&bytes)?;
    let sample_width = usize::from(wav.bits / 8);
    if sample_width == 0 || wav.channels == 0 {
        return Err("invalid WAV format".to_string());
    }
    let frame_size = usize::from(wav.channels) * sample_width;
    if frame_size == 0 {
        return Err("invalid WAV frame size".to_string());
    }
    let frame_count = wav.data.len() / frame_size;
    let frames_per_bucket = std::cmp::max(1, wav.sample_rate / pixels_per_second) as usize;
    let mut data = Vec::new();

    if wav.bits == 8 || wav.bits == 16 {
        let mut frame_start = 0usize;
        while frame_start < frame_count {
            let frame_end = std::cmp::min(frame_count, frame_start + frames_per_bucket);
            let mut min_value: Option<i32> = None;
            let mut max_value: Option<i32> = None;
            for frame_index in frame_start..frame_end {
                let sample = mixed_frame_sample(
                    wav.data,
                    frame_index,
                    sample_width,
                    wav.channels,
                    wav.bits,
                )?;
                min_value = Some(min_value.map_or(sample, |value| value.min(sample)));
                max_value = Some(max_value.map_or(sample, |value| value.max(sample)));
            }
            if let (Some(min_sample), Some(max_sample)) = (min_value, max_value) {
                let peak = if wav.bits == 8 { 128.0 } else { 32768.0 };
                let scale = 128.0 / peak;
                data.push(clamp_i8(py_round(f64::from(min_sample) * scale)));
                data.push(clamp_i8(py_round(f64::from(max_sample) * scale)));
            }
            frame_start = frame_end;
        }
    }

    Ok(WaveformPayload {
        sample_rate: wav.sample_rate,
        channels: wav.channels,
        bits: wav.bits,
        duration: round_3(frame_count as f64 / f64::from(wav.sample_rate.max(1))),
        pixels_per_second,
        data,
    })
}

fn parse_wav(bytes: &[u8]) -> Result<WavInfo<'_>, String> {
    if bytes.len() < 12 || &bytes[0..4] != b"RIFF" || &bytes[8..12] != b"WAVE" {
        return Err("invalid WAV header".to_string());
    }

    let mut offset = 12usize;
    let mut sample_rate = 0u32;
    let mut channels = 0u16;
    let mut bits = 0u16;
    let mut data: Option<&[u8]> = None;

    while offset + 8 <= bytes.len() {
        let chunk_id = &bytes[offset..offset + 4];
        let chunk_size = read_u32_le(bytes, offset + 4)? as usize;
        let chunk_start = offset + 8;
        let chunk_end = chunk_start
            .checked_add(chunk_size)
            .ok_or_else(|| "invalid WAV chunk size".to_string())?;
        if chunk_end > bytes.len() {
            return Err("truncated WAV chunk".to_string());
        }

        if chunk_id == b"fmt " {
            if chunk_size < 16 {
                return Err("invalid WAV fmt chunk".to_string());
            }
            let audio_format = read_u16_le(bytes, chunk_start)?;
            if audio_format != 1 {
                return Err("only PCM WAV is supported".to_string());
            }
            channels = read_u16_le(bytes, chunk_start + 2)?;
            sample_rate = read_u32_le(bytes, chunk_start + 4)?;
            bits = read_u16_le(bytes, chunk_start + 14)?;
        } else if chunk_id == b"data" {
            data = Some(&bytes[chunk_start..chunk_end]);
        }

        offset = chunk_end + (chunk_size % 2);
    }

    let data = data.ok_or_else(|| "missing WAV data chunk".to_string())?;
    if sample_rate == 0 || channels == 0 || bits == 0 {
        return Err("missing WAV fmt chunk".to_string());
    }
    Ok(WavInfo {
        sample_rate,
        channels,
        bits,
        data,
    })
}

fn mixed_frame_sample(
    data: &[u8],
    frame_index: usize,
    sample_width: usize,
    channels: u16,
    bits: u16,
) -> Result<i32, String> {
    let frame_offset = frame_index
        .checked_mul(usize::from(channels) * sample_width)
        .ok_or_else(|| "invalid WAV frame offset".to_string())?;
    let mut sum = 0i32;
    for channel in 0..usize::from(channels) {
        let sample_offset = frame_offset + channel * sample_width;
        let sample = match bits {
            8 => i32::from(data[sample_offset]) - 128,
            16 => i32::from(read_i16_le(data, sample_offset)?),
            _ => 0,
        };
        sum += sample;
    }
    Ok(floor_div(sum, i32::from(channels)))
}

fn read_u16_le(bytes: &[u8], offset: usize) -> Result<u16, String> {
    let slice = bytes
        .get(offset..offset + 2)
        .ok_or_else(|| "truncated WAV value".to_string())?;
    Ok(u16::from_le_bytes([slice[0], slice[1]]))
}

fn read_i16_le(bytes: &[u8], offset: usize) -> Result<i16, String> {
    let slice = bytes
        .get(offset..offset + 2)
        .ok_or_else(|| "truncated WAV sample".to_string())?;
    Ok(i16::from_le_bytes([slice[0], slice[1]]))
}

fn read_u32_le(bytes: &[u8], offset: usize) -> Result<u32, String> {
    let slice = bytes
        .get(offset..offset + 4)
        .ok_or_else(|| "truncated WAV value".to_string())?;
    Ok(u32::from_le_bytes([slice[0], slice[1], slice[2], slice[3]]))
}

fn floor_div(value: i32, divisor: i32) -> i32 {
    let quotient = value / divisor;
    let remainder = value % divisor;
    if remainder != 0 && ((remainder > 0) != (divisor > 0)) {
        quotient - 1
    } else {
        quotient
    }
}

fn py_round(value: f64) -> i32 {
    let floor = value.floor();
    let diff = value - floor;
    if diff < 0.5 {
        floor as i32
    } else if diff > 0.5 {
        (floor + 1.0) as i32
    } else {
        let floor_int = floor as i64;
        if floor_int % 2 == 0 {
            floor_int as i32
        } else {
            (floor_int + 1) as i32
        }
    }
}

fn clamp_i8(value: i32) -> i32 {
    value.clamp(-128, 127)
}

fn round_3(value: f64) -> f64 {
    (value * 1000.0).round() / 1000.0
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::{SystemTime, UNIX_EPOCH};

    #[test]
    fn generates_8_bit_mono_buckets() {
        let path = temp_wav_path("mono8");
        write_pcm_wav(&path, 1, 8, 4, &[0, 255, 128, 138]);

        let payload = waveform_payload(&path, 2).expect("waveform payload");

        assert_eq!(payload.sample_rate, 4);
        assert_eq!(payload.channels, 1);
        assert_eq!(payload.bits, 8);
        assert_eq!(payload.duration, 1.0);
        assert_eq!(payload.data, vec![-128, 127, 0, 10]);
        let _ = fs::remove_file(path);
    }

    #[test]
    fn averages_16_bit_stereo_frames_like_python_floor_division() {
        let path = temp_wav_path("stereo16");
        let samples = [-32768i16, 0, 32767, 32767, -1, 0, 256, 0];
        let bytes: Vec<u8> = samples
            .iter()
            .flat_map(|sample| sample.to_le_bytes())
            .collect();
        write_pcm_wav(&path, 2, 16, 4, &bytes);

        let payload = waveform_payload(&path, 2).expect("waveform payload");

        assert_eq!(payload.channels, 2);
        assert_eq!(payload.bits, 16);
        assert_eq!(payload.data, vec![-64, 127, 0, 0]);
        let _ = fs::remove_file(path);
    }

    #[test]
    fn keeps_short_audio_in_one_bucket() {
        let path = temp_wav_path("short");
        let samples = [-1000i16, 2000];
        let bytes: Vec<u8> = samples
            .iter()
            .flat_map(|sample| sample.to_le_bytes())
            .collect();
        write_pcm_wav(&path, 1, 16, 100, &bytes);

        let payload = waveform_payload(&path, 20).expect("waveform payload");

        assert_eq!(payload.duration, 0.02);
        assert_eq!(payload.data, vec![-4, 8]);
        let _ = fs::remove_file(path);
    }

    #[test]
    fn rejects_invalid_wav() {
        let path = temp_wav_path("invalid");
        fs::write(&path, b"not a wav").expect("write invalid file");

        let error = waveform_payload(&path, 20).expect_err("invalid wav should fail");

        assert!(error.contains("invalid WAV header"));
        let _ = fs::remove_file(path);
    }

    fn temp_wav_path(name: &str) -> std::path::PathBuf {
        let stamp = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("clock")
            .as_nanos();
        std::env::temp_dir().join(format!("video_automation_{name}_{stamp}.wav"))
    }

    fn write_pcm_wav(path: &Path, channels: u16, bits: u16, sample_rate: u32, data: &[u8]) {
        let mut bytes = Vec::new();
        let fmt_size = 16u32;
        let data_size = data.len() as u32;
        let riff_size = 4 + 8 + fmt_size + 8 + data_size;
        let byte_rate = sample_rate * u32::from(channels) * u32::from(bits / 8);
        let block_align = channels * (bits / 8);

        bytes.extend_from_slice(b"RIFF");
        bytes.extend_from_slice(&riff_size.to_le_bytes());
        bytes.extend_from_slice(b"WAVE");
        bytes.extend_from_slice(b"fmt ");
        bytes.extend_from_slice(&fmt_size.to_le_bytes());
        bytes.extend_from_slice(&1u16.to_le_bytes());
        bytes.extend_from_slice(&channels.to_le_bytes());
        bytes.extend_from_slice(&sample_rate.to_le_bytes());
        bytes.extend_from_slice(&byte_rate.to_le_bytes());
        bytes.extend_from_slice(&block_align.to_le_bytes());
        bytes.extend_from_slice(&bits.to_le_bytes());
        bytes.extend_from_slice(b"data");
        bytes.extend_from_slice(&data_size.to_le_bytes());
        bytes.extend_from_slice(data);
        fs::write(path, bytes).expect("write wav");
    }
}
