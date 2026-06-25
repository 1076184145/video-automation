use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};
use std::cmp::Ordering;

fn round_3(value: f64) -> f64 {
    (value * 1000.0).round() / 1000.0
}

#[derive(Debug, Clone)]
struct Range {
    start: f64,
    end: f64,
}

impl Range {
    fn duration(&self) -> f64 {
        round_3((self.end - self.start).max(0.0))
    }
}

fn extract_valid_ranges(py_ranges: &Bound<'_, PyList>, duration: f64) -> Vec<Range> {
    let mut valid = Vec::new();
    for item in py_ranges.iter() {
        if let Ok(dict) = item.downcast::<PyDict>() {
            let start: f64 = match dict.get_item("start") {
                Ok(Some(v)) => v.extract::<f64>().unwrap_or(-1.0),
                _ => -1.0,
            };
            let end: f64 = match dict.get_item("end") {
                Ok(Some(v)) => v.extract::<f64>().unwrap_or(-1.0),
                _ => -1.0,
            };
            if start >= 0.0 && end >= 0.0 {
                let start = start.max(0.0);
                let end = end.min(duration);
                if end > start {
                    valid.push(Range {
                        start: round_3(start),
                        end: round_3(end),
                    });
                }
            }
        }
    }
    valid.sort_by(|a, b| a.start.partial_cmp(&b.start).unwrap());
    valid
}

fn intersect_ranges(left: &[Range], right: &[Range]) -> Vec<Range> {
    let mut intersections = Vec::new();
    let mut left_index = 0;
    let mut right_index = 0;

    while left_index < left.len() && right_index < right.len() {
        let l = &left[left_index];
        let r = &right[right_index];

        let start = l.start.max(r.start);
        let end = l.end.min(r.end);

        if end > start {
            intersections.push(Range {
                start: round_3(start),
                end: round_3(end),
            });
        }

        if l.end < r.end {
            left_index += 1;
        } else {
            right_index += 1;
        }
    }
    intersections
}

fn merge_ranges_internal(ranges: &[Range], gap_seconds: f64, min_duration: f64) -> Vec<Range> {
    if ranges.is_empty() {
        return Vec::new();
    }
    let mut merged = Vec::new();
    let mut current = ranges[0].clone();

    for item in ranges.iter().skip(1) {
        if item.start <= current.end + gap_seconds {
            current.end = current.end.max(item.end);
            continue;
        }
        if current.duration() >= min_duration {
            merged.push(current.clone());
        }
        current = item.clone();
    }
    if current.duration() >= min_duration {
        merged.push(current);
    }
    merged
}

#[pyfunction]
fn merge_invalid_ranges<'py>(
    py: Python<'py>,
    duration: f64,
    silences: &Bound<'py, PyList>,
    freezes: &Bound<'py, PyList>,
) -> PyResult<Bound<'py, PyList>> {
    let silences_val = extract_valid_ranges(silences, duration);
    let freezes_val = extract_valid_ranges(freezes, duration);

    let reason = if !silences_val.is_empty() && !freezes_val.is_empty() {
        "silence+freeze"
    } else {
        "silence"
    };

    let to_merge = if !silences_val.is_empty() && !freezes_val.is_empty() {
        intersect_ranges(&silences_val, &freezes_val)
    } else if !silences_val.is_empty() {
        silences_val
    } else {
        Vec::new()
    };

    let merged = merge_ranges_internal(&to_merge, 0.12, 0.35);

    let result = PyList::empty_bound(py);
    for r in merged {
        let dict = PyDict::new_bound(py);
        dict.set_item("start", r.start)?;
        dict.set_item("end", r.end)?;
        dict.set_item("duration", r.duration())?;
        dict.set_item("drop", true)?;
        dict.set_item("reason", reason)?;
        result.append(dict)?;
    }
    Ok(result)
}

#[derive(Debug, Clone)]
struct Clip {
    start: f64,
    end: f64,
    reason: String,
    keep: bool,
    subtitle_override: Option<bool>,
    subtitle_text: Option<String>,
    transcript_text: Option<String>,
}

impl Clip {
    fn duration(&self) -> f64 {
        round_3((self.end - self.start).max(0.0))
    }

    fn into_dict<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
        let dict = PyDict::new_bound(py);
        dict.set_item("start", self.start)?;
        dict.set_item("end", self.end)?;
        dict.set_item("duration", self.duration())?;
        dict.set_item("keep", self.keep)?;
        dict.set_item("reason", &self.reason)?;
        if let Some(b) = self.subtitle_override {
            dict.set_item("subtitle_override", b)?;
        }
        if let Some(s) = &self.subtitle_text {
            dict.set_item("subtitle_text", s)?;
        }
        if let Some(s) = &self.transcript_text {
            dict.set_item("transcript_text", s)?;
        }
        Ok(dict)
    }
}

