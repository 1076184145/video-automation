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
