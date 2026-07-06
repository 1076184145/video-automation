import { API } from "./api.js";
import { t } from "./i18n.js";
import { setButtonLoading, showToast } from "./toast.js";
import { basename, escapeHtml, formatDate } from "./utils.js";

export async function renderPublishCenterPage(_match, { signal } = {}) {
  const app = document.getElementById("app");
  app.innerHTML = `<div class="loading">${t("common.loading")}</div>`;
  let state = { targets: [], attempts: [], packages: [] };

  async function load() {
    try {
      const [targets, attempts, packages] = await Promise.all([
        API.getPublishTargets({ signal }),
        API.getPublishAttempts({ signal }),
        API.getPublishPackages({ signal }),
      ]);
      state = {
        targets: targets.items || [],
        attempts: attempts.items || [],
        packages: packages.items || [],
      };
      app.innerHTML = renderPublishCenter(state);
    } catch (error) {
      app.innerHTML = `<div class="error">${escapeHtml(error.message || t("common.error"))} <button class="button" id="retry-publish">${t("common.retry")}</button></div>`;
      document.getElementById("retry-publish")?.addEventListener("click", load);
    }
  }

  const handler = async (event) => {
    const credentialForm = event.target?.closest?.("#publish-credential-form");
    const attemptForm = event.target?.closest?.("#publish-attempt-form");
    if (event.type === "submit" && credentialForm) {
      event.preventDefault();
      const button = credentialForm.querySelector('[type="submit"]');
      setButtonLoading(button, true, t("common.loading"));
      try {
        const data = new FormData(credentialForm);
        await API.savePublishCredentials("bilibili", {
          account_id: data.get("account_id"),
          client_id: data.get("client_id"),
          access_token: data.get("access_token"),
          refresh_token: data.get("refresh_token"),
        });
        credentialForm.reset();
        showToast(t("publish.credentials_saved"), "success");
      } catch (error) {
        showToast(`${t("publish.action_failed")} ${error.message}`, "error");
      } finally {
        setButtonLoading(button, false);
      }
      return;
    }
    if (event.type === "submit" && attemptForm) {
      event.preventDefault();
      const button = attemptForm.querySelector('[type="submit"]');
      setButtonLoading(button, true, t("common.loading"));
      try {
        const data = new FormData(attemptForm);
        await API.createPublishAttempt({
          job_id: data.get("job_id"),
          provider: "bilibili",
          credential_ref: data.get("account_id") ? `bilibili:${data.get("account_id")}` : "",
          title: data.get("title"),
          description: data.get("description"),
        });
        await load();
      } catch (error) {
        showToast(`${t("publish.action_failed")} ${error.message}`, "error");
      } finally {
        setButtonLoading(button, false);
      }
      return;
    }
    const action = event.target?.closest?.("[data-publish-action]");
    if (!action) return;
    const row = action.closest("[data-publish-id]");
    const id = row?.dataset.publishId;
    if (!id) return;
    setButtonLoading(action, true, t("common.loading"));
    try {
      if (action.dataset.publishAction === "start") await API.startPublishAttempt(id);
      if (action.dataset.publishAction === "retry") await API.retryPublishAttempt(id);
      if (action.dataset.publishAction === "sync") await API.syncPublishAttempt(id);
      await load();
    } catch (error) {
      showToast(`${t("publish.action_failed")} ${error.message}`, "error");
      setButtonLoading(action, false);
    }
  };
  app.addEventListener("submit", handler);
  app.addEventListener("click", handler);
  await load();
  const timer = setInterval(() => {
    if (document.visibilityState === "visible" && state.attempts.some((item) => ["uploading", "processing"].includes(item.status))) {
      load();
    }
  }, 3000);
  return () => {
    clearInterval(timer);
    app.removeEventListener("submit", handler);
    app.removeEventListener("click", handler);
  };
}

