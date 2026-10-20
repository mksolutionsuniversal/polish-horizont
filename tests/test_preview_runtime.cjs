"use strict";
// Dependency-free Node smoke test: run the actual preview JS against a tiny DOM
// fixture. This is not a browser/visual test and never makes network requests.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const root = path.resolve(__dirname, "..", "preview");
const html = fs.readFileSync(path.join(root, "index.html"), "utf8");
const fixture = html.match(/<script id="fixture-data" type="application\/json">([\s\S]*?)<\/script>/);
assert.ok(fixture, "embedded synthetic fixtures are required");

class Node {
  constructor() {
    this.children = [];
    this.attrs = {};
    this.dataset = {};
    this.listeners = {};
    this.textContent = "";
    this.value = "";
    this.hidden = false;
  }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  setAttribute(key, value) { this.attrs[key] = String(value); }
  addEventListener(type, callback) { this.listeners[type] = callback; }
  fire(type) { assert.ok(this.listeners[type], type); this.listeners[type]({ preventDefault() {} }); }
}

const ids = new Map();
for (const id of [
  "fixture-data", "timeline", "empty-state", "time-field", "filter-start",
  "filter-end", "filter-message", "event-count", "source-count", "source-list",
  "snapshot-time", "filters",
]) ids.set(id, new Node());
ids.get("fixture-data").textContent = fixture[1];
ids.get("time-field").value = "occurred_at";

const languages = ["pl", "en"].map((language) => {
  const button = new Node();
  button.dataset.language = language;
  return button;
});
const translatable = new Node();
translatable.dataset.i18n = "timelineTitle";
const doc = {
  documentElement: { lang: "pl" },
  title: "",
  getElementById(id) { assert.ok(ids.has(id), id); return ids.get(id); },
  querySelectorAll(selector) {
    if (selector === "[data-i18n]") return [translatable];
    if (selector === "[data-language]") return languages;
    throw new Error("unexpected selector " + selector);
  },
  createElement() { return new Node(); },
};

vm.runInNewContext(fs.readFileSync(path.join(root, "app.js"), "utf8"), {
  document: doc, Date, Intl, setTimeout(callback) { callback(); },
}, { filename: "preview/app.js" });

const timeline = ids.get("timeline");
const count = ids.get("event-count");
assert.equal(timeline.children.length, 4);
assert.equal(ids.get("source-list").children.length, 6);
assert.match(count.textContent, /^4 /);
const originalSnapshot = ids.get("snapshot-time").textContent;
languages[1].fire("click");
assert.equal(doc.documentElement.lang, "en");
assert.match(doc.title, /synthetic preview/);
assert.equal(translatable.innerHTML, "Event timeline");
assert.notEqual(ids.get("snapshot-time").textContent, originalSnapshot);
assert.equal(languages[1].attrs["aria-pressed"], "true");
const eventTitle = timeline.children[0].children[1].children.find((n) => n.className === "event-title");
assert.match(eventTitle.textContent, /^Exercise:/);

const filters = ids.get("filters");
ids.get("filter-start").value = "2026-10-21T00:00";
ids.get("filter-end").value = "2026-10-20T00:00";
filters.fire("input");
assert.equal(timeline.children.length, 0);
assert.match(ids.get("filter-message").textContent, /start date/i);
assert.equal(ids.get("filter-start").attrs["aria-invalid"], "true");
assert.equal(ids.get("filter-end").attrs["aria-invalid"], "true");

ids.get("filter-start").value = "2026-10-19T00:00";
ids.get("filter-end").value = "2026-10-21T00:00";
filters.fire("input");
assert.equal(ids.get("filter-message").textContent, "");
assert.equal(ids.get("filter-start").attrs["aria-invalid"], "false");
assert.ok(timeline.children.length > 0, "corrected range must restore event cards");

console.log("Preview JS smoke PASS");
