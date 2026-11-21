/*
  Hunch Home's page: the stage (the ribbons and what is happening now), first-run set-up, trying a sentence,
  what happened with a way to correct it, what the owner has taught, the home and its assistants, settings.

  Every address is relative, since Home Assistant serves the page under a path of its own. Everything shown is
  written with textContent: nothing from the home or a sentence is ever read as markup.
*/
import { Silk } from "./silk.js";

// The model's own words for a declined sentence. They are never shown: the page shows the outcome's label.
const DECLINED = "not for me";

// What Hunch can be taught: its actions and its kinds of thing, in the model's own words. The app's state says
// which the model in use knows (`teach`); these are the fallback.
const ACTIONS = ["turn on", "turn off", "set level", "raise", "lower", "set colour", "set temperature", "lock",
  "unlock", "open", "close", "next", "previous", "ask", "add", "remove", "announce"];
const KINDS = ["Lights", "Climate", "Security", "Speakers & TVs", "Water", "Scenes", "Timers and alarms", "Weather",
  "Lists", "Other device"];

// Everyday things of each kind, so a fan or a kettle can be placed when teaching: what the rules do with each
// (`logic/table.py`). Water is not covered yet: Hunch says so and does nothing.
const KIND_EXAMPLES = {
  "Lights": "lamps, ceiling lights, a lamp on a plug",
  "Climate": "heating, fans, blinds",
  "Security": "locks, doors, gates, garage doors",
  "Speakers & TVs": "speakers, TVs, music",
  "Water": "taps, sprinklers (not yet)",
  "Scenes": "scenes such as movie night",
  "Timers and alarms": "timers",
  "Weather": "the weather now",
  "Lists": "shopping and to-do lists",
  "Other device": "plugs, kettles, vacuums"
};

// How long an outcome holds the stage before it settles back.
const HOLD_MS = { done: 7000, passed: 6500, ignored: 2600, error: 9000, nothing: 7000 };
const HEARING_MAX_MS = 15000;
const DECIDING_MAX_MS = 4000;
const OFFLINE_AFTER_MS = 3000;
const FIX_NAMES_SHOWN = 5;   // a group of devices worth fixing names this many, then "and 2 more"
const RECORD_FIRST = 8;
const RECORD_STEP = 25;

// Home Assistant's kinds of device in the words people use, singular and plural. Its "cover" is anything that
// opens and closes; where the app says which kind (its device class), that is named instead.
const DEVICE_WORDS = {
  light: ["light", "lights"], switch: ["switch", "switches"], lock: ["lock", "locks"],
  cover: ["blind or door", "blinds and doors"], climate: ["thermostat", "thermostats"], fan: ["fan", "fans"],
  media_player: ["speaker or TV", "speakers and TVs"], scene: ["scene", "scenes"], script: ["script", "scripts"],
  vacuum: ["robot vacuum", "robot vacuums"], valve: ["valve", "valves"], humidifier: ["humidifier", "humidifiers"],
  water_heater: ["water heater", "water heaters"], todo: ["list", "lists"], input_boolean: ["toggle", "toggles"],
  button: ["button", "buttons"], input_button: ["button", "buttons"], lawn_mower: ["lawn mower", "lawn mowers"],
  weather: ["weather forecast", "weather forecasts"], timer: ["timer", "timers"], siren: ["siren", "sirens"],
  alarm_control_panel: ["alarm", "alarms"], camera: ["camera", "cameras"], remote: ["remote", "remotes"],
  sensor: ["sensor", "sensors"], binary_sensor: ["sensor", "sensors"], number: ["number", "numbers"],
  select: ["choice", "choices"], input_select: ["choice", "choices"], input_number: ["number", "numbers"],
  calendar: ["calendar", "calendars"]
};
const COVER_WORDS = {
  awning: ["awning", "awnings"], blind: ["blind", "blinds"], curtain: ["curtain", "curtains"],
  damper: ["damper", "dampers"], door: ["door", "doors"], garage: ["garage door", "garage doors"],
  gate: ["gate", "gates"], shade: ["shade", "shades"], shutter: ["shutter", "shutters"], window: ["window", "windows"]
};
const MEDIA_WORDS = { tv: ["TV", "TVs"], speaker: ["speaker", "speakers"], receiver: ["receiver", "receivers"] };
function deviceWords(k) {
  const cls = text(k.device_class);
  if (k.kind === "cover" && COVER_WORDS[cls]) return COVER_WORDS[cls];
  if (k.kind === "media_player" && MEDIA_WORDS[cls]) return MEDIA_WORDS[cls];
  if (DEVICE_WORDS[k.kind]) return DEVICE_WORDS[k.kind];
  const w = String(k.kind || "device").replace(/_/g, " ");
  return [w, w + "s"];
}

// What changed a turn's devices again within 30 seconds, in the record's own words (`logic/undo.py`), as the page
// says it: only what happened, since the owner may have meant it ("lamp on", then "lamp off" on purpose).
const LATER_SENTENCE = "a later Hunch command";
const UNDONE_BY = {
  "someone signed in to Home Assistant": "by someone in Home Assistant",
  "something else: a switch, a device, an automation or a voice command":
    "by something else, such as a switch, an automation or another voice command"
};

// "Changed again 12 seconds later by another sentence." The seconds come from the next sentence in the list that
// acted on the same device, where there is one.
function changedAgain(t) {
  if (text(t.undone_by) === LATER_SENTENCE) {
    const mine = new Set((t.entities || []).map(text).filter(Boolean));
    const at = new Date(t.time);
    const later = turns.filter((x) => x.id !== t.id && x.outcome === "done" && new Date(x.time) >= at &&
      (x.entities || []).some((n) => mine.has(text(n))))
      .sort((a, b) => new Date(a.time) - new Date(b.time))[0];
    const seconds = later ? Math.round((new Date(later.time) - at) / 1000) : NaN;
    return seconds >= 0 && seconds <= 30
      ? "Changed again " + seconds + (seconds === 1 ? " second" : " seconds") + " later by another sentence."
      : "Changed again within 30 seconds by another sentence.";
  }
  const by = UNDONE_BY[text(t.undone_by)];
  return "Changed again within 30 seconds" + (by ? " " + by : "") + ".";
}

// How a tried sentence reads: "Hunch would turn on the Lamp".
const WOULD = {
  "turn on": "turn on", "turn off": "turn off", "set level": "set the level of", raise: "turn up",
  lower: "turn down", "set colour": "change the colour of", "set temperature": "set the temperature of",
  lock: "lock", unlock: "unlock", open: "open", close: "close", next: "skip to the next track on",
  previous: "go back a track on", ask: "look up", add: "add to", remove: "remove from", announce: "announce on"
};

const params = new URLSearchParams(location.search);
const $ = (id) => document.getElementById(id);
const reducedQuery = window.matchMedia ? window.matchMedia("(prefers-reduced-motion: reduce)") : null;

let state = null;          // api/state
let turns = [];            // what happened, newest first
let recordLimit = RECORD_FIRST;
let recordFull = false;
let taught = [];

/* ---------- small helpers ---------- */

function text(x) { return typeof x === "string" ? x.trim() : ""; }
function el(tag, cls, content) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (content !== undefined && content !== null) n.textContent = content;
  return n;
}
function quoted(s) { s = text(s); return s ? "“" + s + "”" : ""; }
function plural(n, words) { return n + " " + (n === 1 ? words[0] : words[1]); }
function listJoin(items) {
  items = items.filter(Boolean);
  if (items.length <= 1) return items.join("");
  return items.slice(0, -1).join(", ") + " and " + items[items.length - 1];
}
function realAction(a) { a = text(a); return a && a.toLowerCase() !== DECLINED ? a : ""; }
function realKind(k) { k = text(k); return k && k.toLowerCase() !== "none" ? k : ""; }
function ms(x) { return typeof x === "number" && isFinite(x) && x >= 0 ? Math.round(x) + "\u00a0ms" : ""; }

const clockFmt = new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit" });
const dayFmt = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short" });
function when(iso) {
  const d = new Date(iso);
  if (isNaN(d)) return "";
  const now = new Date();
  const days = Math.round((new Date(now.toDateString()) - new Date(d.toDateString())) / 86400000);
  const clock = clockFmt.format(d);
  if (days === 0) return clock;
  if (days === 1) return "Yesterday " + clock;
  return dayFmt.format(d) + " " + clock;
}

function agentName(id) {
  if (!state) return "your other agent";
  const a = (state.agents || []).find((x) => x.id === id);
  return a ? a.name : "your other agent";
}
function passName() { return agentName(state && state.settings ? state.settings.pass_to : ""); }
// "This was not a home command" is offered only where the teaching in use can learn it.
function canDecline() { return !!(state && state.teach && state.teach.can_decline === true); }
function teachList(key, fallback) {
  const t = state && state.teach && Array.isArray(state.teach[key]) ? state.teach[key].map(text).filter(Boolean) : [];
  return t.length ? t : fallback;
}