export function renderPublishCenter({ targets = [], attempts = [], packages = [] } = {}) {
  return `
    <section class="page-head">
      <div>
        <h1 class="page-title">${t("publish.title")}</h1>
        <p class="page-subtitle">${t("publish.subtitle")}</p>
      </div>
    </section>
    <section class="publish-target-grid">
      ${targets.map(renderTarget).join("") || `<div class="empty">${t("publish.no_targets")}</div>`}
    </section>
    <section class="panel publish-setup-panel">
      <div>
        <h2>${t("publish.authorization")}</h2>
        <p class="muted">${t("publish.authorization_note")}</p>
      </div>
      <form id="publish-credential-form" class="publish-inline-form" autocomplete="off">
        <input name="account_id" required placeholder="${t("publish.account_id")}" />
        <input name="client_id" required placeholder="Client ID" />
        <input name="access_token" type="password" autocomplete="new-password" required placeholder="Access Token" />
        <input name="refresh_token" type="password" autocomplete="new-password" placeholder="Refresh Token" />
        <button class="button primary" type="submit">${t("common.save")}</button>
      </form>
    </section>
    <section class="panel publish-setup-panel">
      <div>
        <h2>${t("publish.new_attempt")}</h2>
        <p class="muted">${t("publish.new_attempt_note")}</p>
      </div>
      <form id="publish-attempt-form" class="publish-inline-form">
        <select name="job_id" required>
          <option value="">${t("publish.choose_package")}</option>
          ${packages.map((item) => {
            const jobName = packageJobName(item);
            const sourceName = item.source_name || item.source_video?.name || basename(item.job?.source_path) || jobName;
            return `<option value="${escapeHtml(jobName)}">${escapeHtml(sourceName)}</option>`;
          }).join("")}
        </select>
        <input name="account_id" placeholder="${t("publish.account_id")}" />
        <input name="title" required placeholder="${t("publish.video_title")}" />
        <input name="description" placeholder="${t("publish.description")}" />
        <button class="button primary" type="submit">${t("publish.create_attempt")}</button>
      </form>
    </section>
    <section class="publish-attempts">
      <div class="section-heading">
        <div><h2>${t("publish.attempts")}</h2><p>${t("publish.attempts_note")}</p></div>
        <span class="library-count">${attempts.length}</span>
      </div>
      <div class="publish-attempt-list">
        ${attempts.length ? attempts.map(renderAttempt).join("") : `<div class="empty">${t("publish.no_attempts")}</div>`}
      </div>
    </section>`;
}

function renderTarget(target) {
  return `
    <article class="panel publish-target-card">
      <div class="publish-target-mark">B</div>
      <div>
        <h2>${escapeHtml(target.name || target.id)}</h2>
        <p>${target.requires_platform_approval ? t("publish.approval_required") : t("publish.ready")}</p>
        ${target.manual_fallback ? `<span class="badge optional">${t("publish.manual_fallback")}</span>` : ""}
      </div>
    </article>`;
}

function renderAttempt(attempt) {
  const total = Math.max(0, Number(attempt.total_bytes || 0));
  const uploaded = Math.max(0, Number(attempt.uploaded_bytes || 0));
  const percent = total ? Math.min(100, Math.round(uploaded / total * 100)) : 0;
  const manualUrl = API.jobFileUrl(attempt.job_name, "publish_package.json", true);
  return `
    <article class="publish-attempt-row" data-publish-id="${escapeHtml(attempt.id)}">
      <div class="publish-attempt-main">
        <div class="publish-attempt-title">
          <strong>${escapeHtml(attempt.job_name)}</strong>
          <span class="badge ${publishStatusGroup(attempt.status)}">${t(`publish.status_${attempt.status}`)}</span>
        </div>
        <p>${escapeHtml(attempt.provider)} · ${escapeHtml(formatDate(attempt.updated_at))}</p>
        ${["uploading", "processing", "failed"].includes(attempt.status) && total ? `
          <div class="publish-progress"><span class="progress"><span style="width:${percent}%"></span></span><strong>${percent}%</strong></div>` : ""}
        ${attempt.error ? `<p class="publish-error">${escapeHtml(attempt.error)}</p>` : ""}
      </div>
      <div class="publish-attempt-actions">
        ${attempt.status === "draft" ? `<button class="button compact-button primary" type="button" data-publish-action="start">${t("publish.start")}</button>` : ""}
        ${attempt.status === "failed" && attempt.retryable ? `<button class="button compact-button" type="button" data-publish-action="retry">${t("common.retry")}</button>` : ""}
        ${attempt.status === "processing" ? `<button class="button compact-button" type="button" data-publish-action="sync">${t("publish.sync")}</button>` : ""}
        ${attempt.action === "open_manual_package" || attempt.status === "failed" ? `<a class="button compact-button" href="${manualUrl}">publish_package.json</a>` : ""}
      </div>
    </article>`;
}

function packageJobName(item) {
  return item.job_name || basename(item.job?.job_dir) || "";
}

function publishStatusGroup(status) {
  if (status === "published") return "done";
  if (status === "failed") return "failed";
  if (["uploading", "processing"].includes(status)) return "processing";
  return "review";
}
