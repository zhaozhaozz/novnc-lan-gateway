const form = document.querySelector("#target-form");
const formTitle = document.querySelector("#form-title");
const targetId = document.querySelector("#target-id");
const targetName = document.querySelector("#target-name");
const targetHost = document.querySelector("#target-host");
const targetPort = document.querySelector("#target-port");
const targetPassword = document.querySelector("#target-password");
const clearPassword = document.querySelector("#clear-password");
const clearPasswordRow = document.querySelector("#clear-password-row");
const targetDescription = document.querySelector("#target-description");
const formMessage = document.querySelector("#form-message");
const targetsContainer = document.querySelector("#targets");
const refreshButton = document.querySelector("#refresh-targets");
const cancelEditButton = document.querySelector("#cancel-edit");

let targets = [];

async function requestJson(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });

  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`;
    try {
      const payload = await response.json();
      message = payload.detail || message;
    } catch {
      // Keep the HTTP status message when the response is not JSON.
    }
    throw new Error(message);
  }

  if (response.status === 204) {
    return null;
  }

  return response.json();
}

function setMessage(message, isError = false) {
  formMessage.textContent = message;
  formMessage.className = `message${isError ? " error" : ""}`;
}

function resetForm() {
  targetId.value = "";
  form.reset();
  targetPort.value = "5900";
  clearPassword.checked = false;
  clearPasswordRow.classList.add("hidden");
  targetPassword.placeholder = "";
  formTitle.textContent = "Add target";
  cancelEditButton.classList.add("hidden");
  setMessage("");
}

function targetPayload() {
  const payload = {
    name: targetName.value,
    host: targetHost.value,
    port: Number(targetPort.value),
    description: targetDescription.value,
  };

  if (!targetId.value || targetPassword.value) {
    payload.password = targetPassword.value;
  }

  if (targetId.value && clearPassword.checked) {
    payload.password = "";
  }

  return payload;
}

function editTarget(target) {
  targetId.value = target.id;
  targetName.value = target.name;
  targetHost.value = target.host;
  targetPort.value = String(target.port);
  targetPassword.value = "";
  targetPassword.placeholder = target.has_password ? "Leave blank to keep saved password" : "";
  clearPassword.checked = false;
  clearPasswordRow.classList.toggle("hidden", !target.has_password);
  targetDescription.value = target.description || "";
  formTitle.textContent = "Edit target";
  cancelEditButton.classList.remove("hidden");
  targetName.focus();
}

function openViewer(target) {
  window.open(`/viewer/${encodeURIComponent(target.id)}`, "_blank", "noopener");
}

async function deleteTarget(target) {
  const confirmed = window.confirm(`Delete ${target.name}?`);
  if (!confirmed) {
    return;
  }
  await requestJson(`/api/targets/${encodeURIComponent(target.id)}`, { method: "DELETE" });
  await loadTargets();
}

async function probeTarget(target, statusNode) {
  statusNode.textContent = "Checking connection...";
  statusNode.className = "target-status";
  try {
    const result = await requestJson(`/api/targets/${encodeURIComponent(target.id)}/probe`);
    statusNode.textContent = result.message;
    statusNode.className = `target-status ${result.ok ? "ok" : "error"}`;
  } catch (error) {
    statusNode.textContent = error.message;
    statusNode.className = "target-status error";
  }
}

function renderTargets() {
  targetsContainer.textContent = "";

  if (targets.length === 0) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = "No targets have been added yet.";
    targetsContainer.append(empty);
    return;
  }

  for (const target of targets) {
    const item = document.createElement("article");
    item.className = "target";

    const content = document.createElement("div");
    const title = document.createElement("h3");
    title.textContent = target.name;

    const endpoint = document.createElement("p");
    endpoint.className = "endpoint";
    endpoint.textContent = `${target.host}:${target.port}`;

    const description = document.createElement("p");
    description.className = "description";
    description.textContent = [
      target.description || "No description",
      target.has_password ? "Password saved" : "No saved password",
    ].join(" · ");

    const status = document.createElement("p");
    status.className = "target-status";
    status.textContent = "";

    content.append(title, endpoint, description, status);

    const actions = document.createElement("div");
    actions.className = "actions";

    const openButton = document.createElement("button");
    openButton.className = "secondary";
    openButton.type = "button";
    openButton.textContent = "Open noVNC";
    openButton.addEventListener("click", () => openViewer(target));

    const probeButton = document.createElement("button");
    probeButton.className = "ghost";
    probeButton.type = "button";
    probeButton.textContent = "Probe";
    probeButton.addEventListener("click", () => probeTarget(target, status));

    const editButton = document.createElement("button");
    editButton.className = "ghost";
    editButton.type = "button";
    editButton.textContent = "Edit";
    editButton.addEventListener("click", () => editTarget(target));

    const deleteButton = document.createElement("button");
    deleteButton.className = "danger";
    deleteButton.type = "button";
    deleteButton.textContent = "Delete";
    deleteButton.addEventListener("click", () => deleteTarget(target));

    actions.append(openButton, probeButton, editButton, deleteButton);
    item.append(content, actions);
    targetsContainer.append(item);
  }
}

async function loadTargets() {
  targetsContainer.textContent = "";
  const loading = document.createElement("p");
  loading.className = "empty";
  loading.textContent = "Loading targets...";
  targetsContainer.append(loading);

  try {
    targets = await requestJson("/api/targets");
    renderTargets();
  } catch (error) {
    targetsContainer.textContent = "";
    const message = document.createElement("p");
    message.className = "empty";
    message.textContent = error.message;
    targetsContainer.append(message);
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const id = targetId.value;
  const method = id ? "PUT" : "POST";
  const path = id ? `/api/targets/${encodeURIComponent(id)}` : "/api/targets";

  try {
    await requestJson(path, {
      method,
      body: JSON.stringify(targetPayload()),
    });
    resetForm();
    await loadTargets();
    setMessage("Target saved.");
  } catch (error) {
    setMessage(error.message, true);
  }
});

refreshButton.addEventListener("click", loadTargets);
cancelEditButton.addEventListener("click", resetForm);

loadTargets();