async function api(path, opts = {}) {
  // Every request says it comes from this page; a change also says it is JSON. The server refuses a change
  // without both, which a plain form on another site cannot send.
  const init = { method: opts.method || "GET", headers: { "X-Hunch-Page": "1" }, cache: "no-store" };
  if (opts.body !== undefined) {
    init.method = opts.method || "POST";
    init.body = JSON.stringify(opts.body);
  }
  if (init.method !== "GET") init.headers["Content-Type"] = "application/json";
  let res;
  try {
    res = await fetch(path, init);
  } catch (e) {
    throw refusal(0, "Hunch did not answer. Check that the app is running, then try again.");
  }
  let data = null;
  try { data = await res.json(); } catch (e) { data = null; }
  if (!res.ok) {
    const said = data && typeof data.error === "string" && data.error.trim() ? data.error.trim() : "";
    throw refusal(res.status, said || FALLBACK[res.status] || FALLBACK.other);
  }
  return data;
}

// What the page says when the server gives no words of its own: never a status code or a raw error.
const FALLBACK = {
  501: "That part of Hunch is not built yet.",
  503: "Hunch cannot reach Home Assistant just now. It keeps trying. Try again in a minute.",
  502: "Hunch did not answer. Check that the app is running, then try again.",
  504: "Hunch did not answer in time. Try again.",
  other: "Hunch could not do that. Try again."
};
function refusal(status, message) {
  const e = new Error(message);
  e.status = status;
  // "not yet" and "cannot reach Home Assistant" are states of things, not faults: shown as calm notes
  e.calm = status === 501 || status === 503;
  return e;
}
function errorText(e) { return e && typeof e.message === "string" && e.message ? e.message : FALLBACK.other; }

function note(node, message, isError) {
  if (!node) return;
  node.textContent = message || "";
  node.classList.toggle("is-error", !!isError);
  clearTimeout(node._t);
  if (message && !isError) node._t = setTimeout(() => { node.textContent = ""; }, 2600);
}
// A refusal under the control that asked: kept until the next try, marked as a fault only when it is one.
function noteRefusal(node, e) {
  if (!node) return;
  note(node, errorText(e), !e.calm);
  clearTimeout(node._t);
}

/* ---------- what Hunch did, in plain words ---------- */

// "Closed Living room blinds": what Hunch did to which devices, from its action and the names of the devices
// that changed. The kind only picks the words (a timer is started, a list is added to); it is never shown, and
// neither is the action's own label. The model's two answers appear only inside "That was wrong".
const DID = {
  "turn on": "Turned on", "turn off": "Turned off", "set level": "Set", raise: "Turned up", lower: "Turned down",
  "set colour": "Changed the colour of", "set temperature": "Set", lock: "Locked", unlock: "Unlocked",
  open: "Opened", close: "Closed", next: "Skipped to the next track on", previous: "Went back a track on",
  ask: "Looked up", add: "Added to", remove: "Removed from", announce: "Made an announcement on"
};
// A timer has no name to show: what was done to it, said whole.
const TIMER_DID = {
  "turn on": "Started a timer", "turn off": "Cancelled the timer", remove: "Cancelled the timer",
  ask: "Checked the timer", raise: "Added time to the timer", lower: "Took time off the timer"
};
// A speaker's on and off do something else when the sentence says so, in the rules' order (`logic/table.py`).
const SPEAKER_DID = [
  ["turn on", ["unmute"], "Unmuted"], ["turn on", ["resume", "unpause", "continue"], "Resumed"],
  ["turn on", ["play"], "Started playing on"], ["turn off", ["mute", "silence"], "Muted"],
  ["turn off", ["pause", "stop"], "Paused"]
];
// Everyday things of each kind, for a best guess said in words: "close heating, fans or blinds".
const GUESS_THINGS = {
  "Lights": "lights", "Climate": "heating, fans or blinds", "Security": "locks or doors",
  "Speakers & TVs": "speakers or TVs", "Water": "taps or sprinklers", "Scenes": "a scene",
  "Timers and alarms": "a timer or alarm", "Weather": "the weather", "Lists": "a list",
  "Other device": "plugs, kettles or vacuums"
};

function didWhat(t) {
  if (!t || t.outcome !== "done") return "";
  const action = realAction(t.action), kind = realKind(t.kind);
  const names = listJoin(Array.isArray(t.entities) ? t.entities.map(text) : []);
  if (kind === "Timers and alarms" && TIMER_DID[action]) return TIMER_DID[action];
  if (action === "ask" && kind === "Weather") return "Looked up the weather";
  if (action === "announce") return "Made an announcement" + (names ? " on " + names : "");
  if (!names) return "";
  let verb = DID[action] || "Acted on";
  if (action === "set level" && kind === "Lights") verb = "Set the brightness of";
  if (action === "set level" && kind === "Speakers & TVs") verb = "Set the volume of";
  if (kind === "Speakers & TVs") {
    const words = text(t.sentence).toLowerCase().replace(/[^\p{L}\p{N}_]+/gu, " ").split(" ");
    const said = SPEAKER_DID.find(([a, w]) => a === action && w.some((x) => words.includes(x)));
    if (said) verb = said[2];
  }
  return verb + " " + names;
}

// A turn where Hunch did nothing, as a headline: the app's short line for it ("Couldn't work out which device you
// meant"), else its note without the leading "Sorry, ", else nothing (and the label leads).
function nothingHead(t) {
  const short = text(t.short);
  if (short) return short;
  const note = text(t.note).replace(/^sorry,\s*/i, "").replace(/\.$/, "");
  return note ? note.charAt(0).toUpperCase() + note.slice(1) : "";
}

// A turn as the page shows it: what happened is the headline ("Closed Living room blinds", "Couldn't work out which
// device you meant") and the outcome the small print ("Hunch did it · 13 ms", "Hunch did nothing"), as on the
// owner's Pi; `said` marks a headline that already says the note. A turn passed on, ignored or that could not reach
// an agent keeps its label as the headline, since there the outcome is what happened. The list leaves out the time.
function turnWords(t, withTime) {
  const label = text(t.label);
  const time = withTime ? ms(t.ms) : "";
  const did = didWhat(t);
  if (did) return { head: did, small: [label, time].filter(Boolean).join(" · ") };
  const none = t && t.outcome === "nothing" ? nothingHead(t) : "";
  if (none) return { head: none, small: [label, time].filter(Boolean).join(" · "), said: true };
  return { head: label, small: time };
}

// The stage at rest: "Last: Turned on Reading light · “could you pop it on” · Hunch did it · 12:30".
function lastLine(t, at) {
  const words = turnWords(t, false);
  const head = words.head !== text(t.label) ? words.head : "";
  return "Last: " + [head, quoted(t.sentence), text(t.label), at].filter(Boolean).join(" · ");
}

// A headline longer than this is set smaller, so it wraps in at most three lines on a phone.
const LONG_HEAD = 26;
function longHead(s) { return text(s).length > LONG_HEAD; }

// A best guess, or what was taught, in words: "close heating, fans or blinds", "look up the weather".
function guessWords(action, kind) {
  action = realAction(action);
  const verb = action ? WOULD[action] || action : "";
  const things = GUESS_THINGS[realKind(kind)] || "";
  if (verb && things) return verb + " " + things;
  if (verb) return verb + " something";
  return things ? "something to do with " + things : "";
}

/* ---------- the ribbons ---------- */

function chooseQuality() {
  const asked = params.get("render");
  if (asked === "full" || asked === "light" || asked === "still") return asked;
  if (reducedQuery && reducedQuery.matches) return "still";
  const small = Math.min(window.innerWidth || 9999, (screen && screen.width) || 9999) < 700;
  const cores = navigator.hardwareConcurrency || 8;
  const memory = navigator.deviceMemory || 8;
  const saving = navigator.connection && navigator.connection.saveData;
  return small || cores <= 4 || memory <= 2 || saving ? "light" : "full";
}

const stageNode = $("stage");
const silk = new Silk($("silk"), { quality: chooseQuality(), noWebgl: params.get("webgl") === "0" });

(function grain() {
  const c = document.createElement("canvas");
  c.width = c.height = 128;
  const g = c.getContext("2d"), d = g.createImageData(128, 128);
  for (let i = 0; i < d.data.length; i += 4) {
    const v = Math.random() * 255;
    d.data[i] = d.data[i + 1] = d.data[i + 2] = v;
    d.data[i + 3] = 13;
  }
  g.putImageData(d, 0, 0);
  stageNode.querySelector(".grain").style.backgroundImage = "url(" + c.toDataURL() + ")";
})();

function sizeSilk() {
  const r = stageNode.getBoundingClientRect();
  silk.resize(r.width, r.height);
}
sizeSilk();
if (window.ResizeObserver) new ResizeObserver(sizeSilk).observe(stageNode);
else window.addEventListener("resize", sizeSilk);

