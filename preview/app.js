"use strict";

const fixtureElement = document.getElementById("fixture-data");
const fixtures = JSON.parse(fixtureElement.textContent);
const copy = {
  pl: {
    previewLabel: "PODGLĄD · DANE SYNTETYCZNE", eyebrow: "SYTUACYJNY OBRAZ · TRYB DEMO",
    headline: "Świat w kontekście.<br><em>Źródła na widoku.</em>", heroCopy: "Chronologia i kondycja źródeł, z wyraźnym rozdzieleniem faktów od brakujących danych.",
    snapshotLabel: "PRZYKŁADOWY ZRZUT", utcNote: "Wszystkie czasy w UTC",
    notice: "To statyczny podgląd z fikcyjnymi danymi. Nie łączy się z bazą SQLite, źródłami ani siecią. Kraje przy źródłach opisują pochodzenie, nie potwierdzoną lokalizację wydarzenia.",
    feedEyebrow: "MONITORING", timelineTitle: "Oś wydarzeń", timeField: "Filtruj według czasu", occurred: "Czas zdarzenia", published: "Czas publikacji", collected: "Czas pobrania", fromUtc: "Od (UTC, włącznie)", beforeUtc: "Przed (UTC, bez końca)", reset: "Wyczyść", rangeHint: "Zakres [od, przed); wybrany czas pozostaje nieznany, jeśli źródło go nie podało.", emptyTitle: "Brak wpisów w tym zakresie", emptyCopy: "Zmień filtr czasu lub wyczyść zakres dat.",
    sourceEyebrow: "TRANSPARENTNOŚĆ", sourcesTitle: "Kondycja źródeł", sourceIntro: "Stan ostatniej próby jest oddzielony od liczby wydarzeń.", countryLegend: "Kraj przy źródle = kraj zaobserwowany w metadanych źródła. Nie potwierdza miejsca zdarzenia.", footer: "PRYWATNOŚĆ PRZEDE WSZYSTKIM · PODGLĄD LOKALNY",
    occurredLabel: "ZDARZENIE", publishedLabel: "PUBLIKACJA", collectedLabel: "POBRANO", unknown: "Nieznany", geo: "Precyzja geolokalizacji", noGeo: "Brak danych lokalizacyjnych", validation: "Walidacja", sourceObserved: "Kraj zaobserwowany przez źródło", language: "Język źródła", openSource: "Otwórz źródło (przykład)",
    statuses: {ok:"Dostępne",empty:"Pusto",stale:"Nieaktualne",failed:"Błąd pobrania",unavailable:"Niedostępne",never_run:"Nieuruchomione"}, statusDetails: {ok:"Dostępna próbka syntetyczna.",empty:"Ostatnie sprawdzenie zakończyło się poprawnie; brak wpisów.",stale:"Widoczna próbka jest oznaczona jako nieaktualna.",failed:"Pobieranie nie powiodło się; liczba wydarzeń nie jest znana.",unavailable:"Źródło jest niedostępne; nie oznacza to zera wydarzeń.",never_run:"Nie wykonano jeszcze żadnej próby."},
    sourceCount: "źródeł", eventCount: "wpisów", validationValues: {pending:"Oczekuje"}, geoValues: {region:"Region",country:"Kraj",unknown:"Nieznana"}, dateRangeError: "Data początkowa musi poprzedzać końcową.",
  },
  en: {
    previewLabel: "PREVIEW · SYNTHETIC DATA", eyebrow: "SITUATIONAL PICTURE · DEMO MODE",
    headline: "The world in context.<br><em>Sources in view.</em>", heroCopy: "Chronology and source health, with a clear separation between facts and missing data.",
    snapshotLabel: "SAMPLE SNAPSHOT", utcNote: "All times in UTC",
    notice: "This is a static preview with fictional data. It does not connect to SQLite, sources, or the network. Countries shown beside sources describe provenance, not a verified event location.",
    feedEyebrow: "MONITORING", timelineTitle: "Event timeline", timeField: "Filter by timestamp", occurred: "Event time", published: "Publication time", collected: "Collection time", fromUtc: "From (UTC, inclusive)", beforeUtc: "Before (UTC, exclusive)", reset: "Clear", rangeHint: "Range [from, before); the selected time stays unknown when the source did not provide it.", emptyTitle: "No entries in this range", emptyCopy: "Change the time filter or clear the date range.",
    sourceEyebrow: "TRANSPARENCY", sourcesTitle: "Source health", sourceIntro: "The latest attempt status is separate from the event count.", countryLegend: "Country beside a source = country observed in source metadata. It does not confirm where an event happened.", footer: "PRIVACY FIRST · LOCAL PREVIEW",
    occurredLabel: "EVENT", publishedLabel: "PUBLISHED", collectedLabel: "COLLECTED", unknown: "Unknown", geo: "Geolocation precision", noGeo: "No location data", validation: "Validation", sourceObserved: "Country observed by source", language: "Source language", openSource: "Open source (example)",
    statuses: {ok:"Available",empty:"Empty",stale:"Stale",failed:"Fetch failed",unavailable:"Unavailable",never_run:"Never run"}, statusDetails: {ok:"Synthetic sample available.",empty:"Last check succeeded; no items were returned.",stale:"The visible sample is marked stale.",failed:"Fetch failed; the event count is unknown.",unavailable:"Source is unavailable; this does not mean zero events.",never_run:"No attempt has been made yet."},
    sourceCount: "sources", eventCount: "entries", validationValues: {pending:"Pending"}, geoValues: {region:"Region",country:"Country",unknown:"Unknown"}, dateRangeError: "The start date must be earlier than the end date.",
  },
};

