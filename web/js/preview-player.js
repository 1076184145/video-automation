import { t } from "./i18n.js";
import { showToast } from "./toast.js";
import { formatTime } from "./utils.js";

export function renderVideoControls() {
  return `
    <div class="video-controls" data-video-controls>
      <button class="video-control-button" type="button" data-video-play aria-label="${t("video.play")}" title="${t("video.play")}">▶</button>
      <div class="video-time"><span data-video-current>0:00</span><span>/</span><span data-video-duration>0:00</span></div>
      <input class="video-scrubber" data-video-scrubber type="range" min="0" max="1000" value="0" aria-label="${t("video.seek")}" />
      <button class="video-control-button" type="button" data-video-mute aria-label="${t("video.mute")}" title="${t("video.mute")}">${t("video.sound_short")}</button>
      <input class="volume-slider" data-video-volume type="range" min="0" max="1" step="0.01" value="1" aria-label="${t("video.volume")}" />
      <button class="video-control-button" type="button" data-video-fullscreen aria-label="${t("video.fullscreen")}" title="${t("video.fullscreen")}">⛶</button>
    </div>
  `;
}

export function restoreNativeVideoControls(video) {
  const player = video.closest("[data-video-player]");
  player?.querySelector("[data-video-controls]")?.remove();
  video.setAttribute("controls", "");
}

export function bindCustomVideoControls(video) {
  const player = video.closest("[data-video-player]");
  const controls = player?.querySelector("[data-video-controls]");
  if (!player || !controls) return;
  video.removeAttribute("controls");
  if (video.dataset.customControlsBound === "1") {
    updateVideoControls(video);
    return;
  }
  video.dataset.customControlsBound = "1";

  const update = () => updateVideoControls(video);
  for (const eventName of ["play", "pause", "timeupdate", "durationchange", "loadedmetadata", "volumechange", "error"]) {
    video.addEventListener(eventName, update);
  }
  document.addEventListener("fullscreenchange", update);

  controls.querySelector("[data-video-play]")?.addEventListener("click", () => {
    if (video.paused) playPreviewVideo(video);
    else video.pause();
  });
  controls.querySelector("[data-video-mute]")?.addEventListener("click", () => {
    video.muted = !video.muted;
  });
  controls.querySelector("[data-video-fullscreen]")?.addEventListener("click", () => {
    if (document.fullscreenElement) {
      document.exitFullscreen?.();
    } else {
      if (player.requestFullscreen) player.requestFullscreen();
      else video.requestFullscreen?.();
    }
  });
  controls.querySelector("[data-video-scrubber]")?.addEventListener("input", (event) => {
    const duration = Number(video.duration || 0);
    if (!Number.isFinite(duration) || duration <= 0) return;
    video.currentTime = (Number(event.currentTarget.value || 0) / 1000) * duration;
  });
  controls.querySelector("[data-video-volume]")?.addEventListener("input", (event) => {
    const value = Math.max(0, Math.min(1, Number(event.currentTarget.value || 0)));
    video.volume = value;
    video.muted = value <= 0;
  });

  update();
}

async function playPreviewVideo(video) {
  const hasSource = Boolean(video.currentSrc || video.getAttribute("src"));
  if (!hasSource || video.error) {
    showToast(t("job.no_preview"), "warning");
    updateVideoControls(video);
    return;
  }
  try {
    await video.play();
  } catch (error) {
    showToast(`${t("common.error")} ${error?.message || ""}`.trim(), "error");
    updateVideoControls(video);
  }
}

function updateVideoControls(video) {
  const player = video.closest("[data-video-player]");
  const controls = player?.querySelector("[data-video-controls]");
  if (!controls) return;
  const duration = Number.isFinite(video.duration) ? video.duration : 0;
  const current = Number.isFinite(video.currentTime) ? video.currentTime : 0;
  const progress = duration > 0 ? Math.max(0, Math.min(100, (current / duration) * 100)) : 0;
  const hasSource = Boolean(video.currentSrc || video.getAttribute("src"));
  const canPlay = hasSource && !video.error;
  const canSeek = canPlay && duration > 0;
  const playButton = controls.querySelector("[data-video-play]");
  const muteButton = controls.querySelector("[data-video-mute]");
  const fullscreenButton = controls.querySelector("[data-video-fullscreen]");
  const scrubber = controls.querySelector("[data-video-scrubber]");
  const volume = controls.querySelector("[data-video-volume]");

  controls.classList.toggle("is-disabled", !canPlay);
  if (playButton) {
    playButton.disabled = !canPlay;
    playButton.textContent = video.paused ? "▶" : "Ⅱ";
    playButton.setAttribute("aria-label", t(video.paused ? "video.play" : "video.pause"));
    playButton.title = t(video.paused ? "video.play" : "video.pause");
  }
  const currentNode = controls.querySelector("[data-video-current]");
  const durationNode = controls.querySelector("[data-video-duration]");
  if (currentNode) currentNode.textContent = formatTime(current);
  if (durationNode) durationNode.textContent = duration > 0 ? formatTime(duration) : "0:00";
  if (scrubber) {
    scrubber.disabled = !canSeek;
    scrubber.value = duration > 0 ? String(Math.round((current / duration) * 1000)) : "0";
    scrubber.style.setProperty("--progress", `${progress}%`);
  }
  if (muteButton) {
    muteButton.disabled = !canPlay;
    const muted = video.muted || video.volume <= 0;
    muteButton.textContent = muted ? t("video.muted_short") : t("video.sound_short");
    muteButton.setAttribute("aria-label", t(muted ? "video.unmute" : "video.mute"));
    muteButton.title = t(muted ? "video.unmute" : "video.mute");
  }
  if (volume) {
    volume.disabled = !canPlay;
    volume.value = String(video.muted ? 0 : video.volume);
  }
  if (fullscreenButton) {
    fullscreenButton.disabled = !canPlay;
    const active = document.fullscreenElement === player;
    fullscreenButton.setAttribute("aria-label", t(active ? "video.exit_fullscreen" : "video.fullscreen"));
    fullscreenButton.title = t(active ? "video.exit_fullscreen" : "video.fullscreen");
  }
}
