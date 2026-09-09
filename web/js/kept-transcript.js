// Project text onto source-time kept intervals; never mutate the source transcript.
export function projectKeptTranscript(segments, clips) {
  return clips.filter((clip) => clip.keep !== false && clip.end > clip.start).flatMap((clip) => {
    if (clip.subtitle_override) return [{ start: clip.start, end: clip.end, text: clip.subtitle_text || "", partial: false }];
    return segments.flatMap((segment) => {
      const start = Math.max(Number(segment.start), clip.start);
      const end = Math.min(Number(segment.end), clip.end);
      if (!(end > start)) return [];
      const partial = start > segment.start || end < segment.end;
      const words = Array.isArray(segment.words) ? segment.words : [];
      const timed = words.length && words.every((word) => Number.isFinite(word.start) && Number.isFinite(word.end) && word.end > word.start);
      if (partial && timed) {
        const text = words.filter((word) => word.end > start && word.start < end)
          .map((word) => word.word ?? word.text ?? "").join("").trim();
        return text ? [{ start, end, text, partial: false }] : [];
      }
      return [{ start, end, text: segment.text || "", partial }];
    });
  });
}
