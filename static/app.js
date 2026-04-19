function createBlankRule() {
  return {
    name: "",
    xpath: "",
    extract: "text",
    attribute: "",
    include_in_result: true,
    is_link_follow: false,
    regex_enabled: false,
    regex_pattern: "",
    children: [],
  };
}

function normalizeRule(rule = {}) {
  return {
    name: rule.name || "",
    xpath: rule.xpath || "",
    extract: rule.extract || "text",
    attribute: rule.attribute || "",
    include_in_result: rule.include_in_result !== false,
    is_link_follow: rule.is_link_follow === true,
    regex_enabled: rule.regex_enabled === true,
    regex_pattern: rule.regex_pattern || "",
    children: Array.isArray(rule.children) ? rule.children.map(normalizeRule) : [],
  };
}

function cloneTemplate(id) {
  return document.getElementById(id).content.cloneNode(true);
}

function buildRuleFragment(rule = {}) {
  const normalized = normalizeRule(rule);
  const rowFragment = cloneTemplate("rule-row-template");
  const childFragment = cloneTemplate("child-config-template");

  const row = rowFragment.querySelector(".rule-row");
  const childRow = childFragment.querySelector(".child-config-row");
  const childBody = childFragment.querySelector(".child-rules-body");

  row.querySelector(".rule-name-input").value = normalized.name;
  row.querySelector(".rule-xpath-input").value = normalized.xpath;
  row.querySelector(".extract-select").value = normalized.extract;
  row.querySelector(".attr-input").value = normalized.attribute;
  row.querySelector(".include-checkbox").checked = normalized.include_in_result;
  row.querySelector(".follow-checkbox").checked = normalized.is_link_follow;
  row.querySelector(".regex-checkbox").checked = normalized.regex_enabled;
  row.querySelector(".regex-pattern-input").value = normalized.regex_pattern;

  normalized.children.forEach((childRule) => {
    const childNodes = buildRuleFragment(childRule);
    childBody.append(...childNodes);
  });

  syncRuleRow(row, childRow);
  return [row, childRow];
}

function syncRuleRow(row, childRow) {
  const select = row.querySelector(".extract-select");
  const attrInput = row.querySelector(".attr-input");
  const followCheckbox = row.querySelector(".follow-checkbox");
  const regexCheckbox = row.querySelector(".regex-checkbox");
  const regexInput = row.querySelector(".regex-pattern-input");
  const useAttr = select.value === "attr" || followCheckbox.checked;

  attrInput.disabled = !useAttr;
  if (!useAttr) {
    attrInput.value = "";
  }

  regexInput.hidden = !regexCheckbox.checked;
  regexInput.disabled = !regexCheckbox.checked;
  childRow.hidden = !followCheckbox.checked;
}

function appendRule(tbody, rule = {}) {
  const nodes = buildRuleFragment(rule);
  tbody.append(...nodes);
}

function clearRules(tbody) {
  tbody.innerHTML = "";
}

function serializeRules(tbody) {
  const rules = [];
  const rows = Array.from(tbody.children).filter((element) => element.classList.contains("rule-row"));

  rows.forEach((row) => {
    const childRow = row.nextElementSibling;
    const childBody = childRow?.querySelector(".child-rules-body");

    rules.push({
      name: row.querySelector(".rule-name-input").value.trim(),
      xpath: row.querySelector(".rule-xpath-input").value.trim(),
      extract: row.querySelector(".extract-select").value,
      attribute: row.querySelector(".attr-input").value.trim(),
      include_in_result: row.querySelector(".include-checkbox").checked,
      is_link_follow: row.querySelector(".follow-checkbox").checked,
      regex_enabled: row.querySelector(".regex-checkbox").checked,
      regex_pattern: row.querySelector(".regex-pattern-input").value.trim(),
      children: childBody ? serializeRules(childBody) : [],
    });
  });

  return rules;
}

function serializeConfig() {
  return {
    target_url: document.querySelector('[name="target_url"]').value.trim(),
    row_xpath: document.querySelector('[name="row_xpath"]').value.trim(),
    timeout: document.querySelector('[name="timeout"]').value,
    max_rows: document.querySelector('[name="max_rows"]').value,
    user_agent: document.querySelector('[name="user_agent"]').value,
    rules: serializeRules(document.getElementById("rules-body")),
  };
}

function syncRulesJson() {
  const config = serializeConfig();
  document.getElementById("rules-json-input").value = JSON.stringify(config.rules);
  return config;
}

function saveConfig() {
  syncRulesJson();
}

