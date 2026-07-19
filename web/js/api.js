export const API = {
  async getCapabilities() {
    return requestJson("/api/v1/capabilities");
  },
  async getProjects(options) {
    return requestJson("/api/v1/projects", options);
  },
  async createProject(payload) {
    return postJson("/api/v1/projects", payload);
  },
  async updateProject(id, payload) {
    return postJson(`/api/v1/projects/${encodeURIComponent(id)}`, payload);
  },
  async deleteProject(id) {
    return requestJson(`/api/v1/projects/${encodeURIComponent(id)}`, { method: "DELETE" });
  },
  async getCreatorKits(options) {
    return requestJson("/api/v1/creator-kits", options);
  },
  async createCreatorKit(payload) {
    return postJson("/api/v1/creator-kits", payload);
  },
  async updateCreatorKit(id, payload) {
    return postJson(`/api/v1/creator-kits/${encodeURIComponent(id)}`, payload);
  },
  async deleteCreatorKit(id) {
    return requestJson(`/api/v1/creator-kits/${encodeURIComponent(id)}`, { method: "DELETE" });
  },
  async getRecipes(options) {
    return requestJson("/api/v1/recipes", options);
  },
  async createRecipe(payload) {
    return postJson("/api/v1/recipes", payload);
  },
  async updateRecipe(id, payload) {
    return postJson(`/api/v1/recipes/${encodeURIComponent(id)}`, payload);
  },
  async deleteRecipe(id) {
    return requestJson(`/api/v1/recipes/${encodeURIComponent(id)}`, { method: "DELETE" });
  },
  async importRecipes(items) {
    return postJson("/api/v1/recipes/import", { items });
  },
  async getQueue(options) {
    return requestJson("/api/v1/queue", options);
  },
  async pauseQueue() {
    return postJson("/api/v1/queue/pause", {});
  },
  async resumeQueue() {
    return postJson("/api/v1/queue/resume", {});
  },
  async pauseQueueItem(id) {
    return postJson(`/api/v1/queue/${encodeURIComponent(id)}/pause`, {});
  },
  async resumeQueueItem(id) {
    return postJson(`/api/v1/queue/${encodeURIComponent(id)}/resume`, {});
  },
  async cancelQueueItem(id) {
    return postJson(`/api/v1/queue/${encodeURIComponent(id)}/cancel`, {});
  },
  async retryQueueStage(id, stage) {
    return postJson(`/api/v1/queue/${encodeURIComponent(id)}/retry-stage`, { stage });
  },
  async reorderQueue(ids) {
    return postJson("/api/v1/queue/reorder", { ids });
  },
  async getPublishTargets(options) {
    return requestJson("/api/v1/publish-targets", options);
  },
  async savePublishCredentials(provider, payload) {
    return postJson(`/api/v1/publish-targets/${encodeURIComponent(provider)}/credentials`, payload);
  },
  async deletePublishCredentials(provider, accountId) {
    return requestJson(`/api/v1/publish-targets/${encodeURIComponent(provider)}/credentials/${encodeURIComponent(accountId)}`, { method: "DELETE" });
  },
  async getPublishAttempts(options) {
    return requestJson("/api/v1/publish-attempts", options);
  },
  async createPublishAttempt(payload) {
    return postJson("/api/v1/publish-attempts", payload);
  },
  async startPublishAttempt(id) {
    return postJson(`/api/v1/publish-attempts/${encodeURIComponent(id)}/start`, {});
  },
  async retryPublishAttempt(id) {
    return postJson(`/api/v1/publish-attempts/${encodeURIComponent(id)}/retry`, {});
  },
  async syncPublishAttempt(id) {
    return postJson(`/api/v1/publish-attempts/${encodeURIComponent(id)}/sync`, {});
  },
  async getPublishPackages(options) {
    return requestJson("/publish/packages", options);
  },
  async getRevisions(jobName, options) {
    return requestJson(`/api/v1/jobs/${encodeURIComponent(jobName)}/revisions`, options);
  },
  async getJobQuality(jobName, options) {
    return requestJson(`/api/v1/jobs/${encodeURIComponent(jobName)}/quality`, options);
  },
  async getPreferences(options) {
    return requestJson("/api/v1/preferences", options);
  },
  async exportPreferences(options) {
    return requestJson("/api/v1/preferences/export", options);
  },
  async clearPreferences() {
    return requestJson("/api/v1/preferences", { method: "DELETE" });
  },
  async getRevision(jobName, revisionId) {
    return requestJson(`/api/v1/jobs/${encodeURIComponent(jobName)}/revisions/${encodeURIComponent(revisionId)}`);
  },
  async getJobs(options) {
    return requestJson("/jobs", options);
  },
  async getJob(name, options) {
    return requestJson(`/jobs/${encodeURIComponent(name)}`, options);
  },
  async getRecordings(options) {
    return requestJson("/recordings", options);
  },
  async uploadRecording(file, onProgress, options) {
    const query = new URLSearchParams({ filename: file.name });
    return uploadWithProgress(`/recordings/upload?${query.toString()}`, file, onProgress, options);
  },
  async getJobFile(name, filename, options) {
    return requestJson(this.jobFileUrl(name, filename), options);
  },
  async submitJob(payload) {
    return requestJson("/process", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
  },
  async submitBatch(payload) {
    return requestJson("/process/batch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      timeout: 30000
    });
  },
  async approveJob(name) {
    return requestJson(`/jobs/${encodeURIComponent(name)}/approve`, { method: "POST" });
  },
  async updateCuts(name, clips) {
    return requestJson(`/jobs/${encodeURIComponent(name)}/cuts`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ clips })
    });
  },
  async updateTranscript(name, segments) {
    return requestJson(`/jobs/${encodeURIComponent(name)}/transcript`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ segments })
    });
  },
  async saveClipFeedback(name, payload) {
    return postJson(`/jobs/${encodeURIComponent(name)}/clip-feedback`, payload);
  },
  async rerunStage(name, stage) {
    return requestJson(`/jobs/${encodeURIComponent(name)}/rerun`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ stage })
    });
  },
  async generateCovers(name, payload) {
    return postJson(`/jobs/${encodeURIComponent(name)}/covers/generate`, payload, 30000);
  },
  async selectCover(name, payload) {
    return postJson(`/jobs/${encodeURIComponent(name)}/covers/select`, payload);
  },
  async generateSegments(name, payload) {
    return postJson(`/jobs/${encodeURIComponent(name)}/segments/generate`, payload, 120000);
  },
  async generateMetadata(name, payload) {
    return postJson(`/jobs/${encodeURIComponent(name)}/metadata/generate`, payload, 120000);
  },
  async saveMetadata(name, payload) {
    return postJson(`/jobs/${encodeURIComponent(name)}/metadata`, payload);
  },
  async generateHighlights(name, payload) {
    return postJson(`/jobs/${encodeURIComponent(name)}/highlights/generate`, payload, 120000);
  },
  async generateHighlightCut(name, payload) {
    return postJson(`/jobs/${encodeURIComponent(name)}/highlights/cut`, payload, 30000);
  },
  async renderHighlightCut(name, payload) {
    return postJson(`/jobs/${encodeURIComponent(name)}/highlights/render`, payload, 30000);
  },
  async generatePublishPackage(name, payload) {
    return postJson(`/jobs/${encodeURIComponent(name)}/publish/package`, payload, 60000);
  },
  async generateProjectExport(name, payload) {
    return postJson(`/jobs/${encodeURIComponent(name)}/project-export/generate`, payload, 120000);
  },
  async translateSubtitles(name, payload) {
    return postJson(`/jobs/${encodeURIComponent(name)}/subtitles/translate`, payload, 300000);
  },
  async renderTranslatedSubtitles(name, payload) {
    return postJson(`/jobs/${encodeURIComponent(name)}/subtitles/render-translated`, payload, 30000);
  },
  async deleteJob(name) {
    return requestJson(`/jobs/${encodeURIComponent(name)}`, { method: "DELETE", timeout: 120000 });
  },
  async cancelJob(name) {
    return postJson(`/jobs/${encodeURIComponent(name)}/cancel`, {});
  },
  async getHealth(options) {
    return requestJson("/health", options);
  },
  async installHealthTools(payload = {}) {
    return postJson("/health/install-tools", payload, 30000);
  },
  async updateSettings(payload) {
    return postJson("/settings", payload, 30000);
  },
  openEvents() {
    return new EventSource("/events");
  },
  jobFileUrl(name, filename, download = false, cacheKey = "") {
    const params = new URLSearchParams();
    if (download) params.set("download", "1");
    if (cacheKey) params.set("v", String(cacheKey));
    const query = params.toString();
    return `/jobs/${encodeURIComponent(name)}/files/${encodeURIComponent(filename)}${query ? `?${query}` : ""}`;
  }
};