// Draw only while the stage can be seen: never in a hidden tab or with the stage scrolled away.
let stageVisible = true;
function updatePause() { silk.pause(document.hidden || !stageVisible); }
document.addEventListener("visibilitychange", updatePause);
if (window.IntersectionObserver) {
  new IntersectionObserver((entries) => {
    stageVisible = entries[entries.length - 1].isIntersecting;
    updatePause();
  }).observe(stageNode);
}

if (params.get("debug") === "1") {
  const dbg = $("debug");
  dbg.hidden = false;
  setInterval(() => {
    dbg.textContent = silk.quality + " " + silk.fps.toFixed(0) + " fps  " + silk.bw + "x" + silk.bh + "  " +
      silk.renderer + (silk.paused ? "  paused" : "");
  }, 500);
}

/* ---------- the stage ---------- */

const now = {
  label: $("now-label"), head: $("now-head"), sentence: $("now-sentence"), detail: $("now-detail"), box: $("now")
};
let stageState = "resting";
let stageTimer = 0;
let lastTurn = null;
let linkDown = false;

function usesHunch() { return !!(state && (state.assistants || []).some((a) => a.uses_hunch)); }
function listeningOn() { return !!(state && state.listening && state.listening.status === "on"); }

function land() {
  now.box.classList.remove("landing");
  void now.box.offsetWidth;
  now.box.classList.add("landing");
}

function setStage(next, content) {
  stageState = next;
  stageNode.dataset.state = next;
  if (next === "done") stageNode.dataset.flood = ""; else delete stageNode.dataset.flood;
  silk.set(next);
  clearTimeout(stageTimer);
  const firstRun = !!state && !homeAway() && !usesHunch() && next === "resting";
  $("setup").hidden = !firstRun;
  if (content) {
    // the words land only when they change: a state read again in the background leaves them still
    const moved = now.head.textContent !== (content.head || "") || now.sentence.textContent !== (content.sentence || "");
    now.label.textContent = content.label || "";
    now.head.textContent = content.head || "";
    now.head.classList.toggle("is-long", longHead(content.head));
    now.sentence.textContent = content.sentence || "";
    now.detail.textContent = content.detail || "";
    if (moved) land();
  }
}

// The app answers but cannot reach Home Assistant: what it knows of the home is out of date or missing.
function homeAway() { return !!state && state.connected === false; }

function rest() {
  if (!state) { setStage("resting", { head: "Hunch Home", label: "", sentence: "Opening Hunch", detail: "" }); return; }
  if (homeAway()) {
    setStage("resting", { head: "Waiting for Home Assistant", label: "", detail: "",
      sentence: "Hunch cannot reach Home Assistant just now. It keeps trying, and this page updates by itself." });
    return;
  }
  if (!usesHunch()) {
    setStage("resting", { head: "Hunch found " + foundSentence() + ".", label: "", sentence: "", detail: "" });
    renderSetup();
    return;
  }
  const using = (state.assistants || []).filter((a) => a.uses_hunch).map((a) => quoted(a.name));
  let head = "Ready", sentence = "Say something to " + listJoin(using) + " in your own words.";
  if (listeningOn()) {
    head = "Listening";
    const room = text(state.settings.mic_room);
    sentence = room ? "Speak in the " + room + ". There is no wake word." : "There is no wake word. Set the microphone's room in Settings.";
  }
  if (text(state.model_problem)) {
    head = "Not ready";
    sentence = text(state.model_problem) + " Everything you say goes to " + passName() + ". Restarting the app may help.";
  } else if (state.model_ready === false) {
    head = "Starting";
    sentence = "Hunch is loading. Until it is ready, what you say goes to " + passName() + ".";
  }
  let detail = "";
  if (lastTurn) detail = lastLine(lastTurn, when(lastTurn.time));
  setStage("resting", { head, sentence, detail, label: "" });
}

function showTurn(t) {
  const outcome = ["done", "passed", "ignored", "error", "nothing"].includes(t.outcome) ? t.outcome : "passed";
  const heard = t.source === "listening" ? "Heard by the microphone" : "From your assistant";
  if (outcome === "ignored") {
    // what listening ignored is not shown or kept: only that something was heard and let go
    setStage("ignored", { label: heard, head: text(t.label) || "Ignored", sentence: "", detail: "" });
  } else {
    lastTurn = t;
    const words = turnWords(t, true);
    setStage(outcome, { label: heard + " · " + when(t.time), head: words.head, sentence: quoted(t.sentence),
      detail: [words.small, words.said ? "" : text(t.note)].filter(Boolean).join(" · ") });
    if (outcome === "done") silk.pulse();
  }
  stageTimer = setTimeout(rest, HOLD_MS[outcome]);
}

function showHearing() {
  setStage("hearing", { label: "Microphone", head: "Hearing", sentence: "", detail: "" });
  stageTimer = setTimeout(rest, HEARING_MAX_MS);
}
function showDeciding() {
  setStage("deciding", { label: "Microphone", head: "Deciding", sentence: "", detail: "" });
  stageTimer = setTimeout(rest, DECIDING_MAX_MS);
}

// `down`: false, "hunch" (Hunch does not answer) or "home" (the page cannot reach Home Assistant, which serves it)
function setLink(down) {
  linkDown = down === true ? "hunch" : down;
  silk.setProblem(!!linkDown);
  renderLinkStatus();
}
function renderLinkStatus() {
  const node = $("link-status");
  if (linkDown === "home") { node.textContent = "Not connected to Home Assistant. Trying again."; return; }
  if (linkDown) { node.textContent = "Not connected to Hunch. Trying again."; return; }
  if (!state) { node.textContent = "Connecting"; return; }
  if (homeAway()) { node.textContent = "Not connected to Home Assistant"; return; }
  const using = (state.assistants || []).filter((a) => a.uses_hunch);
  node.textContent = using.length ? "Used by " + listJoin(using.map((a) => quoted(a.name))) : "Not set up yet";
}

/* ---------- first run ---------- */

function foundSentence() {
  const home = (state && state.home) || {};
  // kinds that read the same (a sensor and a binary sensor, say) are counted together
  const counted = new Map();
  (home.kinds || []).filter((k) => k && k.count > 0).forEach((k) => {
    const words = deviceWords(k);
    const was = counted.get(words[1]);
    counted.set(words[1], { words, count: (was ? was.count : 0) + k.count });
  });
  const parts = [...counted.values()].sort((a, b) => b.count - a.count).map((k) => plural(k.count, k.words));
  const rooms = (home.rooms || []).filter((r) => r.count > 0).length;
  if (!parts.length) return "no devices exposed to Assist yet";
  return listJoin(parts) + (rooms ? " in " + plural(rooms, ["room", "rooms"]) : "");
}

function setupCandidates() {
  return (state.assistants || []).filter((a) => !a.uses_hunch && a.can_set_up !== false)
    .sort((a, b) => (b.preferred ? 1 : 0) - (a.preferred ? 1 : 0));
}
// The sidebar box: offered only where the app has a sidebar entry of its own (not in Docker) that is still off.
function offerSidebar() { return !!state && state.sidebar === false; }

function renderSetup() {
  const list = setupCandidates();
  const pick = $("setup-pipeline");
  const chosen = pick.value;
  const key = JSON.stringify(list.map((a) => [a.id, a.name]));
  if (pick.dataset.key !== key) {   // rebuilt only when the list changes, so an open list is never closed
    pick.dataset.key = key;
    pick.textContent = "";
    list.forEach((a) => {
      const o = el("option", "", a.name);
      o.value = a.id;
      pick.appendChild(o);
    });
    if (chosen && list.some((a) => a.id === chosen)) pick.value = chosen;
  }
  $("setup-choose").hidden = list.length < 2;
  const a = list.find((x) => x.id === pick.value) || list[0];
  $("setup-sidebar-wrap").hidden = !offerSidebar();
  if (!a) {
    const others = (state.assistants || []).filter((x) => !x.uses_hunch);
    $("setup-says").textContent = others.length
      ? "Hunch understands English only, and none of your assistants is in English. Make an English one in " +
        "Home Assistant under Settings, Voice assistants. This page updates by itself."
      : "There is no voice assistant in this Home Assistant yet. Make one under Settings, Voice assistants. " +
        "This page updates by itself.";
    $("setup-go").hidden = true;
    $("setup-sidebar-wrap").hidden = true;
    return;
  }
  if (state.hunch_agent === null) {
    $("setup-says").textContent = "One step first: Home Assistant has found Hunch but not added it yet. Open " +
      "Settings, then Devices and services, find Hunch Home under Discovered and press Submit. This page " +
      "updates by itself.";
    $("setup-go").hidden = true;
    $("setup-sidebar-wrap").hidden = true;
    return;
  }
  $("setup-go").hidden = false;
  // the agent sentences will really go to: the owner's own choice of "Pass on to" when they made one (the set-up
  // leaves it alone), else the agent the assistant has now
  const ownChoice = !!state.pass_to_chosen;
  const agentId = ownChoice ? state.settings.pass_to : (a.agent && a.agent.id);
  const agent = agentWords(agentId, ownChoice ? passName() : text(a.agent && a.agent.name), a.name);
  // where Home Assistant's own sentences do not answer first yet, the button switches that on
  $("setup-says").textContent = "Hunch becomes the agent of " + quoted(a.name) + ". Home Assistant's own commands " +
    (a.answer_first ? "still answer first" : "answer first (Hunch switches that on)") + "; what is not a home command goes to " +
    agent + ". One button undoes it.";
}