fn join_reasons(r1: &str, r2: &str, fallback: &str) -> String {
    let mut vals = Vec::new();
    let p1 = r1.trim();
    if !p1.is_empty() {
        vals.push(p1);
    }
    let p2 = r2.trim();
    if !p2.is_empty() && !vals.contains(&p2) {
        vals.push(p2);
    }
    let p3 = fallback.trim();
    if !p3.is_empty() && !vals.contains(&p3) {
        vals.push(p3);
    }
    if vals.is_empty() {
        fallback.to_string()
    } else {
        vals.join(" / ")
    }
}

fn merge_clips_across_short_gaps(clips: &[Clip], merge_gap_seconds: f64) -> Vec<Clip> {
    if clips.is_empty() {
        return Vec::new();
    }
    let mut merged = vec![clips[0].clone()];
    for current in clips.iter().skip(1) {
        let gap = (current.start - merged.last().unwrap().end).max(0.0);
        if gap <= merge_gap_seconds {
            let last = merged.last_mut().unwrap();
            last.end = current.end;
            let formatted_gap = format!("{:.2}", gap);
            last.reason = join_reasons(
                &last.reason,
                &current.reason,
                &format!("merged gap {}s", formatted_gap),
            );
        } else {
            merged.push(current.clone());
        }
    }
    merged
}

fn absorb_short_clips(mut clips: Vec<Clip>, min_clip_seconds: f64, max_gap: f64) -> Vec<Clip> {
    if clips.len() <= 1 {
        return clips;
    }
    let mut result = Vec::new();
    let mut index = 0;
    while index < clips.len() {
        let current = clips[index].clone();
        if current.duration() >= min_clip_seconds {
            result.push(current);
            index += 1;
            continue;
        }
        let left_gap = if let Some(left) = result.last() {
            current.start - left.end
        } else {
            f64::INFINITY
        };
        let right_gap = if index + 1 < clips.len() {
            clips[index + 1].start - current.end
        } else {
            f64::INFINITY
        };

        let has_left = result.last().is_some();
        let has_right = index + 1 < clips.len();

        if has_left && (left_gap <= right_gap || !has_right) && left_gap <= max_gap {
            let left = result.last_mut().unwrap();
            left.end = current.end;
            left.reason = join_reasons(&left.reason, &current.reason, "absorbed short clip");
            index += 1;
            continue;
        }
        if has_right && right_gap <= max_gap {
            clips[index + 1].start = current.start;
            clips[index + 1].reason = join_reasons(
                &current.reason,
                &clips[index + 1].reason,
                "absorbed short clip",
            );
            index += 1;
            continue;
        }
        result.push(current);
        index += 1;
    }
    result
}

fn stabilize_keep_clips(
    clips: Vec<Clip>,
    min_clip_seconds: f64,
    merge_gap_seconds: f64,
) -> Vec<Clip> {
    if clips.len() <= 1 {
        return clips;
    }
    let merged = merge_clips_across_short_gaps(&clips, merge_gap_seconds);
    let absorbed = absorb_short_clips(
        merged,
        min_clip_seconds,
        merge_gap_seconds.max(min_clip_seconds * 1.5),
    );
    merge_clips_across_short_gaps(&absorbed, merge_gap_seconds)
}

