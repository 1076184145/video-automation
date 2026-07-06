import { API } from "./api.js";
import { clearReviewDraft } from "./review-drafts.js";
import { t } from "./i18n.js";
import { setButtonLoading, showToast } from "./toast.js";
import { escapeHtml, formatDate } from "./utils.js";

export function renderRevisionHistory(revisions = []) {
  if (!revisions.length) return `<div class="empty">${t("revisions.empty")}</div>`;
  return `
    <div class="revision-list">
      ${revisions.map((revision) => `
        <div class="revision-row">
          <div>
            <strong>${t("revisions.revision")} ${Number(revision.revision || 0)}</strong>
            <span class="badge optional">${t(`revisions.kind_${revision.kind}`)}</span>
            <p>${escapeHtml(revision.summary || "")} · ${escapeHtml(formatDate(revision.created_at))}</p>
          </div>
          <button class="button compact-button" type="button" data-restore-revision="${escapeHtml(revision.id)}">${t("revisions.restore")}</button>
        </div>
      `).join("")}
    </div>`;
}

export function bindRevisionHistory(root, jobName, reload) {
  const handler = async (event) => {
    const button = event.target?.closest?.("[data-restore-revision]");
    if (!button) return;
    if (!window.confirm(t("revisions.restore_confirm"))) return;
    setButtonLoading(button, true, t("common.loading"));
    try {
      const revision = await API.getRevision(jobName, button.dataset.restoreRevision);
      if (revision.kind === "transcript") {
        await API.updateTranscript(jobName, revision.payload?.segments || []);
      } else if (revision.kind === "cuts") {
        await API.updateCuts(jobName, revision.payload?.clips || []);
      }
      clearReviewDraft(jobName, revision.kind);
      showToast(t("revisions.restored"), "success");
      await reload();
    } catch (error) {
      showToast(`${t("revisions.restore_failed")} ${error.message}`, "error");
    } finally {
      setButtonLoading(button, false);
    }
  };
  root.addEventListener("click", handler);
  return () => root.removeEventListener("click", handler);
}