// An agent as the set-up sentence names it. Home Assistant's own agent is called "Home Assistant", often the
// assistant's name too: "goes on to Home Assistant" would read as a loop.
function agentWords(id, name, assistantName) {
  name = text(name);
  if (id === "conversation.home_assistant" || (name && name.toLowerCase() === text(assistantName).toLowerCase())) {
    return id === "conversation.home_assistant" || name === "Home Assistant" ? "Home Assistant's own agent"
      : "its own agent, " + quoted(name);
  }
  return name || "the agent it has now";
}
$("setup-pipeline").addEventListener("change", renderSetup);

async function setupAssistant(id, button, noteNode, sidebar) {
  button.disabled = true;
  note(noteNode, "Setting up");
  try {
    await api("api/assistant/setup", { body: { pipeline_id: id, sidebar: !!sidebar } });
    await loadState();
    note(noteNode, "Done");
    rest();
  } catch (e) {
    noteRefusal(noteNode, e);
  } finally {
    button.disabled = false;
  }
}
$("setup-go").addEventListener("click", () => setupAssistant($("setup-pipeline").value || (setupCandidates()[0] || {}).id,
  $("setup-go"), $("setup-note"), offerSidebar() && $("setup-sidebar").checked));

/* ---------- your home ---------- */

function renderHome() {
  const home = state.home || {};
  const away = homeAway();
  $("home-sum").textContent = away
    ? "Hunch cannot see your home until it reaches Home Assistant again."
    : foundSentence().replace(/^./, (c) => c.toUpperCase()) + ".";
  const rooms = $("home-rooms");
  rooms.textContent = "";
  (home.rooms || []).filter((r) => r.count > 0).forEach((r, i) => {
    if (i) rooms.appendChild(document.createTextNode("  ·  "));
    rooms.appendChild(el("span", "room", text(r.name) + "\u00a0" + r.count));
  });

  const ul = $("assistants");
  ul.textContent = "";
  (state.assistants || []).forEach((a) => {
    const li = el("li");
    const top = el("div", "assistant-top");
    top.appendChild(el("p", "assistant-name", text(a.name) || "Assistant"));
    const other = text(a.agent && a.agent.name) || "another agent";
    top.appendChild(el("p", "assistant-uses" + (a.uses_hunch ? " is-hunch" : ""), a.uses_hunch ? "Uses Hunch" : "Uses " + other));
    li.appendChild(top);
    const nodeNote = el("p", "setting-note");
    nodeNote.setAttribute("role", "status");
    if (a.uses_hunch) {
      const before = a.previous_agent ? text(a.previous_agent.name) : "";
      const b = el("button", "button button-quiet assistant-undo", "Stop using Hunch");
      b.type = "button";
      b.setAttribute("aria-describedby", "undo-" + String(a.id).replace(/[^A-Za-z0-9_-]/g, ""));
      const says = el("p", "assistant-says", "What is not a home command goes on to " + passName() + ". Stop using " +
        "Hunch puts " + quoted(a.name) + " back as it was" + (before ? ", with " + before + " as its agent." : "."));
      says.id = b.getAttribute("aria-describedby");
      li.appendChild(says);
      b.addEventListener("click", async () => {
        b.disabled = true;
        note(nodeNote, "Putting it back");
        try {
          await api("api/assistant/undo", { body: { pipeline_id: a.id } });
          await loadState();
          rest();
        } catch (e) { noteRefusal(nodeNote, e); b.disabled = false; }
      });
      li.appendChild(b);
    } else if (a.can_set_up === false) {
      li.appendChild(el("p", "assistant-says", text(a.setup_note)));
    } else {
      const b = el("button", "button-text", "Add Hunch to this assistant");
      b.type = "button";
      b.setAttribute("aria-label", "Add Hunch to " + quoted(a.name));
      b.addEventListener("click", () => setupAssistant(a.id, b, nodeNote, false));
      li.appendChild(b);
    }
    li.appendChild(nodeNote);
    ul.appendChild(li);
  });
  // once for the section: Home Assistant does not let the app tidy up when it is removed, and an assistant left
  // pointing at Hunch answers nothing at all
  $("assistants-uninstall").textContent = "Before you uninstall Hunch Home, press Stop using Hunch here, or Assist will stop answering.";
  $("assistants-uninstall").hidden = !usesHunch();
  const empty = $("assistants-empty");
  empty.hidden = (state.assistants || []).length > 0;
  empty.textContent = away
    ? "Hunch will list your assistants once it reaches Home Assistant again."
    : "There is no voice assistant in this Home Assistant yet. Make one under Settings, Voice assistants.";

  const fixes = [];
  (home.fix || []).forEach((f) => {
    const names = (f.names || []).map(text).filter(Boolean);
    const name = names[0] || "";
    if (!name) return;
    if (f.type === "no_room" && names.length > 2) {
      // a new home with no rooms yet: one entry for them all, with their names on demand
      fixes.push(["Devices with no room", names.length + " devices have no room, so a sentence about a room " +
        "cannot reach them. Give them rooms in Home Assistant.", names]);
    } else if (f.type === "lock_named_like_room") {
      fixes.push([quoted(name), "A room and a lock or door in it are both called " + quoted(name) + ". Hunch will not " +
        "move it, and says so. Give it its own name, such as " + quoted(name + " lock") + "."]);
    } else if (f.type === "no_room") {
      fixes.push([quoted(name), "It has no room, so a sentence about a room, such as " +
        quoted("turn everything off in here") + ", cannot reach it. Give it a room in Home Assistant."]);
    } else if (f.type === "same_name") {
      const where = (f.rooms || []).map(text).filter(Boolean);
      const count = Math.max(2, where.length);
      fixes.push([quoted(name), (count === 2 ? "Two" : count) + " devices are called " + quoted(name) +
        (where.length ? ", in the " + listJoin(where) : "") + ". A sentence that only says " + quoted("the " +
        name.toLowerCase()) + " may reach the wrong one. Give each its own name."]);
    }
  });
  (state.assistants || []).forEach((a) => {
    if (a.stt && a.stt.fixed_phrases) {
      fixes.push(["Assistant " + quoted(a.name), "Its speech-to-text, " + text(a.stt.name) + ", knows only set " +
        "phrases, so your own words never reach Hunch when you speak. In Home Assistant, under Settings, Voice " +
        "assistants, choose a speech-to-text that writes down whatever you say."]);
    }
  });
  // found by its own integration in Home Assistant's entity registry, never by the name both agents share
  if (state.earlier_plugin === true) {
    fixes.push(["The earlier Hunch Home plug-in", "It is still installed. This app replaces it, and having both " +
      "is confusing, since two agents are called Hunch Home. You can remove it: under Settings, Devices & " +
      "services, delete the entry of the Hunch Home integration (not Wyoming Protocol, which is this app), " +
      "remove the plug-in where you installed it from, and restart Home Assistant."]);
  }
  const fl = $("fixes");
  fl.textContent = "";
  fixes.forEach(([name, says, which], i) => {
    const li = el("li");
    li.appendChild(el("p", "fix-name", name));
    li.appendChild(el("p", "fix-says", says));
    if (which) {
      const shown = which.slice(0, FIX_NAMES_SHOWN).map(quoted);
      const rest = which.length - shown.length;
      const list = el("p", "fix-which", listJoin(rest > 0 ? shown.concat([rest + " more"]) : shown) + ".");
      list.id = "fix-which-" + i;
      list.hidden = true;
      const b = el("button", "button-text fix-show", "Which ones");
      b.type = "button";
      b.setAttribute("aria-expanded", "false");
      b.setAttribute("aria-controls", list.id);
      b.addEventListener("click", () => {
        list.hidden = !list.hidden;
        b.setAttribute("aria-expanded", String(!list.hidden));
        b.textContent = list.hidden ? "Which ones" : "Hide them";
      });
      li.appendChild(b);
      li.appendChild(list);
    }
    fl.appendChild(li);
  });
  $("fixes-empty").hidden = fixes.length > 0 || away || !((home.kinds || []).some((k) => k && k.count > 0));
  $("fix-head").hidden = !fixes.length && $("fixes-empty").hidden;
  $("setup-fix-wrap").hidden = !fixes.length;
  $("setup-fix").textContent = fixes.length === 1 ? "One thing in your home is worth fixing"
    : fixes.length + " things in your home are worth fixing";
}

/* ---------- settings ---------- */

function fillSelect(node, options, value) {
  node.textContent = "";
  options.forEach(([v, label]) => {
    const o = el("option", "", label);
    o.value = v;
    node.appendChild(o);
  });
  node.value = value;
}

