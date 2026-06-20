import { API } from "./api.js";
import { renderAiDisclosure } from "./ai-disclosure.js";
import { t } from "./i18n.js";
import { setButtonLoading, showToast } from "./toast.js";
import { basename, escapeHtml } from "./utils.js";

export function renderCovers(jobName, files, cover, manifest, cuts, transcript, health) {
  const state = cover || { status: "idle", candidates: {}, selected: {} };
  const defaultTitle = escapeHtml(state.title || defaultCoverTitle(manifest, cuts, transcript));
  const status = state.status || "idle";
  const coverSettings = health?.settings?.covers || {};
  const keyStatus = coverKeyStatus(health?.settings || {});
  const missingKey = keyStatus.missing;
  const message = [
    missingKey ? `<div class="notice">${t(keyStatus.messageKey)}</div>` : "",
    state.error ? `<div class="error">${escapeHtml(state.error)}</div>` : status === "generating" ? `<div class="notice">${t("cover.generating")}</div>` : ""
  ].join("");
  const candidateHtml = ["9:16", "16:9"].map((aspect) => renderCoverAspect(jobName, files, state, aspect)).join("");
  return `
    <div class="cover-form">
      <div class="field">
        <label for="cover-title">${t("cover.video_title")}</label>
        <input id="cover-title" type="text" value="${defaultTitle}" placeholder="${t("cover.title_placeholder")}" />
      </div>
      <div class="cover-controls">
        <div class="field compact">
          <label for="cover-style">${t("cover.style")}</label>
          <select id="cover-style">
            ${["short_video", "clean", "cinematic", "gaming"].map((value) => `<option value="${value}" ${state.style === value ? "selected" : ""}>${t(`cover.style_${value}`)}</option>`).join("")}
          </select>
        </div>
        <div class="field compact">
          <label for="cover-count">${t("cover.count")}</label>
          <select id="cover-count">
            <option value="3" ${(state.count || 3) !== 5 ? "selected" : ""}>3</option>
            <option value="5" ${(state.count || 3) === 5 ? "selected" : ""}>5</option>
          </select>
        </div>
        <label class="check"><input id="cover-aspect-vertical" type="checkbox" value="9:16" ${(state.aspects || ["9:16", "16:9"]).includes("9:16") ? "checked" : ""} /> ${t("cover.vertical")}</label>
        <label class="check"><input id="cover-aspect-landscape" type="checkbox" value="16:9" ${(state.aspects || ["9:16", "16:9"]).includes("16:9") ? "checked" : ""} /> ${t("cover.landscape")}</label>
        <button class="button primary" id="generate-covers" type="button" ${status === "generating" || missingKey ? "disabled" : ""}>${status === "generating" ? t("common.loading") : t("cover.generate")}</button>
      </div>
      <p class="muted">${t("cover.usage_note")}</p>
      ${renderAiDisclosure("image")}
      <div id="cover-message">${message}</div>
    </div>
    <div class="cover-grid">${candidateHtml || `<div class="empty">${t("cover.no_candidates")}</div>`}</div>
  `;
}

export function coverKeyStatus(settings = {}) {
  const covers = settings.covers || {};
  const optional = settings.optional_modules || {};
  const provider = String(covers.provider || "openai").toLowerCase();
  const coverKey = covers.cover_api_key_configured === true;
  const openaiKey = covers.openai_api_key_configured === true || optional.openai_api_key_configured === true;
  const googleKey = covers.google_api_key_configured === true || optional.google_api_key_configured === true;
  if (provider === "google") {
    return {
      missing: !(googleKey || coverKey),
      messageKey: "cover.key_missing_google"
    };
  }
  if (provider === "openrouter") {
    return {
      missing: !coverKey,
      messageKey: "cover.key_missing_openrouter"
    };
  }
  return {
    missing: !(openaiKey || coverKey),
    messageKey: "cover.key_missing_openai"
  };
}