function showToast(message, level = "info") {
  const root = document.getElementById("toast-root");
  if (!root || !message) {
    return;
  }

  const toast = document.createElement("div");
  toast.className = `toast toast-${level}`;
  toast.innerHTML = `
    <span class="toast-dot" aria-hidden="true"></span>
    <span class="toast-message"></span>
    <button type="button" class="toast-close" aria-label="Close">&times;</button>
  `;
  toast.querySelector(".toast-message").textContent = message;
  toast.querySelector(".toast-close").addEventListener("click", () => {
    toast.classList.add("is-leaving");
    window.setTimeout(() => toast.remove(), 180);
  });

  root.append(toast);
  requestAnimationFrame(() => toast.classList.add("is-visible"));
  window.setTimeout(() => {
    toast.classList.add("is-leaving");
    window.setTimeout(() => toast.remove(), 180);
  }, 3200);
}

function ensureResultLoadingOverlay(resultPanel) {
  let overlay = resultPanel.querySelector(".result-loading-overlay");
  if (overlay) {
    return overlay;
  }

  overlay = document.createElement("div");
  overlay.className = "result-loading-overlay";
  overlay.setAttribute("aria-hidden", "true");
  overlay.innerHTML = `
    <div class="loading-state" role="status" aria-live="polite">
      <span class="loading-spinner" aria-hidden="true"></span>
      <p>正在获取数据，请稍候...</p>
    </div>
  `;
  resultPanel.append(overlay);
  return overlay;
}

function setPreviewLoadingState(form) {
  const previewButton = document.getElementById("preview-button");
  const resultPanel = document.getElementById("result-panel");
  if (!previewButton || !resultPanel) {
    return;
  }

  previewButton.disabled = true;
  previewButton.classList.add("is-loading");
  previewButton.querySelector(".button-spinner").hidden = false;
  previewButton.querySelector(".button-label").textContent = "执行中...";

  ensureResultLoadingOverlay(resultPanel);
  resultPanel.classList.add("result-panel-loading");
  document.body.classList.add("app-loading");
  form.classList.add("form-submitting");
}

function applyConfig(config = {}) {
  document.querySelector('[name="target_url"]').value = config.target_url || "";
  document.querySelector('[name="row_xpath"]').value = config.row_xpath || "";
  document.querySelector('[name="timeout"]').value = config.timeout || "";
  document.querySelector('[name="max_rows"]').value = config.max_rows || "";
  document.querySelector('[name="user_agent"]').value = config.user_agent || "";

  const body = document.getElementById("rules-body");
  clearRules(body);

  const rules = Array.isArray(config.rules) && config.rules.length ? config.rules : [createBlankRule()];
  rules.forEach((rule) => appendRule(body, rule));
  syncRulesJson();
}

document.addEventListener("DOMContentLoaded", () => {
  const form = document.getElementById("crawler-form");
  const rootBody = document.getElementById("rules-body");
  const initialConfig = window.initialFormState || {};
  const previewButton = document.getElementById("preview-button");
  const resultPanel = document.getElementById("result-panel");

  applyConfig(initialConfig);
  document.body.classList.remove("app-loading");
  form.classList.remove("form-submitting");
  previewButton?.classList.remove("is-loading");
  resultPanel?.classList.remove("result-panel-loading");
  previewButton?.querySelector(".button-spinner")?.setAttribute("hidden", "");
  if (previewButton?.querySelector(".button-label")) {
    previewButton.querySelector(".button-label").textContent = "预览数据";
  }

  form.addEventListener("click", (event) => {
    const addButton = event.target.closest(".add-rule-button");
    if (addButton) {
      const targetBody =
        addButton.dataset.target === "root"
          ? rootBody
          : addButton.closest(".child-config-card").querySelector(".child-rules-body");
      appendRule(targetBody, createBlankRule());
      saveConfig();
      return;
    }

    const removeButton = event.target.closest(".remove-rule");
    if (!removeButton) {
      return;
    }

    const row = removeButton.closest(".rule-row");
    const childRow = row.nextElementSibling;
    const parentBody = row.parentElement;

    row.remove();
    if (childRow?.classList.contains("child-config-row")) {
      childRow.remove();
    }

    if (parentBody === rootBody && !parentBody.querySelector(".rule-row")) {
      appendRule(parentBody, createBlankRule());
    }

    saveConfig();
  });

  previewButton?.addEventListener("click", (event) => {
    event.preventDefault();
    if (previewButton.disabled) {
      return;
    }

    syncRulesJson();
    setPreviewLoadingState(form);

    requestAnimationFrame(() => {
      window.setTimeout(() => {
        form.requestSubmit(previewButton);
      }, 0);
    });
  });

  form.addEventListener("change", (event) => {
    const row = event.target.closest(".rule-row");
    if (
      row &&
      (
        event.target.classList.contains("extract-select") ||
        event.target.classList.contains("include-checkbox") ||
        event.target.classList.contains("follow-checkbox") ||
        event.target.classList.contains("regex-checkbox")
      )
    ) {
      syncRuleRow(row, row.nextElementSibling);
    }
    saveConfig();
  });

  form.addEventListener("input", () => {
    saveConfig();
  });

  form.addEventListener("submit", () => {
    syncRulesJson();
  });
});