let language = "pl";
const timeline = document.getElementById("timeline");
const emptyState = document.getElementById("empty-state");
const timeField = document.getElementById("time-field");
const startInput = document.getElementById("filter-start");
const endInput = document.getElementById("filter-end");
const filterMessage = document.getElementById("filter-message");

function text(key) { return copy[language][key]; }
function localText(value) { return value?.[language] ?? ""; }
function displayDate(value) {
  if (!value) return text("unknown");
  return new Intl.DateTimeFormat(language === "pl" ? "pl-PL" : "en-GB", {
    dateStyle: "medium", timeStyle: "short", timeZone: "UTC",
  }).format(new Date(value)) + " UTC";
}
function element(name, className, value) {
  const node = document.createElement(name);
  if (className) node.className = className;
  if (value !== undefined) node.textContent = value;
  return node;
}
function timestampRow(label, value) {
  const row = element("div", "timestamp-row");
  row.append(element("span", "timestamp-label", label));
  row.append(element("time", value ? "timestamp-value" : "timestamp-value is-unknown", displayDate(value)));
  return row;
}
function eventCard(event) {
  const card = element("article", "event-card");
  const marker = element("span", "timeline-marker");
  marker.setAttribute("aria-hidden", "true");
  const main = element("div", "event-main");
  const head = element("div", "event-head");
  head.append(element("span", "event-tag", "SYNTHETIC · " + event.id.toUpperCase()));
  head.append(element("span", "validation-pill", `${text("validation")}: ${copy[language].validationValues[event.validation_status] ?? event.validation_status}`));
  main.append(head);
  main.append(element("h3", "event-title", localText(event.title)));
  main.append(element("p", "event-summary", localText(event.summary)));
  const dates = element("div", "timestamp-grid");
  dates.append(timestampRow(text("occurredLabel"), event.occurred_at));
  dates.append(timestampRow(text("publishedLabel"), event.published_at));
  dates.append(timestampRow(text("collectedLabel"), event.collected_at));
  main.append(dates);
  const details = element("div", "event-details");
  details.append(element("span", "detail-chip", `${text("geo")}: ${event.geo_precision ? copy[language].geoValues[event.geo_precision] ?? event.geo_precision : text("noGeo")}`));
  for (const source of event.sources) {
    const provenance = element("div", "provenance-row");
    const sourceName = element("span", "source-name", source.name);
    const observed = element("span", "observed-country", `${text("sourceObserved")}: ${source.country ?? text("unknown")}`);
    const sourceLink = element("a", "source-link", text("openSource"));
    sourceLink.href = source.url;
    sourceLink.target = "_blank";
    sourceLink.rel = "noopener noreferrer";
    provenance.append(sourceName, observed, element("span", "language-chip", `${text("language")}: ${source.language ?? text("unknown")}`), sourceLink);
    details.append(provenance);
  }
  main.append(details);
  card.append(marker, main);
  return card;
}
function filteredEvents() {
  const selected = timeField.value;
  const start = startInput.value ? Date.parse(`${startInput.value}Z`) : null;
  const end = endInput.value ? Date.parse(`${endInput.value}Z`) : null;
  if (start !== null && end !== null && start >= end) {
    filterMessage.textContent = text("dateRangeError");
    return null;
  }
  filterMessage.textContent = "";
  return fixtures.events.filter((event) => {
    const value = event[selected];
    if (value === null) return start === null && end === null;
    const timestamp = Date.parse(value);
    return (start === null || timestamp >= start) && (end === null || timestamp < end);
  }).sort((left, right) => {
    const a = left[selected] === null ? Number.NEGATIVE_INFINITY : Date.parse(left[selected]);
    const b = right[selected] === null ? Number.NEGATIVE_INFINITY : Date.parse(right[selected]);
    return b - a || right.id.localeCompare(left.id);
  });
}
function renderEvents() {
  const events = filteredEvents();
  timeline.replaceChildren();
  if (events === null) {
    emptyState.hidden = true;
    document.getElementById("event-count").textContent = "—";
    return;
  }
  events.forEach((event) => timeline.append(eventCard(event)));
  emptyState.hidden = events.length !== 0;
  document.getElementById("event-count").textContent = `${events.length} ${text("eventCount")}`;
}
function renderSources() {
  const list = document.getElementById("source-list");
  list.replaceChildren();
  fixtures.sources.forEach((source) => {
    const item = element("article", `source-item status-${source.status}`);
    const top = element("div", "source-item-head");
    const left = element("div", "source-title-wrap");
    const dot = element("span", "status-dot");
    dot.setAttribute("aria-hidden", "true");
    left.append(dot, element("strong", "source-title", source.name));
    top.append(left, element("span", "status-pill", copy[language].statuses[source.status]));
    item.append(top, element("p", "source-detail", localText(source.detail)));
    list.append(item);
  });
  document.getElementById("source-count").textContent = `${fixtures.sources.length} ${text("sourceCount")}`;
}
function renderLanguage() {
  document.documentElement.lang = language;
  document.title = language === "pl" ? "POLISH HORIZONT — podgląd syntetyczny" : "POLISH HORIZONT — synthetic preview";
  document.querySelectorAll("[data-i18n]").forEach((node) => {
    const value = copy[language][node.dataset.i18n];
    if (value !== undefined) node.innerHTML = value;
  });
  document.querySelectorAll("[data-language]").forEach((button) => {
    button.setAttribute("aria-pressed", String(button.dataset.language === language));
  });
  renderEvents();
  renderSources();
}

document.querySelectorAll("[data-language]").forEach((button) => {
  button.addEventListener("click", () => { language = button.dataset.language; renderLanguage(); });
});
document.getElementById("filters").addEventListener("input", renderEvents);
document.getElementById("filters").addEventListener("change", renderEvents);
document.getElementById("filters").addEventListener("reset", () => setTimeout(renderEvents, 0));
document.getElementById("snapshot-time").textContent = displayDate(fixtures.as_of);
document.getElementById("filters").addEventListener("submit", (event) => event.preventDefault());
renderLanguage();