#[pyfunction]
fn generate_and_stabilize_clips<'py>(
    py: Python<'py>,
    duration: f64,
    invalid_segments: &Bound<'py, PyList>,
    min_gap: f64,
    min_clip_seconds: f64,
    merge_gap_seconds: f64,
) -> PyResult<Bound<'py, PyList>> {
    if duration <= 0.0 {
        return Ok(PyList::empty_bound(py));
    }
    let mut clips = Vec::new();
    if invalid_segments.is_empty() {
        clips.push(Clip {
            start: 0.0,
            end: round_3(duration),
            reason: "full media".to_string(),
            keep: true,
            subtitle_override: None,
            subtitle_text: None,
            transcript_text: None,
        });
    } else {
        let mut cursor = 0.0;
        let padding = (min_gap / 2.0).max(0.0);
        for item in invalid_segments.iter() {
            if let Ok(dict) = item.downcast::<PyDict>() {
                let start_val: f64 = match dict.get_item("start") {
                    Ok(Some(v)) => v.extract().unwrap_or(0.0),
                    _ => 0.0,
                };
                let start = (start_val - padding).max(0.0);
                if start > cursor {
                    let mut rsn = "invalid segment".to_string();
                    if let Ok(Some(reason_v)) = dict.get_item("reason") {
                        if let Ok(s) = reason_v.extract::<String>() {
                            rsn = s;
                        }
                    }
                    clips.push(Clip {
                        start: round_3(cursor),
                        end: round_3(start),
                        reason: format!("before {}", rsn),
                        keep: true,
                        subtitle_override: None,
                        subtitle_text: None,
                        transcript_text: None,
                    });
                }
                let end_val: f64 = match dict.get_item("end") {
                    Ok(Some(v)) => v.extract().unwrap_or(start_val),
                    _ => start_val,
                };
                cursor = (end_val + padding).min(duration);
            }
        }
        if cursor < duration {
            clips.push(Clip {
                start: round_3(cursor),
                end: round_3(duration),
                reason: "tail".to_string(),
                keep: true,
                subtitle_override: None,
                subtitle_text: None,
                transcript_text: None,
            });
        }
    }

    clips.retain(|c| c.duration() >= 0.35);

    let stabilized = stabilize_keep_clips(clips, min_clip_seconds, merge_gap_seconds);

    let result = PyList::empty_bound(py);
    for c in stabilized {
        result.append(c.into_dict(py)?)?;
    }
    Ok(result)
}

fn truncate_text(text: &str, limit: usize) -> String {
    if text.chars().count() <= limit {
        return text.to_string();
    }
    let truncated: String = text.chars().take(limit - 1).collect();
    truncated.trim_end().to_string() + "..."
}

