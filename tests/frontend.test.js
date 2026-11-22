"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const test = require("node:test");
const vm = require("node:vm");

function mockElement() {
  return {
    events: {}, children: [], dataset: {}, value: "",
    addEventListener(name, handler) { this.events[name] = handler; },
    replaceChildren(...children) { this.children = children; },
    append(...children) { this.children.push(...children); },
    querySelector() { return this.button ||= {}; },
    reset() { this.wasReset = true; },
    focus() { this.wasFocused = true; },
  };
}

function loadFrontend() {
  const fields = new Map();
  const get = (id) => {
    if (!fields.has(id)) fields.set(id, mockElement());
    return fields.get(id);
  };
  const requests = [];
  const sdkCalls = [];
  vm.runInNewContext(fs.readFileSync("frontend/app.js", "utf8"), {
    Uint8Array, URL,
    document: { getElementById: get, createElement: mockElement },
    fetch: async (url, options) => {
      requests.push({ url, ...options });
      return { ok: true };
    },
    window: {
      albumConfig: { apiUrl: "https://api.example/v1" },
      crypto: { getRandomValues: (bytes) => bytes.fill(13) },
      apigClientFactory: {
        newClient: ({ apiKey }) => ({
          async searchGet({ q }) {
            sdkCalls.push({ apiKey, q });
            return { data: { results: [{ url: "https://example.test/signed", labels: ["Dog", "Sam"] }] } };
          },
        }),
      },
    },
  });
  function submit(id) {
    const form = get(id);
    return form.events.submit({ preventDefault() {}, currentTarget: form });
  }
  return { get, requests, sdkCalls, submit };
}

test("search calls the generated SDK and renders matching images", async () => {
  const page = loadFrontend();
  page.get("query").value = "show me dogs";
  await page.submit("search-form");
  assert.match(page.get("search-status").textContent, /Enter the album API key/);
  page.get("api-key").value = "test-key";
  await page.submit("search-form");
  assert.deepEqual(page.sdkCalls, [{ apiKey: "test-key", q: "show me dogs" }]);
  assert.equal(page.get("results").children[0].children[0].src, "https://example.test/signed");
  assert.equal(page.get("results").children[0].children[1].textContent, "Dog, Sam");
  assert.equal(page.get("search-status").textContent, "1 photo found.");
});

test("upload forwards raw file and comma-separated metadata", async () => {
  const page = loadFrontend();
  const form = page.get("upload-form");
  const file = { name: "photo.png", size: 100, type: "image/png" };
  page.get("api-key").value = "test-key";
  page.get("file").files = [file];
  page.get("labels").value = "Sam,, Sally ";
  await page.submit("upload-form");
  const request = page.requests[0];
  assert.equal(request.body, file);
  assert.equal(request.method, "PUT");
  assert.equal(request.url.pathname, "/v1/photos");
  assert.match(request.url.searchParams.get("key"), /^[a-f0-9]{24}-photo\.png$/);
  assert.equal(request.headers["x-amz-meta-customLabels"], "Sam, Sally");
  assert.equal(request.headers["x-api-key"], "test-key");
  assert.equal(form.wasReset, true);
});
