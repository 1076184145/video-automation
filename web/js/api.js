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
    return requestJson(`/jobs/${encodeURIComponent(name)}/approve`, {
      method: "POST"
    });
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
  async rerunStage(name, stage) {
    return requestJson(`/jobs/${encodeURIComponent(name)}/rerun`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ stage })
    });
  },
  async generateCovers(name, payload) {
    return requestJson(`/jobs/${encodeURIComponent(name)}/covers/generate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      timeout: 30000
    });
  },
  async selectCover(name, payload) {
    return requestJson(`/jobs/${encodeURIComponent(name)}/covers/select`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
  },
  async generateSegments(name, payload) {
    return requestJson(`/jobs/${encodeURIComponent(name)}/segments/generate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      timeout: 120000
    });
  },
  async generateMetadata(name, payload) {
    return requestJson(`/jobs/${encodeURIComponent(name)}/metadata/generate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      timeout: 120000
    });
  },
  async saveMetadata(name, payload) {
    return requestJson(`/jobs/${encodeURIComponent(name)}/metadata`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
  },
  async generateHighlights(name, payload) {
    return requestJson(`/jobs/${encodeURIComponent(name)}/highlights/generate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      timeout: 120000
    });
  },
  async generatePublishPackage(name, payload) {
    return requestJson(`/jobs/${encodeURIComponent(name)}/publish/package`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      timeout: 60000
    });
  },
  async generateProjectExport(name, payload) {
    return requestJson(`/jobs/${encodeURIComponent(name)}/project-export/generate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      timeout: 120000
    });
  },
  async getDownloads() {
    return requestJson("/downloads");
  },
  async startDownload(payload) {
    return requestJson("/downloads", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      timeout: 30000
    });
  },
  async importDownload(id, payload) {
    return requestJson(`/downloads/${encodeURIComponent(id)}/import`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      timeout: 30000
    });
  },
  async deleteJob(name) {
    return requestJson(`/jobs/${encodeURIComponent(name)}`, {
      method: "DELETE"
    });
  },
  async getHealth(options) {
    return requestJson("/health", options);
  },
  jobFileUrl(name, filename, download = false, cacheKey = "") {
    const params = new URLSearchParams();
    if (download) params.set("download", "1");
    if (cacheKey) params.set("v", String(cacheKey));
    const query = params.toString();
    return `/jobs/${encodeURIComponent(name)}/files/${encodeURIComponent(filename)}${query ? `?${query}` : ""}`;
  }
};

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
  const isPost = fetchOptions.method && fetchOptions.method !== "GET";
  if (isPost) retries = 0; // 不重试非幂等请求

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
      if (timeout) clearTimeout(timeout);
      if (externalSignal && abortListener) externalSignal.removeEventListener("abort", abortListener);
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
      if (timeout) clearTimeout(timeout);
      if (externalSignal && abortListener) externalSignal.removeEventListener("abort", abortListener);
      if (retries-- <= 0) {
        throw new Error(error.name === 'AbortError' ? 'Request Timeout' : error.message);
      }
      // 等待 1s 后重试
      await new Promise(resolve => setTimeout(resolve, 1000));
    }
  }
}

// 监听网络状态
window.addEventListener('offline', () => {
  document.body.classList.add('offline');
});
window.addEventListener('online', () => {
  document.body.classList.remove('offline');
});