function postJson(url, payload, timeout) {
  return requestJson(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    timeout
  });
}

function uploadWithProgress(url, file, onProgress, { signal } = {}) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    let settled = false;
    const finish = (callback) => (value) => {
      if (settled) return;
      settled = true;
      signal?.removeEventListener("abort", abortUpload);
      callback(value);
    };
    const abortUpload = () => xhr.abort();
    if (signal?.aborted) {
      reject(abortError(signal));
      return;
    }
    signal?.addEventListener("abort", abortUpload, { once: true });
    xhr.open("POST", url);
    xhr.setRequestHeader("Content-Type", file.type || "application/octet-stream");
    xhr.responseType = "json";
    xhr.upload.onprogress = (event) => {
      if (!event.lengthComputable || typeof onProgress !== "function") return;
      onProgress(Math.min(100, Math.max(0, event.loaded / event.total * 100)));
    };
    xhr.onload = finish(() => {
      const payload = xhr.response || parseJson(xhr.responseText);
      if (xhr.status >= 200 && xhr.status < 300) {
        if (typeof onProgress === "function") onProgress(100);
        resolve(payload);
        return;
      }
      reject(new Error(apiErrorMessage(payload, `${xhr.status} ${xhr.statusText}`)));
    });
    xhr.onerror = finish(() => reject(new Error("Network error")));
    xhr.onabort = finish(() => reject(abortError(signal)));
    xhr.send(file);
  });
}

