export const API = {
  async getJobs() {
    return requestJson("/jobs");
  },
  async getJob(name) {
    return requestJson(`/jobs/${encodeURIComponent(name)}`);
  },
  async getRecordings() {
    return requestJson("/recordings");
  },
  async uploadRecording(file, onProgress) {
    const query = new URLSearchParams({ filename: file.name });
    return uploadWithProgress(`/recordings/upload?${query.toString()}`, file, onProgress);
  },
  async getJobFile(name, filename) {
    return requestJson(this.jobFileUrl(name, filename));
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
    return requestJson(`/jobs/${encodeURIComponent(name)}`, { method: "DELETE" });
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

function uploadWithProgress(url, file, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", url);
    xhr.setRequestHeader("Content-Type", file.type || "application/octet-stream");
    xhr.responseType = "json";
    xhr.upload.onprogress = (event) => {
      if (!event.lengthComputable || typeof onProgress !== "function") return;
      onProgress(Math.min(100, Math.max(0, event.loaded / event.total * 100)));
    };
    xhr.onload = () => {
      const payload = xhr.response || parseJson(xhr.responseText);
      if (xhr.status >= 200 && xhr.status < 300) {
        if (typeof onProgress === "function") onProgress(100);
        resolve(payload);
        return;
      }
      reject(new Error(payload?.error || `${xhr.status} ${xhr.statusText}`));
    };
    xhr.onerror = () => reject(new Error("Network error"));
    xhr.onabort = () => reject(new Error("Upload aborted"));
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

async function requestJson(url, options = {}) {
  const { timeout: timeoutOption, retries: retryOption, signal: externalSignal, ...fetchOptions } = options;
  const timeoutMs = timeoutOption === undefined ? 15000 : Number(timeoutOption);
  let retries = retryOption ?? 1;
  const isNonIdempotent = fetchOptions.method && fetchOptions.method !== "GET";
  if (isNonIdempotent) retries = 0;

  while (retries >= 0) {
    const controller = new AbortController();
    let timeout = null;
    let abortListener = null;
    if (externalSignal) {
      if (externalSignal.aborted) controller.abort();
      abortListener = () => controller.abort();
      externalSignal.addEventListener("abort", abortListener, { once: true });
    }
    if (Number.isFinite(timeoutMs) && timeoutMs > 0) {
      timeout = setTimeout(() => controller.abort(), timeoutMs);
    }
    try {
      const response = await fetch(url, { ...fetchOptions, signal: controller.signal });
      cleanup(timeout, externalSignal, abortListener);
      if (!response.ok) {
        let message = `${response.status} ${response.statusText}`;
        try {
          const payload = await response.json();
          if (payload.error) message = payload.error;
        } catch {}
        throw new Error(message);
      }
      return await response.json();
    } catch (error) {
      cleanup(timeout, externalSignal, abortListener);
      if (retries-- <= 0) {
        throw new Error(error.name === "AbortError" ? "Request Timeout" : error.message);
      }
      await new Promise((resolve) => setTimeout(resolve, 1000));
    }
  }
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
