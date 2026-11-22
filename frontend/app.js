"use strict";

const element = (id) => document.getElementById(id);
const apiKey = element("api-key");

function status(node, message, error = false) {
  node.textContent = message;
  node.dataset.error = String(error);
}

function getApiKey() {
  const key = apiKey.value.trim();
  if (!key) {
    apiKey.focus();
    throw new Error("Enter the album API key.");
  }
  if (!window.albumConfig) throw new Error("The album API has not been configured.");
  return key;
}

function photoCard(photo) {
  const item = document.createElement("li");
  const image = document.createElement("img");
  image.src = photo.url;
  image.alt = photo.labels.length ? photo.labels.join(", ") : "Album photo";
  image.loading = "lazy";
  const caption = document.createElement("p");
  caption.textContent = photo.labels.length ? photo.labels.join(", ") : "No labels";
  item.append(image, caption);
  return item;
}

function requestError(error) {
  if (error.response?.status === 403) return "The API key was rejected.";
  return error.response?.data?.message || error.message || "The request failed.";
}

element("search-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = event.currentTarget.querySelector("button");
  const message = element("search-status");
  const results = element("results");
  results.replaceChildren();
  button.disabled = true;
  try {
    const key = getApiKey();
    if (!window.apigClientFactory) throw new Error("The generated API SDK could not load.");
    const sdk = window.apigClientFactory.newClient({ apiKey: key });
    status(message, "Searching…");
    const response = await sdk.searchGet({ q: element("query").value.trim() }, {}, {});
    const photos = response.data.results;
    results.replaceChildren(...photos.map(photoCard));
    status(message, photos.length
      ? `${photos.length} ${photos.length === 1 ? "photo" : "photos"} found.`
      : "No photos match this search.");
  } catch (error) {
    status(message, requestError(error), true);
  } finally {
    button.disabled = false;
  }
});

function uploadKey(filename, type) {
  const bytes = window.crypto.getRandomValues(new Uint8Array(12));
  const prefix = Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
  const stem = filename.replace(/\.[^.]+$/, "").replace(/[^A-Za-z0-9_-]+/g, "-").slice(0, 80);
  return `${prefix}-${stem || "photo"}.${type === "image/png" ? "png" : "jpg"}`;
}

element("upload-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const button = form.querySelector("button");
  const message = element("upload-status");
  button.disabled = true;
  try {
    const key = getApiKey();
    const file = element("file").files[0];
    if (!file || !["image/jpeg", "image/png"].includes(file.type) || file.size === 0) {
      throw new Error("Choose a JPEG or PNG image.");
    }
    if (file.size > 10 * 1024 * 1024) throw new Error("The image must be 10 MB or smaller.");
    const labels = element("labels").value.split(",").map((label) => label.trim()).filter(Boolean);
    if (labels.some((label) => /[^\x20-\x7E]/.test(label))) {
      throw new Error("Use printable ASCII characters for labels uploaded from the browser.");
    }
    const headers = { "Content-Type": file.type, "x-api-key": key };
    if (labels.length) headers["x-amz-meta-customLabels"] = labels.join(", ");
    const url = new URL(window.albumConfig.apiUrl + "/photos");
    url.searchParams.set("key", uploadKey(file.name, file.type));
    status(message, "Uploading…");
    const response = await fetch(url, { method: "PUT", headers, body: file });
    if (response.status === 403) throw new Error("The API key was rejected.");
    if (!response.ok) throw new Error(`Upload failed (HTTP ${response.status}).`);
    form.reset();
    status(message, "Uploaded. Allow a moment for indexing, then search for the photo.");
  } catch (error) {
    status(message, requestError(error), true);
  } finally {
    button.disabled = false;
  }
});