#[pyfunction]
fn attach_transcript_and_score<'py>(
    py: Python<'py>,
    clips_in: &Bound<'py, PyList>,
    transcript_segments: &Bound<'py, PyList>,
) -> PyResult<Bound<'py, PyList>> {
    let result = PyList::empty_bound(py);

    let mut out_clips = Vec::new();
    for clip_item in clips_in.iter() {
        let dict = clip_item.downcast::<PyDict>()?.copy()?;
        let clip_start: f64 = dict.get_item("start")?.unwrap().extract()?;
        let clip_end: f64 = dict.get_item("end")?.unwrap().extract()?;
        let clip_duration: f64 = dict.get_item("duration")?.unwrap().extract()?;
        let clip_scene_count: usize = match dict.get_item("scene_count")? {
            Some(v) => v.extract().unwrap_or(0),
            None => 0,
        };

        let overlapping = PyList::empty_bound(py);
        let mut parts = Vec::new();

        for seg_item in transcript_segments.iter() {
            if let Ok(seg_dict) = seg_item.downcast::<PyDict>() {
                let seg_start: f64 = match seg_dict.get_item("start") {
                    Ok(Some(v)) => v.extract().unwrap_or(-1.0),
                    _ => -1.0,
                };
                let seg_end: f64 = match seg_dict.get_item("end") {
                    Ok(Some(v)) => v.extract().unwrap_or(-1.0),
                    _ => -1.0,
                };
                if seg_start < 0.0 || seg_end < 0.0 {
                    continue;
                }
                if seg_end <= clip_start || seg_start >= clip_end {
                    continue;
                }

                overlapping.append(seg_dict)?;

                let mut word_matched = false;
                if let Ok(Some(words)) = seg_dict.get_item("words") {
                    if let Ok(words_list) = words.downcast::<PyList>() {
                        if !words_list.is_empty() {
                            let mut selected = String::new();
                            for word_item in words_list.iter() {
                                if let Ok(word_dict) = word_item.downcast::<PyDict>() {
                                    let w_start: f64 = match word_dict.get_item("start") {
                                        Ok(Some(v)) => v.extract().unwrap_or(-1.0),
                                        _ => -1.0,
                                    };
                                    let w_end: f64 = match word_dict.get_item("end") {
                                        Ok(Some(v)) => v.extract().unwrap_or(-1.0),
                                        _ => -1.0,
                                    };
                                    if w_end <= clip_start || w_start >= clip_end {
                                        continue;
                                    }
                                    if let Ok(Some(w_str)) = word_dict.get_item("word") {
                                        if let Ok(s) = w_str.extract::<String>() {
                                            selected.push_str(s.trim());
                                        }
                                    }
                                }
                            }
                            if !selected.is_empty() {
                                parts.push(selected);
                                word_matched = true;
                            }
                        }
                    }
                }
                if !word_matched {
                    if let Ok(Some(text_val)) = seg_dict.get_item("text") {
                        if let Ok(s) = text_val.extract::<String>() {
                            parts.push(s.trim().to_string());
                        }
                    }
                }
            }
        }

        let text = parts
            .into_iter()
            .filter(|s| !s.is_empty())
            .collect::<Vec<_>>()
            .join(" ")
            .trim()
            .to_string();

        if let Ok(Some(ovr)) = dict.get_item("subtitle_override") {
            if ovr.extract::<bool>().unwrap_or(false) {
                let mut sub_text = String::new();
                if let Ok(Some(v)) = dict.get_item("subtitle_text") {
                    if let Ok(s) = v.extract::<String>() {
                        sub_text = s.trim().to_string();
                    }
                }
                if sub_text.is_empty() {
                    if let Ok(Some(v)) = dict.get_item("transcript_text") {
                        if let Ok(s) = v.extract::<String>() {
                            sub_text = s.trim().to_string();
                        }
                    }
                }
                dict.set_item("subtitle_text", &sub_text)?;
                dict.set_item("transcript_text", truncate_text(&sub_text, 160))?;
            } else {
                dict.set_item("transcript_text", truncate_text(&text, 160))?;
            }
        } else {
            dict.set_item("transcript_text", truncate_text(&text, 160))?;
        }

        let limited_overlapping = PyList::empty_bound(py);
        for i in 0..overlapping.len().min(20) {
            limited_overlapping.append(overlapping.get_item(i).unwrap())?;
        }
        dict.set_item("transcript_segments", limited_overlapping)?;

        let dur = clip_duration.max(0.001);
        let final_text: String = match dict.get_item("transcript_text") {
            Ok(Some(v)) => v.extract().unwrap_or("".to_string()),
            _ => "".to_string(),
        };
        let text_chars = final_text.chars().count() as f64;

        let speech_density = (text_chars / 28.0_f64.max(dur * 7.0)).min(1.0);
        let scene_density = ((clip_scene_count as f64) / 1.0_f64.max(dur / 18.0)).min(1.0);
        let duration_balance = if dur >= 8.0 && dur <= 75.0 {
            1.0
        } else if dur >= 4.0 && dur <= 120.0 {
            0.55
        } else {
            0.25
        };

        let score = ((speech_density * 0.55 + scene_density * 0.3 + duration_balance * 0.15)
            * 100.0
            * 10.0)
            .round()
            / 10.0;
        dict.set_item("content_score", score)?;

        let sigs = PyDict::new_bound(py);
        sigs.set_item("speech_density", round_3(speech_density))?;
        sigs.set_item("scene_density", round_3(scene_density))?;
        sigs.set_item("duration_balance", round_3(duration_balance))?;
        dict.set_item("content_signals", sigs)?;

        let rec = if score >= 70.0 {
            "strong_keep"
        } else if score >= 42.0 {
            "review"
        } else {
            "trim_candidate"
        };
        dict.set_item("recommendation", rec)?;

        out_clips.push((score, dict));
    }

    let mut indices: Vec<usize> = (0..out_clips.len()).collect();
    indices.sort_by(|a, b| {
        out_clips[*b]
            .0
            .partial_cmp(&out_clips[*a].0)
            .unwrap_or(Ordering::Equal)
    });
    let mut ranks = vec![0usize; out_clips.len()];
    for (rank, &idx) in indices.iter().enumerate() {
        ranks[idx] = rank + 1;
    }

    for (i, clip) in out_clips.iter().enumerate() {
        clip.1.set_item("content_rank", ranks[i])?;
        result.append(&clip.1)?;
    }

    Ok(result)
}

pub fn register_submodule(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(merge_invalid_ranges, module)?)?;
    module.add_function(wrap_pyfunction!(generate_and_stabilize_clips, module)?)?;
    module.add_function(wrap_pyfunction!(attach_transcript_and_score, module)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_merge_ranges_internal() {
        let ranges = vec![
            Range {
                start: 0.0,
                end: 1.0,
            },
            Range {
                start: 1.1,
                end: 2.0,
            },
            Range {
                start: 3.0,
                end: 4.0,
            },
        ];
        let merged = merge_ranges_internal(&ranges, 0.12, 0.35);
        assert_eq!(merged.len(), 2);
        assert_eq!(merged[0].end, 2.0);
    }

    #[test]
    fn test_intersect_ranges() {
        let left = vec![Range {
            start: 0.0,
            end: 2.0,
        }];
        let right = vec![Range {
            start: 1.0,
            end: 3.0,
        }];
        let inter = intersect_ranges(&left, &right);
        assert_eq!(inter.len(), 1);
        assert_eq!(inter[0].start, 1.0);
        assert_eq!(inter[0].end, 2.0);
    }
}