function parseJson(text) {
  try {
    return JSON.parse(text || "{}");
  } catch {
    return {};
  }
}

export async function requestJson(url, options = {}) {
  const { timeout: timeoutOption, retries: retryOption, signal: externalSignal, ...fetchOptions } = options;
  const timeoutMs = timeoutOption === undefined ? 15000 : Number(timeoutOption);
  let retries = retryOption ?? 1;
  const isNonIdempotent = fetchOptions.method && fetchOptions.method !== "GET";
  if (isNonIdempotent) retries = 0;

  while (retries >= 0) {
    if (externalSignal?.aborted) throw abortError(externalSignal);
    const controller = new AbortController();
    let timeout = null;
    let abortListener = null;
    let timedOut = false;
    if (externalSignal) {
      abortListener = () => controller.abort(externalSignal.reason);
      externalSignal.addEventListener("abort", abortListener, { once: true });
    }
    if (Number.isFinite(timeoutMs) && timeoutMs > 0) {
      timeout = setTimeout(() => {
        timedOut = true;
        controller.abort();
      }, timeoutMs);
    }
    try {
      const response = await fetch(url, { ...fetchOptions, signal: controller.signal });
      if (!response.ok) {
        let message = `${response.status} ${response.statusText}`;
        let payload = null;
        try {
          payload = await response.json();
          message = apiErrorMessage(payload, message);
        } catch {}
        const requestError = new Error(message);
        requestError.payload = payload;
        requestError.status = response.status;
        throw requestError;
      }
      const payload = await response.json();
      cleanup(timeout, externalSignal, abortListener);
      return payload;
    } catch (error) {
      cleanup(timeout, externalSignal, abortListener);
      if (externalSignal?.aborted) throw abortError(externalSignal);
      const finalError = timedOut ? requestTimeoutError(timeoutMs) : error;
      if (retries-- <= 0 || !shouldRetry(error, timedOut)) throw finalError;
      await abortableDelay(1000, externalSignal);
    }
  }
}

export function isAbortError(error, signal) {
  return Boolean(signal?.aborted || error?.name === "AbortError");
}

function abortError(signal) {
  const reason = signal?.reason;
  if (reason instanceof Error && reason.name === "AbortError") return reason;
  return new DOMException("Request aborted", "AbortError");
}

function requestTimeoutError(timeoutMs) {
  const error = new Error("Request Timeout");
  error.name = "RequestTimeoutError";
  error.timeoutMs = timeoutMs;
  return error;
}

function shouldRetry(error, timedOut) {
  if (timedOut) return true;
  const status = Number(error?.status);
  return !Number.isFinite(status) || status >= 500;
}

function abortableDelay(ms, signal) {
  if (signal?.aborted) return Promise.reject(abortError(signal));
  return new Promise((resolve, reject) => {
    const timeout = setTimeout(() => {
      signal?.removeEventListener("abort", handleAbort);
      resolve();
    }, ms);
    const handleAbort = () => {
      clearTimeout(timeout);
      signal?.removeEventListener("abort", handleAbort);
      reject(abortError(signal));
    };
    signal?.addEventListener("abort", handleAbort, { once: true });
  });
}

export function apiErrorMessage(payload, fallback) {
  if (typeof payload?.error === "string" && payload.error.trim()) return payload.error;
  if (payload?.error && typeof payload.error === "object") {
    const message = String(payload.error.message || "").trim();
    if (message) return message;
  }
  return fallback;
}

function cleanup(timeout, externalSignal, abortListener) {
  if (timeout) clearTimeout(timeout);
  if (externalSignal && abortListener) externalSignal.removeEventListener("abort", abortListener);
}

window.addEventListener("offline", () => {
  document.body.classList.add("offline");
});

window.addEventListener("online", () => {
  document.body.classList.remove("offline");
});
