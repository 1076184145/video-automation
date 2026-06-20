import { t } from "./i18n.js";

const TAB_NAMES = ["review", "enhance", "export", "advanced"];

export function defaultJobDetailTab(status) {
  return status === "done" ? "export" : "review";
}

export function jobDetailTabStorageKey(jobName) {
  return `videoAutomationJobDetailTab:${jobName}`;
}

export function renderJobDetailTabs() {
  return `
    <nav class="job-workspace-tabs" role="tablist" aria-label="${t("job.workspace")}">
      ${TAB_NAMES.map((name, index) => `
        <button
          class="job-workspace-tab${index === 0 ? " active" : ""}"
          id="job-workspace-tab-${name}"
          type="button"
          role="tab"
          aria-selected="${index === 0 ? "true" : "false"}"
          aria-controls="job-workspace-${name}"
          tabindex="${index === 0 ? "0" : "-1"}"
          data-job-workspace-tab="${name}"
        >${t(`job.tab_${name}`)}</button>
      `).join("")}
    </nav>
  `;
}

export function bindJobDetailTabs(root, { jobName, status } = {}) {
  const buttons = Array.from(root.querySelectorAll("[data-job-workspace-tab]"));
  const panels = Array.from(root.querySelectorAll("[data-job-workspace-panel]"));
  if (!buttons.length || !panels.length) return () => {};

  const storageKey = jobDetailTabStorageKey(jobName || "current");
  const stored = localStorage.getItem(storageKey);
  const initial = TAB_NAMES.includes(stored) ? stored : defaultJobDetailTab(status);

  const activate = (name, { focus = false } = {}) => {
    if (!TAB_NAMES.includes(name)) return;
    const current = buttons.find((button) => button.getAttribute("aria-selected") === "true")?.dataset.jobWorkspaceTab;
    if (current === "review" && name !== "review") {
      root.querySelector("#section-preview video")?.pause();
    }
    buttons.forEach((button) => {
      const selected = button.dataset.jobWorkspaceTab === name;
      button.classList.toggle("active", selected);
      button.setAttribute("aria-selected", selected ? "true" : "false");
      button.tabIndex = selected ? 0 : -1;
      if (selected && focus) button.focus();
    });
    panels.forEach((panel) => {
      panel.hidden = panel.dataset.jobWorkspacePanel !== name;
    });
    localStorage.setItem(storageKey, name);
  };

  const handleClick = (event) => {
    const button = event.target?.closest?.("[data-job-workspace-tab]");
    if (!button || !root.contains(button)) return;
    activate(button.dataset.jobWorkspaceTab);
  };

  const handleKeydown = (event) => {
    const button = event.target?.closest?.("[data-job-workspace-tab]");
    if (!button || !root.contains(button)) return;
    const currentIndex = buttons.indexOf(button);
    let nextIndex = currentIndex;
    if (event.key === "ArrowRight") nextIndex = (currentIndex + 1) % buttons.length;
    else if (event.key === "ArrowLeft") nextIndex = (currentIndex - 1 + buttons.length) % buttons.length;
    else if (event.key === "Home") nextIndex = 0;
    else if (event.key === "End") nextIndex = buttons.length - 1;
    else return;
    event.preventDefault();
    activate(buttons[nextIndex].dataset.jobWorkspaceTab, { focus: true });
  };

  root.addEventListener("click", handleClick);
  root.addEventListener("keydown", handleKeydown);
  activate(initial);

  return () => {
    root.removeEventListener("click", handleClick);
    root.removeEventListener("keydown", handleKeydown);
  };
}