function renderSettings() {
  const s = state.settings || {};
  $("s-locks").checked = !!s.locks_and_doors;
  $("s-record").checked = !!s.keep_record;
  $("s-share").checked = !!s.share_counts;
  // a home command is never passed on: with the switch off, Hunch says so and nothing moves
  $("s-locks-says").textContent = "On: Hunch unlocks or opens only what you name. Off: it never does. " +
    "To keep a lock out of voice control altogether, stop exposing it to Assist.";
  const agents = (state.agents || []).map((a) => [a.id, text(a.name) || a.id]);
  if (s.pass_to && !agents.some(([id]) => id === s.pass_to)) agents.unshift([s.pass_to, passName()]);
  fillSelect($("s-pass"), agents, s.pass_to);
  const rooms = ((state.home && state.home.rooms) || []).map((r) => [r.name, r.name]);
  if (s.mic_room && !rooms.some(([r]) => r === s.mic_room)) rooms.push([s.mic_room, s.mic_room]);
  fillSelect($("s-room"), [["", "No room"]].concat(rooms), s.mic_room || "");
  renderListening();
  $("record-off-note").hidden = !!s.keep_record;
}

/* ---------- listening with no wake word ---------- */

const LISTEN_BUSY = ["fetching", "starting"];
function listenInfo() { return (state && state.listening) || {}; }
function listenStatus() { return text(listenInfo().status); }

function renderListening() {
  const l = listenInfo();
  const status = listenStatus();
  const model = l.model || {};
  const busy = LISTEN_BUSY.includes(status);
  const sw = $("s-listen");
  // the saved setting, whatever the microphone is doing: switching it off is always one click
  sw.checked = (state.settings && state.settings.listening === true) || busy;
  // the input's description where the app has one (such as "USB Audio Device"), its id only as a fallback
  const chosen = text(l.microphone_name) || text(l.microphone);
  const where = " To use another, choose it in the app's Audio setting on its Configuration tab in Home Assistant, then restart the app.";
  let mic = "";
  if (l.available === false) {
    mic = "No microphone found. Plug a USB microphone into this machine, choose it in the app's Audio setting on its Configuration tab, then restart the app.";
  } else if (chosen) {
    mic = (status === "on" ? "Listening through " : "Microphone: ") + chosen +
      (l.chosen === false ? ", this machine's default input." : ".") + where;
  }
  $("mic").textContent = mic;
  sw.disabled = l.available === false && !busy && status !== "on";

  const progress = model.progress;
  $("listen-progress").hidden = status !== "fetching";
  if (status === "fetching") {
    const p = progress || {};
    $("listen-bar").value = typeof p.fraction === "number" ? p.fraction : 0;
    $("listen-progress-says").textContent = p.stage === "unpacking"
      ? "Unpacking and checking the speech model: " + Math.round((p.fraction || 0) * 100) + "%. This can take a few minutes."
      : "Fetching the speech model: " + (p.done_mb || 0) + " of " + (p.total_mb || model.download_mb || "") + " MB.";
  }
  let says = "";
  if (status === "could not start" || (status === "needs model" && text(l.problem))) says = text(l.problem);
  else if (status === "starting") says = text(l.problem) ? text(l.problem) + (text(l.tried) ? " Last tried at " + when(l.tried) + "." : "")
    : "Starting to listen. Loading the speech model takes a few seconds.";
  else if (status === "on" && !text(state.settings && state.settings.mic_room)) says = "Set the microphone's room below, so a command that names no room acts there.";
  else if ((status === "off" || status === "needs model") && l.memory && text(l.memory.warning)) says = text(l.memory.warning);
  $("listen-warn").textContent = says;
  $("listen-sum-state").textContent = status === "on" ? "On" : busy ? "Starting" : says && status !== "off" ? "Needs a look" : "";
  // opened by itself while listening is on or starting, or has a problem to show; never closed on the owner
  if (status === "on" || busy || (says && status !== "off")) $("listen-fold").open = true;
  if (status !== "needs model" && status !== "could not start") $("listen-fetch").hidden = true;
  // no polling: the app sends a "state" event whenever listening's status or a fetch's progress changes
}

// The confirmation before the one download the app ever makes.
function askToFetch() {
  const l = listenInfo();
  const m = l.model || {};
  $("listen-fetch-says").textContent = "Listening needs a speech model first: " + (text(m.name) || "Parakeet") +
    ", " + (text(m.maker) || "NVIDIA") + "'s speech-to-text model. Hunch fetches it once from " +
    (text(m.source) || "GitHub") + ": about " + (m.download_mb || 480) + " MB to download, and about " +
    (m.disk_mb || 660) + " MB on this machine. This is the only thing Hunch Home ever fetches, and nothing it hears is sent anywhere.";
  $("listen-fetch-warn").textContent = l.memory && text(l.memory.warning) ? text(l.memory.warning) : "";
  $("listen-fetch").hidden = false;
  $("listen-fetch-go").focus();
}

async function switchListening(on, out) {
  $("s-listen").disabled = true;
  try {
    state.settings = await api("api/settings", { body: { listening: on } });
    await loadState();
    if (stageState === "resting") rest();
  } catch (e) {
    noteRefusal(out, e);
  } finally {
    renderSettings();
  }
}

$("listen-fetch-go").addEventListener("click", () => {
  $("listen-fetch").hidden = true;
  switchListening(true, document.querySelector('[data-note="listening"]'));
});
$("listen-fetch-cancel").addEventListener("click", () => { $("listen-fetch").hidden = true; $("s-listen").focus(); });

document.querySelectorAll("[data-setting]").forEach((control) => {
  control.addEventListener("change", async () => {
    const key = control.dataset.setting;
    const value = control.type === "checkbox" ? control.checked : (control.value || null);
    const out = document.querySelector('[data-note="' + key + '"]');
    if (key === "listening") {
      control.checked = !value;   // the switch follows what the app reports, not the click
      if (value && !(listenInfo().model || {}).ready) { askToFetch(); return; }
      await switchListening(value, out);
      return;
    }
    control.disabled = true;
    const asked = key === "share_counts" && shareAsking(out, value);
    try {
      state.settings = await api("api/settings", { body: { [key]: value } });
      if (key === "share_counts") await shareSaved(out, asked); else savedNote(out);
      if (key === "pass_to") {
        await loadState();
        if (stageState === "resting") rest();
      }
    } catch (e) {
      noteRefusal(out, e);
      if (asked) loadData();
    } finally {
      control.disabled = false;
      renderSettings();
      renderHome();
    }
  });
});

$("clear-ask").addEventListener("click", () => { $("clear-confirm").hidden = false; $("clear-ask").hidden = true; $("clear-go").focus(); });
$("clear-cancel").addEventListener("click", () => { $("clear-confirm").hidden = true; $("clear-ask").hidden = false; $("clear-ask").focus(); });
$("clear-go").addEventListener("click", async () => {
  const out = document.querySelector('[data-note="keep_record"]');
  $("clear-go").disabled = true;
  try {
    await api("api/record/clear", { body: {} });
    turns = [];
    lastTurn = null;
    renderTurns();
    note(out, "The record is empty");
    $("clear-confirm").hidden = true;
    $("clear-ask").hidden = false;
    if (stageState === "resting") rest();
  } catch (e) {
    noteRefusal(out, e);
  } finally {
    $("clear-go").disabled = false;
  }
});

/* ---------- try a sentence ---------- */

// The try box's two examples, from this home's own names: a simple one and one in people's own words, each of which
// Hunch carried out for two lights in the test machine, typed with no room. None without a light; the second only
// when that light has a room.
function tryExamples(home) {
  // a name that begins with "the" is said without it, so no example reads "the the lamp"
  const plain = (s) => text(s).toLowerCase().replace(/^the\s+/, "");
  const name = plain(home && home.example_light);
  if (!name) return [];
  const room = plain(home && home.example_room);
  const out = [["Try:", "turn on the " + name]];
  if (room) out.push(["or in your own words:", "it's a bit dark in the " + room]);
  return out;
}

// Each example is a line to press: it goes into the box and is tried (nothing in the home moves).
function renderTryExamples() {
  const list = tryExamples(state.home);
  const node = $("try-examples");
  const key = JSON.stringify(list);
  node.hidden = !list.length;
  if (node.dataset.key === key) return;   // rebuilt only when the home's names change
  node.dataset.key = key;
  node.textContent = "";
  list.forEach(([lead, sentence]) => {
    const line = el("p", "try-example");
    line.appendChild(el("span", "try-example-lead", lead + " "));
    const b = el("button", "button-text", sentence);
    b.type = "button";
    b.addEventListener("click", () => {
      $("try-text").value = sentence;
      $("try-form").requestSubmit();
    });
    line.appendChild(b);
    node.appendChild(line);
  });
}

