import { eventHub } from "./event-hub.js";
import { t } from "./i18n.js";
import { basename, isTerminal, jobName, statusGroup } from "./utils.js";

// Background job notifications: while the tab is hidden, every job that
// reaches a terminal state bumps the "(n)" counter in the document title and,
// with permission, fires a native Notification that deep-links into the job.

export function installBrowserNotifications() {
  let hiddenTerminalCount = 0;
  const notified = new Set();
  const originalTitle = () => {
    const appTitle = t("app.title");
    return document.title.replace(/^\(\d+\)\s+/, "") || appTitle;
  };

  const updateBadge = () => {
    const cleanTitle = originalTitle();
    document.title = hiddenTerminalCount > 0 ? `(${hiddenTerminalCount}) ${cleanTitle}` : cleanTitle;
  };

  const resetBadge = () => {
    hiddenTerminalCount = 0;
    updateBadge();
  };

  const notifyJob = (job) => {
    if (!job || !job.job_dir || !isTerminal(job.status)) return;
    const key = `${job.job_dir}|${job.status}`;
    if (notified.has(key)) return;
    notified.add(key);
    if (notified.size > 200) {
      const first = notified.values().next().value;
      if (first) notified.delete(first);
    }

    if (document.visibilityState === "visible") return;
    hiddenTerminalCount += 1;
    updateBadge();

    if (!("Notification" in window) || Notification.permission !== "granted") return;
    const group = statusGroup(job.status);
    const title = t(`notify.job_${group}`);
    const body = basename(job.source_path) || jobName(job);
    let notification;
    try {
      notification = new Notification(title, {
        body,
        tag: `video-automation-${job.job_dir}`,
        silent: true
      });
    } catch (error) {
      console.warn("Browser notification could not be created", error);
      return;
    }
    notification.onclick = () => {
      window.focus();
      location.hash = `#/jobs/${encodeURIComponent(jobName(job))}`;
      notification.close();
    };
  };

  eventHub.subscribe("job", notifyJob);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") resetBadge();
  });
  window.addEventListener("languagechange", updateBadge);
}
