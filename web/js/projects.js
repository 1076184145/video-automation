import { API, isAbortError } from "./api.js";
import { t } from "./i18n.js";
import { setButtonLoading, showToast } from "./toast.js";
import { emptyState, errorState, loadingState } from "./ui-states.js";
import { escapeHtml } from "./utils.js";

export async function renderProjects(_match, { signal } = {}) {
  const app = document.getElementById("app");
  app.innerHTML = loadingState(t("common.loading"));
  let disposed = false;
  let loadVersion = 0;
  const isActive = () => !disposed && !signal?.aborted;

  async function refresh() {
    const version = ++loadVersion;
    try {
      const [projectResponse, kitResponse] = await Promise.all([
        API.getProjects({ signal }),
        API.getCreatorKits({ signal }),
      ]);
      if (!isActive() || version !== loadVersion) return;
      app.innerHTML = renderProjectsView({
        projects: projectResponse.items || [],
        kits: kitResponse.items || [],
      });
    } catch (error) {
      if (!isActive() || version !== loadVersion || isAbortError(error, signal)) return;
      app.innerHTML = errorState(error.message || t("common.error"), { retryLabel: t("common.retry") });
    }
  }

  const unbind = bindProjectActions(app, { refresh, isActive });
  await refresh();
  return () => {
    disposed = true;
    loadVersion += 1;
    unbind();
  };
}

export function renderProjectsView({ projects = [], kits = [] } = {}) {
  const kitById = new Map(kits.map((kit) => [kit.id, kit]));
  return `
    <section class="page-head library-page-head">
      <div>
        <h1 class="page-title">${t("projects.title")}</h1>
        <p class="page-subtitle">${t("projects.subtitle")}</p>
      </div>
      <a class="button primary" href="#/new">+ ${t("dashboard.new_job")}</a>
    </section>
    <section class="library-section">
      <div class="section-heading">
        <div>
          <h2>${t("projects.title")}</h2>
          <p>${t("projects.section_note")}</p>
        </div>
        <span class="library-count">${projects.length}</span>
      </div>
      <form class="library-create-form" id="create-project-form">
        <div class="field"><label for="project-name">${t("projects.name")}</label><input id="project-name" name="name" required maxlength="120" /></div>
        <div class="field"><label for="project-description">${t("projects.description")}</label><input id="project-description" name="description" maxlength="2000" /></div>
        <div class="field"><label for="project-tags">${t("projects.tags")}</label><input id="project-tags" name="tags" placeholder="直播, 周更" /></div>
        <div class="field"><label for="project-default-kit">${t("projects.default_kit")}</label><select id="project-default-kit" name="default_kit_id"><option value="">${t("projects.no_default_kit")}</option>${kits.map((kit) => `<option value="${escapeHtml(kit.id)}">${escapeHtml(kit.name)}</option>`).join("")}</select></div>
        <button class="button primary" type="submit">${t("projects.create")}</button>
      </form>
      <div class="library-list" id="project-list">
        ${projects.length ? projects.map((project) => renderProjectRow(project, kitById)).join("") : renderEmpty(t("projects.empty"), "#/new", t("dashboard.new_job"))}
      </div>
    </section>
    <section class="library-section">
      <div class="section-heading">
        <div>
          <h2>${t("kits.title")}</h2>
          <p>${t("kits.subtitle")}</p>
        </div>
        <span class="library-count">${kits.length}</span>
      </div>
      <form class="library-create-form compact" id="create-kit-form">
        <div class="field"><label for="kit-name">${t("projects.name")}</label><input id="kit-name" name="name" required maxlength="120" /></div>
        <div class="field"><label for="kit-platform">${t("kits.platform")}</label><select id="kit-platform" name="platform"><option value="douyin">Douyin</option><option value="bilibili">Bilibili</option><option value="youtube_shorts">YouTube Shorts</option></select></div>
        <div class="field"><label for="kit-aspect">${t("kits.aspect")}</label><select id="kit-aspect" name="aspect"><option value="9:16">9:16</option><option value="16:9">16:9</option><option value="1:1">1:1</option></select></div>
        <button class="button primary" type="submit">${t("kits.create")}</button>
      </form>
      <div class="library-list" id="kit-list">
        ${kits.length ? kits.map(renderKitRow).join("") : renderEmpty(t("kits.empty"))}
      </div>
    </section>`;
}