export function bindCoverActions(root, jobName, reload) {
  const handler = async (event) => {
    if (event.target.id === "generate-covers") {
      if (!window.confirm(t("cover.confirm_generate"))) return;
      const button = event.target;
      setButtonLoading(button, true, t("common.loading"));
      try {
        await API.generateCovers(jobName, collectCoverOptions());
        setCoverMessage(t("cover.started"));
        showToast(t("cover.started"), "success");
        await reload();
      } catch (error) {
        setCoverMessage(`${t("cover.failed")}${escapeHtml(error.message)}`, true);
        showToast(`${t("cover.failed")}${error.message}`, "error");
      } finally {
        setButtonLoading(button, false);
      }
      return;
    }
    const selectButton = event.target.closest("[data-select-cover]");
    if (!selectButton) return;
    setButtonLoading(selectButton, true);
    try {
      await API.selectCover(jobName, {
        aspect: selectButton.dataset.aspect,
        candidate: selectButton.dataset.file
      });
      setCoverMessage(t("cover.select_ok"));
      showToast(t("cover.select_ok"), "success");
      await reload();
    } catch (error) {
      setCoverMessage(`${t("cover.select_failed")}${escapeHtml(error.message)}`, true);
      showToast(`${t("cover.select_failed")}${error.message}`, "error");
    } finally {
      setButtonLoading(selectButton, false);
    }
  };
  root.addEventListener("click", handler);
  return () => root.removeEventListener("click", handler);
}

function renderCoverAspect(jobName, files, cover, aspect) {
  const candidates = cover?.candidates?.[aspect] || [];
  if (!candidates.length) return "";
  const selected = cover?.selected?.[aspect] || "";
  return `
    <div class="cover-aspect ${aspect === "16:9" ? "landscape" : "vertical"}">
      <div class="cover-aspect-head">
        <h3>${aspect === "9:16" ? t("cover.vertical") : t("cover.landscape")}</h3>
        ${selected ? `<span class="badge done">${t("cover.selected")}: ${escapeHtml(selected)}</span>` : ""}
      </div>
      <div class="cover-candidates">
        ${candidates.map((candidate) => renderCoverCandidate(jobName, files, candidate, aspect, selected)).join("")}
      </div>
    </div>
  `;
}

function renderCoverCandidate(jobName, files, candidate, aspect, selected) {
  const file = candidate.file || "";
  if (!file || !files.has(file)) return "";
  const info = files.get(file);
  const cacheKey = `${info.size_bytes || 0}-${info.modified_at || 0}`;
  const url = API.jobFileUrl(jobName, file, false, cacheKey);
  const downloadUrl = API.jobFileUrl(jobName, file, true, cacheKey);
  const isSelected = selected === file;
  return `
    <article class="cover-card ${isSelected ? "selected" : ""}">
      <img src="${url}" alt="${escapeHtml(file)}" loading="lazy" />
      <div class="cover-card-actions">
        <button class="button compact-button ${isSelected ? "primary" : ""}" type="button" data-select-cover data-aspect="${aspect}" data-file="${escapeHtml(file)}">${isSelected ? t("cover.selected") : t("cover.select")}</button>
        <a class="button compact-button download-link file-image" download href="${downloadUrl}">${t("common.download")}</a>
      </div>
    </article>
  `;
}

function collectCoverOptions() {
  const aspects = Array.from(document.querySelectorAll("#cover-aspect-vertical, #cover-aspect-landscape"))
    .filter((input) => input.checked)
    .map((input) => input.value);
  return {
    title: document.getElementById("cover-title")?.value?.trim() || "",
    style: document.getElementById("cover-style")?.value || "short_video",
    count: Number(document.getElementById("cover-count")?.value || 3),
    aspects: aspects.length ? aspects : ["9:16", "16:9"]
  };
}

function setCoverMessage(message, isError = false) {
  const box = document.getElementById("cover-message");
  if (!box) return;
  box.innerHTML = `<div class="${isError ? "error" : "notice"}">${message}</div>`;
}

function defaultCoverTitle(manifest, cuts, transcript) {
  const source = manifest?.source_name || "";
  const clipText = (cuts?.clips || []).find((clip) => clip?.transcript_text)?.transcript_text || "";
  const transcriptText = (transcript?.segments || []).find((segment) => segment?.text)?.text || "";
  return basename(source || clipText || transcriptText || "video-cover").replace(/\.[^.]+$/, "").replace(/[_-]+/g, " ").slice(0, 48);
}
