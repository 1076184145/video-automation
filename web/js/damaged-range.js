// Subtract a source-time interval without deleting the original media or rows.
// Keeping excluded pieces makes the operation inspectable and reversible.
export function excludeDamagedRange(clips, start, end) {
  if (!Number.isFinite(start) || !Number.isFinite(end) || start < 0 || end <= start) {
    throw new RangeError("invalid range");
  }
  let affected = 0;
  const result = clips.flatMap((clip) => {
    if (clip.keep === false || clip.end <= start || clip.start >= end) return [{ ...clip }];
    affected += 1;
    const left = Math.max(clip.start, start);
    const right = Math.min(clip.end, end);
    const ranges = [[clip.start, left, true], [left, right, false], [right, clip.end, true]];
    return ranges.filter(([a, b]) => b > a).map(([a, b, keep]) => ({
      ...clip, start: a, end: b, duration: b - a, keep,
      reason: keep ? clip.reason : "manually excluded damaged range",
      // A manual whole-clip subtitle cannot be mapped to new boundaries safely.
      // The save endpoint rebuilds text from timed transcript segments instead.
      ...(a !== clip.start || b !== clip.end
        ? { subtitle_override: false, subtitle_text: "", transcript_text: "" } : {}),
    }));
  });
  return { clips: result, affected };
}