function renderProjectRow(project, kitById) {
  const kit = kitById.get(project.default_kit_id);
  return `
    <article class="library-row">
      <div class="library-row-main">
        <strong>${escapeHtml(project.name)}</strong>
        <p>${escapeHtml(project.description || t("projects.no_description"))}</p>
        <div class="library-row-meta">
          ${(project.tags || []).map((tag) => `<span>${escapeHtml(tag)}</span>`).join("")}
          ${kit ? `<span>${escapeHtml(kit.name)}</span>` : ""}
        </div>
      </div>
      <div class="library-row-actions">
        <a class="button compact-button" href="#/new?project=${encodeURIComponent(project.id)}">${t("projects.use")}</a>
        <button class="button compact-button danger" type="button" data-delete-project="${escapeHtml(project.id)}">${t("common.delete")}</button>
      </div>
    </article>`;
}

function renderKitRow(kit) {
  return `
    <article class="library-row">
      <div class="library-row-main">
        <strong>${escapeHtml(kit.name)}</strong>
        <p>${escapeHtml(kit.platform || "—")} · ${escapeHtml(kit.aspect || "—")}</p>
      </div>
      <div class="library-row-actions">
        <button class="button compact-button danger" type="button" data-delete-kit="${escapeHtml(kit.id)}">${t("common.delete")}</button>
      </div>
    </article>`;
}

function renderEmpty(message, href = "", action = "") {
  return emptyState({
    title: escapeHtml(message),
    className: "library-empty",
    actionHtml: href ? `<a class="button" href="${href}">${escapeHtml(action)}</a>` : ""
  });
}

function bindProjectActions(root, { refresh, isActive }) {
  const handleSubmit = async (event) => {
    const projectForm = event.target?.closest?.("#create-project-form");
    const kitForm = event.target?.closest?.("#create-kit-form");
    if (!projectForm && !kitForm) return;
    event.preventDefault();
    const form = projectForm || kitForm;
    const button = form.querySelector('[type="submit"]');
    const data = new FormData(form);
    setButtonLoading(button, true, t("common.loading"));
    try {
      if (projectForm) {
        await API.createProject({
          name: data.get("name"),
          description: data.get("description"),
          tags: String(data.get("tags") || "").split(/[,，]/).map((tag) => tag.trim()).filter(Boolean),
          default_kit_id: data.get("default_kit_id") || null,
        });
      } else {
        await API.createCreatorKit({ name: data.get("name"), platform: data.get("platform"), aspect: data.get("aspect") });
      }
      if (!isActive()) return;
      showToast(t(projectForm ? "projects.created" : "kits.created"), "success");
      await refresh();
    } catch (error) {
      if (!isActive()) return;
      showToast(error.message, "error");
      setButtonLoading(button, false);
    }
  };

  const handleClick = async (event) => {
    if (event.target?.closest?.("[data-retry]")) {
      await refresh();
      return;
    }
    const projectButton = event.target.closest("[data-delete-project]");
    const kitButton = event.target.closest("[data-delete-kit]");
    if (!projectButton && !kitButton) return;
    if (!window.confirm(t("projects.delete_confirm"))) return;
    const button = projectButton || kitButton;
    setButtonLoading(button, true, t("common.loading"));
    try {
      if (projectButton) await API.deleteProject(projectButton.dataset.deleteProject);
      else await API.deleteCreatorKit(kitButton.dataset.deleteKit);
      if (!isActive()) return;
      await refresh();
    } catch (error) {
      if (!isActive()) return;
      showToast(error.message, "error");
      setButtonLoading(button, false);
    }
  };

  root.addEventListener("submit", handleSubmit);
  root.addEventListener("click", handleClick);
  return () => {
    root.removeEventListener("submit", handleSubmit);
    root.removeEventListener("click", handleClick);
  };
}