function renderTryRooms() {
  renderTryExamples();
  const rooms = ((state.home && state.home.rooms) || []).map((r) => [r.name, r.name]);
  const node = $("try-room");
  const was = node.value;
  fillSelect(node, [["", "No room in particular"]].concat(rooms), was || "");
  $("try-room-wrap").hidden = !rooms.length;
}

$("try-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const input = $("try-text");
  const sentence = text(input.value);
  if (!sentence) { input.focus(); return; }
  const box = $("try-answer");
  const go = $("try-go");
  go.disabled = true;
  box.hidden = false;
  box.classList.add("is-waiting");
  const wasResting = stageState === "resting";
  if (wasResting) silk.set("deciding");
  try {
    const body = { text: sentence };
    if ($("try-room").value) body.room = $("try-room").value;
    renderTry(sentence, await api("api/try", { body }));
  } catch (err) {
    box.textContent = "";
    box.appendChild(el("p", "setting-note" + (err.calm ? "" : " is-error"), errorText(err)));
  } finally {
    box.classList.remove("is-waiting");
    go.disabled = false;
    if (wasResting && stageState === "resting") silk.set("resting");
  }
});

function renderTry(sentence, r) {
  const box = $("try-answer");
  box.textContent = "";
  const action = realAction(r.action), kind = realKind(r.kind);
  const names = (r.entities || []).map(text).filter(Boolean);
  let head;
  if (r.outcome === "done") {
    const verb = WOULD[action] || action;
    head = "Hunch would " + verb + (names.length ? " " + listJoin(names) : "") + ".";
  } else if (r.outcome === "ignored") {
    head = "Hunch would ignore this.";
  } else if (r.outcome === "nothing") {
    // a home command it does not carry out: answered by Hunch, never passed on
    head = "Hunch would do nothing.";
  } else {
    head = "Hunch would pass this to " + passName() + ".";
  }
  // why it would not act, in one plain sentence (the app maps every reason it can give); where the app's own line
  // already says "Hunch would do nothing: ...", that is the line, said once
  let why = r.outcome !== "done" ? text(r.why) || text(r.note) : "";
  if (r.outcome === "nothing" && /^Hunch would do nothing\b/.test(why)) { head = why; why = ""; }
  box.appendChild(el("p", "try-would", head));
  if (why) box.appendChild(el("p", "try-devices", why));
  // the guess in words, never the model's own labels
  if (r.outcome !== "done" && guessWords(action, kind)) {
    box.appendChild(el("p", "try-devices", "Its best guess: " + guessWords(action, kind) + "."));
  }
  // the sentence and the time taken; the score stays in the data, and the line above the box already says that
  // nothing in the home moves
  box.appendChild(el("p", "try-detail", [quoted(sentence), ms(r.ms)].filter(Boolean).join(" · ")));
}

/* ---------- what happened ---------- */

// `examples`: words shown beside each value (the value itself is what is sent)
function optionList(select, items, examples) {
  select.textContent = "";
  const first = el("option", "", "Choose");
  first.value = "";
  select.appendChild(first);
  items.forEach((v) => {
    const o = el("option", "", examples && examples[v] ? v + ": " + examples[v] : v);
    o.value = v;
    select.appendChild(o);
  });
}

function turnNode(t, isNew) {
  const li = $("turn-template").content.firstElementChild.cloneNode(true);
  if (isNew) li.classList.add("is-new");
  li.querySelector(".turn-sentence").textContent = quoted(t.sentence);
  const time = li.querySelector(".turn-time");
  time.textContent = when(t.time);
  time.dateTime = text(t.time);
  // what was done first, in the outcome's place and colour; "Hunch did it" small beside it
  const words = turnWords(t, false);
  const out = li.querySelector(".outcome");
  out.textContent = words.head;
  out.dataset.outcome = t.outcome;
  li.querySelector(".turn-what").textContent = words.small;
  const turnNote = li.querySelector(".turn-note");
  turnNote.textContent = text(t.note);
  turnNote.hidden = !text(t.note) || !!words.said;
  // changed again within 30 seconds: often a sign Hunch got one wrong, said only as what happened
  const undone = li.querySelector(".turn-undone");
  if (t.undone === "yes") {
    undone.textContent = changedAgain(t);
    undone.hidden = false;
  }

  const wrong = li.querySelector(".turn-wrong");
  const form = li.querySelector(".correct");
  const act = li.querySelector(".correct-action");
  const kind = li.querySelector(".correct-kind");
  const formNote = li.querySelector(".correct-note");
  const id = "c-" + String(t.id).replace(/[^A-Za-z0-9_-]/g, "");
  // nothing to correct when the agent could not be reached: Hunch's own reading is not what went wrong
  wrong.hidden = t.outcome === "error";
  form.id = id;
  wrong.setAttribute("aria-controls", id);
  wrong.setAttribute("aria-label", "That was wrong: " + quoted(t.sentence));
  optionList(act, teachList("actions", ACTIONS));
  optionList(kind, teachList("kinds", KINDS), KIND_EXAMPLES);
  wrong.addEventListener("click", () => {
    const open = form.hidden;
    form.hidden = !open;
    wrong.setAttribute("aria-expanded", String(open));
    if (open) act.focus();
  });
  li.querySelector(".correct-cancel").addEventListener("click", () => {
    form.hidden = true;
    wrong.setAttribute("aria-expanded", "false");
    wrong.focus();
  });
  const decline = li.querySelector(".correct-decline");
  decline.hidden = !canDecline();
  // what choosing "not a home command" does, in one line, before it is chosen
  const help = li.querySelector(".correct-help");
  help.hidden = !canDecline();
  help.textContent = "Not a home command: from the next time, Hunch passes this sentence on to " + passName() +
    ", which may still act on it, and ignores it when it is listening.";
  const button = form.querySelector("button[type=submit]");

  async function teach(action, kind, said) {
    button.disabled = decline.disabled = true;
    note(formNote, "");
    try {
      await api("api/teach", { body: { sentence: t.sentence, action, kind } });
      form.hidden = true;
      wrong.hidden = true;
      const done = li.querySelector(".turn-taught");
      done.hidden = false;
      done.textContent = "Taught: " + said + ". Used from the next sentence on.";
      loadTaught();
    } catch (err) {
      noteRefusal(formNote, err);
    } finally {
      button.disabled = decline.disabled = false;
    }
  }
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    if (!act.value || !kind.value) { note(formNote, "Choose both, then teach it.", true); return; }
    teach(act.value, kind.value, guessWords(act.value, kind.value));
  });
  // the model's own phrase for this is sent, never shown
  decline.addEventListener("click", () => teach(DECLINED, "none", "not a home command, so it goes on to " + passName()));
  return li;
}

function renderTurns() {
  const ol = $("turns");
  ol.textContent = "";
  turns.forEach((t) => ol.appendChild(turnNode(t, false)));
  const empty = $("turns-empty");
  empty.hidden = turns.length > 0;
  if (!turns.length) {
    empty.textContent = usesHunch()
      ? "Nothing yet. Say something to your assistant, and it appears here."
      : "Nothing yet. Once Hunch is added to your assistant, what it hears appears here.";
  }
  $("turns-more").hidden = recordFull || turns.length < recordLimit;
}

function addLiveTurn(t) {
  if (t.outcome === "ignored") return;   // listening keeps nothing it ignored, and neither does the page
  if (turns.some((x) => x.id === t.id)) return;
  turns.unshift(t);
  if (turns.length > 400) turns.length = 400;
  $("turns").prepend(turnNode(t, true));
  $("turns-empty").hidden = true;
}

async function loadRecord() {
  try {
    const got = await api("api/record?limit=" + recordLimit);
    turns = Array.isArray(got) ? got : [];
    recordFull = turns.length < recordLimit;
    if (!lastTurn && turns.length) lastTurn = turns[0];
    renderTurns();
  } catch (e) {
    $("turns-empty").hidden = false;
    $("turns-empty").textContent = errorText(e);
  }
}
$("turns-more").addEventListener("click", () => { recordLimit += RECORD_STEP; loadRecord(); });

/* ---------- what you have taught ---------- */

function renderTaught() {
  const ul = $("taught-list");
  ul.textContent = "";
  taught.forEach((k) => {
    const li = el("li", k.active === false ? "is-inactive" : "");
    const said = el("p", "taught-said", quoted(k.sentence));
    const means = realAction(k.action) ? guessWords(k.action, k.kind) : "not a home command";
    said.appendChild(el("span", "taught-means", "means " + means));
    if (k.active === false) said.appendChild(el("span", "taught-off", "Not used: this version of Hunch cannot learn this answer"));
    li.appendChild(said);
    const b = el("button", "button-text", "Remove");
    const out = el("p", "setting-note taught-note");
    out.setAttribute("role", "status");
    b.type = "button";
    b.setAttribute("aria-label", "Remove " + quoted(k.sentence));
    b.addEventListener("click", async () => {
      b.disabled = true;
      try {
        await api("api/taught/" + encodeURIComponent(k.id), { method: "DELETE" });
        taught = taught.filter((x) => x.id !== k.id);
        renderTaught();
      } catch (e) {
        b.disabled = false;
        noteRefusal(out, e);
      }
    });
    li.appendChild(b);
    li.appendChild(out);
    ul.appendChild(li);
  });
  const notice = state && state.teach ? text(state.teach.notice) : "";
  // hidden while there is nothing taught: That was wrong, under each sentence, is where teaching starts
  $("taught").hidden = !taught.length && !notice;
  $("taught-notice").textContent = notice;
  $("taught-notice").hidden = !notice;
}
async function loadTaught() {
  try {
    const got = await api("api/taught");
    taught = Array.isArray(got) ? got : [];
  } catch (e) {
    taught = [];
  }
  renderTaught();
}

