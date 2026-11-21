import { t } from "./i18n.js";

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
