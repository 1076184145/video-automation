const PREFIX = "video-automation.review-draft.v1";

function storageKey(jobName, kind) {
  return `${PREFIX}:${encodeURIComponent(jobName)}:${kind}`;
}

export function saveReviewDraft(jobName, kind, data, storage = localStorage) {
  const draft = {
    job_name: jobName,
    kind,
    data,
    updated_at: new Date().toISOString(),
  };
  storage.setItem(storageKey(jobName, kind), JSON.stringify(draft));
  return draft;
}

export function loadReviewDraft(jobName, kind, storage = localStorage) {
  try {
    const raw = storage.getItem(storageKey(jobName, kind));
    if (!raw) return null;
    const draft = JSON.parse(raw);
    if (draft?.job_name !== jobName || draft?.kind !== kind || !Array.isArray(draft.data)) return null;
    return draft;
  } catch {
    return null;
  }
}

export function clearReviewDraft(jobName, kind, storage = localStorage) {
  storage.removeItem(storageKey(jobName, kind));
}

// A transcript draft may be dropped only after the server confirms that a deferred save finished
// ("applied"), and only if the saved transcript still holds exactly the draft's edits. Matching text
// alone is not enough: a failed apply can leave transcript.json written while later steps never ran.
export function transcriptDraftConfirmed(draft, segments, saveState) {
  if (saveState?.state !== "applied") return false;
  if (!Array.isArray(draft) || !Array.isArray(segments) || draft.length !== segments.length) return false;
  return draft.every((item, index) => {
    const segment = segments[index] || {};
    return Math.abs(Number(item?.start) - Number(segment.start)) < 0.0005
      && Math.abs(Number(item?.end) - Number(segment.end)) < 0.0005
      && String(item?.text || "").trim() === String(segment.text || "").trim();
  });
}

export function createDraftSaver(jobName, kind, readData, delay = 250) {
  let timer = null;
  const flush = () => {
    clearTimeout(timer);
    timer = null;
    saveReviewDraft(jobName, kind, readData());
  };
  const schedule = () => {
    clearTimeout(timer);
    timer = setTimeout(flush, delay);
  };
  const cancel = () => {
    clearTimeout(timer);
    timer = null;
  };
  const dispose = () => {
    if (timer) flush();
  };
  return { schedule, flush, cancel, dispose };
}