/* ---------- your data ---------- */

// What the app keeps, the record to keep or to send, the daily counts exactly as they would be sent, and the
// install reference (api/data). The two downloads come from the app's own page server and are asked for by this
// script, with the page's header, so only this page can have them.
let yourData = null;

const longDay = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "long", timeZone: "UTC" });
function dayName(day) {
  const d = new Date(text(day) + "T12:00:00Z");
  return isNaN(d) ? text(day) : longDay.format(d);
}
// While the settings file is damaged and left where it is, a change is used but not saved: said instead of "Saved".
const UNSAVED = "In use until the app restarts. Hunch could not save it: see the note at the top of this page.";
function settingsUnsaved() { return !!(state && state.settings_saved === false); }
function savedNote(out) { if (settingsUnsaved()) say(out, UNSAVED); else note(out, "Saved"); }
function savedWords() { return settingsUnsaved() ? UNSAVED + " " : "Saved. "; }
// A note that stays until the next change, unlike note()'s passing "Saved".
function say(node, message) {
  clearTimeout(node._t);
  node.classList.remove("is-error");
  node.textContent = message || "";
}
// A count's name in the readable table: the model's word for a declined sentence (DECLINED alone, or with
// " / none" after it) is said in the page's words; the raw message below the table keeps it as sent.
function countWord(k) {
  return k === DECLINED || k.startsWith(DECLINED + " /") ? "Not a home command" : k;
}
function tally(counts) {
  const parts = Object.entries(counts || {}).map(([k, n]) => countWord(k) + ": " + n);
  return parts.length ? parts.join("\n") : "none";
}

// The message's fields, in its order, as the table names them; values in the message's own words.
const COUNT_ROWS = [
  ["schema", "Message format"], ["day", "Day counted (UTC)"], ["install_id", "Install number"],
  ["plugin_version", "Hunch Home version"], ["model_versions", "Model version"], ["sentences", "Sentences"],
  ["by_action_and_kind", "Commands"], ["by_outcome", "What Hunch did with them"],
  ["handed_back_by_reason", "Why it passed sentences on"], ["errors", "Errors from Home Assistant"],
  ["undone", "Put back soon after"], ["scores", "Hunch's confidence"]
];

function renderCounts(sharing) {
  const { counts: c = {}, message: raw = "" } = sharing;
  const body = $("data-counts").tBodies[0];
  body.textContent = "";
  COUNT_ROWS.forEach(([key, name]) => {
    const tr = el("tr");
    const th = el("th", "", name);
    th.scope = "row";
    let value = c[key];
    if (key === "install_id") value = "not shown here";
    else if (key === "day") value = dayName(value);
    else if (value && typeof value === "object") value = tally(value);
    tr.appendChild(th);
    tr.appendChild(el("td", "", String(value === undefined || value === null ? "" : value)));
    body.appendChild(tr);
  });
  $("data-counts-caption").textContent = "The daily counts for " + dayName(sharing.day);
  $("data-message").textContent = text(raw);
  $("data-counts").hidden = $("data-raw").hidden = !text(raw);
}

function shareSays(s) {
  const day = dayName(s.day);
  const none = "Yesterday, " + day + ", has no sentences in the record, so nothing is sent for it.";
  if (!s.chosen) {
    return s.sends ? "Sharing is off, so nothing is sent. Were it on, Hunch would send this for " + day + "."
      : "Sharing is off, so nothing is sent. " + none;
  }
  if (s.deletion && (s.deletion.status === "owed" || s.deletion.status === "asking")) {
    return "Sharing is on, but nothing is sent until the deletion below has gone through.";
  }
  if (!s.sends) return "Sharing is on. " + none;
  if (s.last_sent_day === s.day) return "Sharing is on. The counts for " + day + " were sent. This is that day's message as the record has it now.";
  return "Sharing is on. Hunch sends this for " + day + " within the hour.";
}

function deletionSays(d) {
  if (!d) return "";
  if (d.status === "asking") return "Asking xevall to delete the counts this home sent.";
  if (d.status === "owed") {
    return d.tried ? "Not deleted yet: Hunch could not reach xevall's server at " + when(d.tried) + ". It tries again every hour, and sends nothing meanwhile."
      : "A deletion of the counts this home sent is waiting to be asked for. Hunch asks every hour until it goes through.";
  }
  if (d.status === "done") return "Deleted: xevall's server deleted the counts this home sent, at " + when(d.done) + ".";
  return "";
}

function renderData() {
  const d = yourData;
  if (!d) return;
  const s = d.sharing || {};
  const deletion = s.deletion || {};
  $("data-share-says").textContent = shareSays(s);
  renderCounts(s);
  $("data-sent").textContent = s.last_sent_day ? "The last counts sent were for " + dayName(s.last_sent_day) + "."
    : "Nothing has been sent from this home.";
  // a deletion done long ago is not news once sharing is back on
  $("data-deletion").textContent = s.chosen && deletion.status === "done" ? "" : deletionSays(deletion);
  let uninstall = "";
  if (deletion.status === "owed" || deletion.status === "asking") {
    uninstall = "Uninstalling cannot ask for this deletion. Leave the app running until it is done.";
  } else if (s.chosen) {
    uninstall = "Uninstalling cannot ask for a deletion. If you want this home's counts deleted, switch sharing off first and leave the app running for a minute.";
  }
  $("data-uninstall").textContent = uninstall;
  const install = d.install || {};
  $("data-ref").textContent = text(install.reference);
  const earlier = Array.isArray(install.earlier) ? install.earlier : [];
  const list = $("data-earlier");
  list.textContent = "";
  earlier.forEach((e) => {
    const li = el("li");
    li.appendChild(text(e.reference) ? el("code", "data-ref-small", text(e.reference))
      : el("span", "data-unread", "Its number could not be read; the file is kept as it is."));
    li.appendChild(el("span", "data-file", text(e.file)));
    list.appendChild(li);
  });
  $("data-aside").hidden = !earlier.length;
  // opened by itself when the owner must see something: a damaged record set aside, a deletion still owed
  if (earlier.length || deletion.status === "owed" || deletion.status === "asking") $("data-fold").open = true;
  // with Keep a record off, what was kept before is still kept: said, and it can be cleared here too
  const kept = d.kept || {};
  $("data-record-off").hidden = kept.on !== false;
  $("data-clear").hidden = kept.on !== false || !(kept.turns > 0);
}

async function loadData() {
  try {
    yourData = await api("api/data");
    renderData();
  } catch (e) {
    $("data-share-says").textContent = errorText(e);
  }
}

// Switching sharing off asks for the deletion at once, and the app answers once it has been asked: the note
// under the switch says so while it waits, then says how it went.
function shareAsking(out, on) {
  const was = !!(yourData && yourData.sharing && yourData.sharing.chosen);
  if (on || !was) return false;
  say(out, savedWords() + "Asking xevall to delete the counts this home sent.");
  $("data-deletion").textContent = deletionSays({ status: "asking" });
  return true;
}
async function shareSaved(out, asked) {
  await loadData();
  const d = yourData && yourData.sharing ? yourData.sharing.deletion || {} : {};
  if (!asked) { savedNote(out); return; }
  if (d.status === "done") say(out, savedWords() + "xevall's server deleted the counts this home sent.");
  else say(out, savedWords() + "Hunch could not reach xevall's server, so the counts are not deleted yet. It tries again every hour.");
}

function fileName(disposition) {
  const m = /filename="([A-Za-z0-9._-]+)"/.exec(disposition || "");
  return m ? m[1] : "";
}

// One of the two files, asked for with the page's header: the server answers nothing else.
async function fetchDownload(kind) {
  let res;
  try {
    res = await fetch("api/record/download?kind=" + kind, { headers: { "X-Hunch-Page": "1" }, cache: "no-store" });
  } catch (e) {
    throw refusal(0, "Hunch did not answer. Check that the app is running, then try again.");
  }
  if (!res.ok) {
    let said = "";
    try { said = text((await res.json()).error); } catch (e) { said = ""; }
    throw refusal(res.status, said || FALLBACK[res.status] || FALLBACK.other);
  }
  return res;
}

