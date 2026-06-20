import { API } from "./api.js";
import { jobName } from "./utils.js";

export async function loadJobFile(jobName, files, filename) {
  if (!files.has(filename)) return null;
  try {
    return await API.getJobFile(jobName, filename);
  } catch {
    return null;
  }
}

export async function loadHealthSafe() {
  try {
    return await API.getHealth({ timeout: 5000, retries: 0 });
  } catch {
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

export function isTypingTarget(target) {
  return Boolean(target?.closest?.("input, textarea, select, button, a, [contenteditable='true']"));
}
