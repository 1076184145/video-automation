import { API, isAbortError } from "./api.js";
import { jobName } from "./utils.js";

export async function loadJobFile(jobName, files, filename, options = {}) {
  if (!files.has(filename)) return null;
  try {
    return await API.getJobFile(jobName, filename, options);
  } catch (error) {
    if (isAbortError(error, options.signal)) throw error;
    return null;
  }
}

export async function loadHealthSafe(options = {}) {
  try {
    return await API.getHealth({ ...options, timeout: 5000, retries: 0 });
  } catch (error) {
    if (isAbortError(error, options.signal)) throw error;
    return null;
  }
}

export function parseEventPayload(event) {
  try {
    return JSON.parse(event.data || "{}");
  } catch {
    return {};
  }
}

export function isJobEventForName(job, name) {
  return Boolean(job?.job_dir) && jobName(job) === name;
}

export function shouldApplyLiveJobEvent(runtimeStale) {
  return !runtimeStale;
}

export function isTypingTarget(target) {
  return Boolean(target?.closest?.("input, textarea, select, button, a, [contenteditable='true']"));
}