// The page cannot know whether a saved file reached the owner (inside Home Assistant's phone apps it may not),
// so it says only that the file was sent to the browser, and what to do if nothing arrived.
async function download(kind, button) {
  const out = $("data-get-note");
  button.disabled = true;
  try {
    const res = await fetchDownload(kind);
    const name = fileName(res.headers.get("Content-Disposition")) || "hunch-home-" + kind + ".json";
    const url = URL.createObjectURL(await res.blob());
    const a = el("a");
    a.href = url;
    a.download = name;
    a.hidden = true;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 60000);
    say(out, kind === "report" ? "Sent to your browser as " + name + "; if nothing arrived, use Copy the report."
      : "Sent to your browser as " + name + "; if nothing arrived, open this page in a web browser rather than Home Assistant's phone app.");
  } catch (e) {
    noteRefusal(out, e);
  } finally {
    button.disabled = false;
  }
}
$("data-full").addEventListener("click", () => download("full", $("data-full")));
$("data-report").addEventListener("click", () => download("report", $("data-report")));

// The same report as text, to the clipboard. The clipboard is asked at once, with the text to follow, since a
// browser may refuse it once the click is past; if it is refused, the report is shown selected to copy by hand.
$("data-report-copy").addEventListener("click", async () => {
  const out = $("data-get-note");
  const button = $("data-report-copy");
  const area = $("data-report-text");
  button.disabled = true;
  const pending = fetchDownload("report").then((res) => res.text());
  let copied = false;
  try {
    if (navigator.clipboard && window.isSecureContext && window.ClipboardItem) {
      try {
        await navigator.clipboard.write([new ClipboardItem({
          "text/plain": pending.then((t) => new Blob([t], { type: "text/plain" }))
        })]);
        copied = true;
      } catch (e) { copied = false; }
    }
    const report = await pending;
    if (!copied) copied = await copyText(report);
    if (copied) {
      area.hidden = true;
      say(out, "The report is copied. Paste it into your bug report.");
    } else {
      area.textContent = report;
      area.hidden = false;
      area.focus();
      selectAll(area);
      say(out, "Selected. Copy it from here.");
    }
  } catch (e) {
    noteRefusal(out, e);
  } finally {
    button.disabled = false;
  }
});

$("data-clear-ask").addEventListener("click", () => {
  $("data-clear-confirm").hidden = false; $("data-clear-ask").hidden = true; $("data-clear-go").focus();
});
$("data-clear-cancel").addEventListener("click", () => {
  $("data-clear-confirm").hidden = true; $("data-clear-ask").hidden = false; $("data-clear-ask").focus();
});
$("data-clear-go").addEventListener("click", async () => {
  const out = $("data-clear-note");
  $("data-clear-go").disabled = true;
  try {
    await api("api/record/clear", { body: {} });
    turns = [];
    lastTurn = null;
    renderTurns();
    $("data-clear-confirm").hidden = true;
    $("data-clear-ask").hidden = false;
    await loadData();
    note(out, "The record is empty");
    if (stageState === "resting") rest();
  } catch (e) {
    noteRefusal(out, e);
  } finally {
    $("data-clear-go").disabled = false;
  }
});

// The page's fallback when the clipboard is refused: the text is selected, to copy by hand.
function selectAll(node) {
  const range = document.createRange();
  range.selectNodeContents(node);
  const sel = window.getSelection();
  sel.removeAllRanges();
  sel.addRange(range);
}

async function copyText(value) {
  if (navigator.clipboard && window.isSecureContext) {
    try { await navigator.clipboard.writeText(value); return true; } catch (e) { /* the older way below */ }
  }
  const area = el("textarea");
  area.value = value;
  area.setAttribute("readonly", "");
  area.style.position = "fixed";
  area.style.opacity = "0";
  document.body.appendChild(area);
  area.select();
  let done = false;
  try { done = document.execCommand("copy"); } catch (e) { done = false; }
  area.remove();
  return done;
}
$("data-ref-copy").addEventListener("click", async () => {
  const ref = text($("data-ref").textContent);
  if (!ref) return;
  const out = $("data-ref-note");
  if (await copyText(ref)) { note(out, "Copied"); return; }
  selectAll($("data-ref"));
  say(out, "Selected. Copy it from here.");
});

/* ---------- state and the live stream ---------- */

// Files of the app's own that could not be read at start and were kept aside: said once, plainly, at the top.
function renderNotices() {
  const list = $("notices");
  const said = Array.isArray(state.notices) ? state.notices.map(text).filter(Boolean) : [];
  list.textContent = "";
  said.forEach((s) => list.appendChild(el("li", "", s)));
  list.hidden = !said.length;
}

async function loadState() {
  state = await api("api/state");
  renderHome();
  renderSettings();
  renderTryRooms();
  renderLinkStatus();
  renderTaught();   // the teaching's notice comes with the state
  renderNotices();
  $("versions").textContent = "Hunch Home " + text(state.version) + "  ·  model " + text(state.model_version);
  const privacy = text(state.privacy_url);
  if (/^https:\/\//.test(privacy)) $("privacy-link").href = privacy;
  loadData();   // the "Your data" section: its own address, read alongside the state
  keepWatching();
}

function handle(ev) {
  if (!ev || typeof ev !== "object") return;
  switch (ev.event) {
    case "turn":
      showTurn(ev);
      addLiveTurn(ev);
      // a sentence came through Home Assistant, so it is reachable again: the line clears at once
      if (homeAway() && ev.source !== "listening") loadState().then(renderLinkStatus).catch(() => {});
      break;
    case "hearing":
      showHearing();
      break;
    case "quiet":
      // a sound too short to be speech: back to rest, with nothing to decide
      if (stageState === "hearing") { if (ev.speech === false) rest(); else showDeciding(); }
      break;
    case "home":
    case "state":   // the model ready or failed, Home Assistant lost or reached, an assistant settled
      loadState().then(() => { if (stageState === "resting") rest(); }).catch(() => {});
      break;
  }
}

let offlineTimer = 0;
let retry = 1000;

// The live stream dropped: who is away? Inside Home Assistant the page is reached through Home Assistant itself,
// so no answer at all means Home Assistant is away (it is restarting); an answer that is not Hunch's (a 502 from
// the proxy) means Hunch is. In Docker (no sidebar entry) the page is Hunch's own, so no answer means Hunch.
async function probeLink() {
  let res = null;
  try {
    res = await fetch("api/state", { headers: { "X-Hunch-Page": "1" }, cache: "no-store" });
  } catch (e) {
    const insideHomeAssistant = !!state && state.sidebar !== null && state.sidebar !== undefined;
    if (insideHomeAssistant) setLink("home"); else setLink("hunch");
    return;
  }
  if (res.ok) return;   // Hunch answers: the stream is only reconnecting
  if (res.status === 503) setLink("home"); else setLink("hunch");
}
function connect() {
  let es;
  try { es = new EventSource("api/events"); } catch (e) { setTimeout(connect, retry); return; }
  es.onmessage = (m) => {
    let ev;
    try { ev = JSON.parse(m.data); } catch (e) { return; }
    handle(ev);
  };
  es.onopen = () => {
    retry = 1000;
    clearTimeout(offlineTimer);
    offlineTimer = 0;
    if (linkDown) {
      setLink(false);
      // anything missed while away
      loadState().then(loadRecord).then(() => { if (stageState === "resting") rest(); }).catch(() => {});
    }
  };
  es.onerror = () => {
    if (!offlineTimer && !linkDown) offlineTimer = setTimeout(() => { offlineTimer = 0; probeLink(); }, OFFLINE_AFTER_MS);
    if (es.readyState === 2) {
      es.close();
      setTimeout(connect, retry);
      retry = Math.min(retry * 2, 8000);
    }
  };
}

// For checks and screenshots: show an event as if it came from the stream.
window.hunchPage = { handle, silk };

// While Home Assistant is out of reach, nothing is set up yet, or the model is not ready, the state is read again
// every few seconds, so the page follows what happens without a reload.
const POLL_MS = 6000;
let pollTimer = 0;
function keepWatching() {
  clearTimeout(pollTimer);
  // also while the model is not ready: a fallback, should the stream's "state" event be missed
  if (!state || (!homeAway() && usesHunch() && state.model_ready !== false)) return;
  pollTimer = setTimeout(async () => {
    if (!document.hidden) {
      try {
        const wasAway = homeAway();
        await loadState();
        if (wasAway && !homeAway()) await loadRecord();
        if (stageState === "resting") rest();
      } catch (e) { /* the live stream reports a lost connection */ }
    }
    keepWatching();
  }, POLL_MS);
}

async function start() {
  rest();
  try {
    await loadState();
  } catch (e) {
    setStage("error", { label: "", head: "Hunch is not answering", detail: "",
      sentence: e.status === 503 ? errorText(e) : "Check that the app is running. This page keeps trying." });
    setLink(true);
    setTimeout(start, 4000);
    return;
  }
  setLink(false);
  await Promise.all([loadRecord(), loadTaught()]);
  $("page").hidden = false;
  rest();
  connect();
}
start();
