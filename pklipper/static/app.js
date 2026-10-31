"use strict";

const SEEK_STEP = 2;
const NUDGE_STEP = 1;
const RATES = [0.25, 0.5, 1, 2];
const MIN_CLIP = 0.5;
const MAX_ZOOM = 200;

const state = {
  items: [],
  days: [],
  dayNames: {},
  day: null,
  visible: [],
  current: -1,
  clips: [],
  active: -1,
  marker: null,
  looping: true,
  dragging: false,
  generation: 0,
  rate: 1,
  zoom: 1,
  offset: 0,
  history: [],
  media: { scale: 1, x: 0, y: 0 },
};

const MAX_MEDIA_ZOOM = 20;
const PAN_STEP = 80;
const PAN_DIRECTIONS = {
  ArrowLeft: [1, 0], ArrowRight: [-1, 0], ArrowUp: [0, 1], ArrowDown: [0, -1],
};

function shownMedia() {
  const item = currentItem();
  return item && item.kind === "photo" ? photo : player;
}

/** The media's box as it would sit with no zoom applied. */
function restingBox(node) {
  const applied = node.style.transform;
  node.style.transform = "none";
  const box = node.getBoundingClientRect();
  node.style.transform = applied;
  return box;
}

function applyMediaZoom() {
  const { scale, x, y } = state.media;
  shownMedia().style.transform = `translate(${x}px, ${y}px) scale(${scale})`;
  element("screen").toggleAttribute("data-zoomed", scale > 1);
}

function resetMediaZoom() {
  state.media = { scale: 1, x: 0, y: 0 };
  player.style.transform = "";
  photo.style.transform = "";
  element("screen").removeAttribute("data-zoomed");
}

/** Zoom the picture about the cursor, the way a map does. */
function zoomMedia(factor, clientX, clientY) {
  const box = restingBox(shownMedia());
  const scale = Math.min(Math.max(state.media.scale * factor, 1), MAX_MEDIA_ZOOM);
  const held = {
    x: (clientX - box.left - state.media.x) / state.media.scale,
    y: (clientY - box.top - state.media.y) / state.media.scale,
  };
  state.media.scale = scale;
  state.media.x = scale === 1 ? 0 : clientX - box.left - held.x * scale;
  state.media.y = scale === 1 ? 0 : clientY - box.top - held.y * scale;
  applyMediaZoom();
}

function panMedia(dx, dy) {
  if (state.media.scale === 1) return;
  state.media.x += dx;
  state.media.y += dy;
  applyMediaZoom();
}

/** Snapshot the clips so ctrl+z can put them back. */
function remember() {
  state.history.push(JSON.stringify({ clips: state.clips, active: state.active }));
  if (state.history.length > 50) state.history.shift();
}

function undo() {
  const previous = state.history.pop();
  if (!previous) {
    toast("nothing to undo", true);
    return;
  }
  const restored = JSON.parse(previous);
  state.clips = restored.clips;
  state.active = restored.active;
  state.marker = null;
  nameBox.value = activeClip()?.name ?? "";
  drawTimeline();
  updateSaveBar();
  playActiveClip();
}

/** How long the file under the cursor is, whatever the player has loaded. */
function mediaSpan() {
  return currentItem()?.duration || player.duration || 0;
}

/** The stretch of the video the bar currently shows, in seconds. */
function view() {
  const span = mediaSpan();
  const width = span / state.zoom;
  const start = Math.min(Math.max(state.offset, 0), Math.max(span - width, 0));
  return { span, start, width: width || 1 };
}

/** Where `time` sits across the bar, 0 at its left edge and 1 at its right. */
function acrossBar(time) {
  const window = view();
  return (time - window.start) / window.width;
}

/** The time at `fraction` of the way across the bar. */
function atFraction(fraction) {
  const window = view();
  return window.start + fraction * window.width;
}

function zoomBy(factor, anchor) {
  const span = view().span;
  if (!span) return;
  const held = atFraction(anchor);
  state.zoom = Math.min(Math.max(state.zoom * factor, 1), MAX_ZOOM);
  state.offset = held - (span / state.zoom) * anchor;
  drawTimeline();
}

function keepPlayheadInView() {
  if (state.zoom === 1) return;
  const window = view();
  const at = player.currentTime;
  if (at < window.start || at > window.start + window.width) {
    state.offset = at - window.width / 2;
    drawTimeline();
  }
}

const element = (id) => document.getElementById(id);
const player = element("player");
const photo = element("photo");
const nameBox = element("name");

function stamp(seconds) {
  if (seconds === null || Number.isNaN(seconds)) return "–";
  const minutes = Math.floor(seconds / 60);
  return `${minutes}:${(seconds % 60).toFixed(3).padStart(6, "0")}`;
}

function short(seconds) {
  if (seconds === null) return "–";
  const minutes = Math.floor(seconds / 60);
  return `${minutes}:${(seconds % 60).toFixed(1).padStart(4, "0")}`;
}

function currentItem() {
  return state.visible[state.current] || null;
}

function activeClip() {
  return state.clips[state.active] || null;
}

function frame() {
  const item = currentItem();
  return item && item.fps ? 1 / item.fps : 0.04;
}

async function getJSON(url, options) {
  const answer = await fetch(url, options);
  return { ok: answer.ok, body: await answer.json() };
}

/** POST `payload` to `url`, toasting the refusal; resolves to the body or null. */
async function postJSON(url, payload) {
  const { ok, body } = await getJSON(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (ok) return body;
  toast(body.error || "the server refused", true);
  return null;
}

function toast(message, bad) {
  const box = element("toast");
  box.textContent = message;
  box.hidden = false;
  if (bad) box.dataset.bad = "1"; else delete box.dataset.bad;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { box.hidden = true; }, 3200);
}

function notice(message) {
  element("notice").hidden = !message;
  element("notice-text").textContent = message || "";
}

async function load() {
  const { body } = await getJSON("/api/library");
  state.items = body.items;
  state.days = body.days;
  state.dayNames = body.dayNames;
  state.day = state.days[0] || null;
  applyFilter(0);
}

function renderDays() {
  const nav = element("days");
  nav.replaceChildren();
  for (const day of state.days) {
    const button = document.createElement("button");
    button.textContent = state.dayNames[day] ? `${day} · ${state.dayNames[day]}` : day;
    button.title = "double-click to name this day";
    button.setAttribute("aria-current", String(day === state.day));
    button.onclick = () => { state.day = day; applyFilter(0); };
    button.ondblclick = () => nameDay(day);
    nav.append(button);
  }
}

async function nameDay(day) {
  const name = prompt(`Name for ${day} (empty to clear)`, state.dayNames[day] || "");
  if (name === null) return;
  const body = await postJSON("/api/day", { day, name });
  if (!body) return;
  state.dayNames[day] = name.trim();
  renderDays();
  toast(`${day} saves into ${body.saved}`);
}

function applyFilter(startAt) {
  const hideSaved = element("hide-saved").checked;
  state.visible = state.items.filter((item) =>
    item.day === state.day && !(hideSaved && item.saves.length));
  renderDays();
  renderStrip();
  fetch(`/api/warm?day=${encodeURIComponent(state.day)}`).catch(() => undefined);
  const clips = state.visible.reduce((total, item) => total + item.saves.length, 0);
  element("count").textContent = `${state.visible.length} files · ${clips} keepers`;
  select(Math.min(startAt, state.visible.length - 1));
}

function renderStrip() {
  const strip = element("filmstrip");
  strip.replaceChildren();
  state.visible.forEach((item, index) => {
    const tile = document.createElement("div");
    tile.className = "tile";
    tile.dataset.index = String(index);
    const thumb = document.createElement("img");
    thumb.loading = "lazy";
    thumb.src = `/api/thumb?f=${encodeURIComponent(item.relpath)}`;
    const meta = document.createElement("div");
    meta.className = "meta";
    const clock = item.captured.slice(11, 16);
    const length = item.kind === "video" ? `${item.duration.toFixed(0)}s` : "photo";
    meta.innerHTML =
      `<span class="name">${item.label}</span>` +
      `<span class="sub">${clock} · ${length}</span>`;
    if (item.saves.length) {
      const kept = document.createElement("span");
      kept.className = "kept";
      const count = item.saves.length;
      kept.textContent = `✓ ${count} clip${count > 1 ? "s" : ""}: ` +
        item.saves.map((save) => save.name).join(", ");
      meta.append(kept);
    }
    tile.append(thumb, meta);
    tile.onclick = () => select(index);
    strip.append(tile);
  });
  highlightTile();
}

function highlightTile() {
  for (const tile of document.querySelectorAll(".tile")) {
    const active = Number(tile.dataset.index) === state.current;
    tile.setAttribute("aria-current", String(active));
    if (active) tile.scrollIntoView({ block: "nearest" });
  }
}

function select(index) {
  state.generation += 1;
  if (!state.visible.length) {
    state.current = -1;
    player.removeAttribute("data-live");
    photo.removeAttribute("data-live");
    element("title").textContent = "nothing to show";
    element("timeline").hidden = true;
    return;
  }
  state.current = Math.max(0, Math.min(index, state.visible.length - 1));
  const item = currentItem();
  state.zoom = 1;
  state.offset = 0;
  state.history = [];
  resetMediaZoom();
  state.clips = item.saves.map((save, saveIndex) => ({
    start: save.start ?? null, end: save.end ?? null, name: save.name, saved: true,
    saveIndex,
  }));
  state.active = -1;
  state.marker = null;
  nameBox.value = item.kind === "photo" ? item.saves[0]?.name ?? "" : "";
  element("title").textContent = `${item.label} · ${item.day}`;
  highlightTile();
  if (item.kind === "video") {
    addClip();
    showVideo(item, state.generation);
  } else {
    showPhoto(item);
  }
  updateSaveBar();
}

function showPhoto(item) {
  player.pause();
  player.removeAttribute("src");
  player.removeAttribute("data-live");
  element("timeline").hidden = true;
  notice("");
  photo.src = `/api/photo?f=${encodeURIComponent(item.relpath)}`;
  photo.dataset.live = "1";
}

async function showVideo(item, token) {
  photo.removeAttribute("data-live");
  photo.removeAttribute("src");
  element("timeline").hidden = false;
  player.removeAttribute("data-live");
  player.poster = `/api/thumb?f=${encodeURIComponent(item.relpath)}`;
  notice("preparing a playable proxy…");
  const outcome = await waitForProxy(item.relpath, token);
  if (state.generation !== token) return;
  if (outcome.state !== "ready") {
    notice(outcome.error || `ffmpeg could not prepare ${item.label}`);
    return;
  }
  notice("");
  player.src = `/api/video?f=${encodeURIComponent(item.relpath)}`;
  player.dataset.live = "1";
  player.playbackRate = state.rate;
  player.play().catch(() => notice("press space to play"));
  prefetchNeighbours();
}

async function waitForProxy(relpath, token) {
  const url = `/api/prepare?f=${encodeURIComponent(relpath)}`;
  let { body } = await getJSON(url);
  while ((body.state === "queued" || body.state === "working")
         && state.generation === token) {
    await new Promise((done) => setTimeout(done, 600));
    ({ body } = await getJSON(url));
  }
  return body;
}

function prefetchNeighbours() {
  state.visible.slice(state.current + 1, state.current + 4)
    .filter((item) => item.kind === "video")
    .forEach((item) => {
      fetch(`/api/prepare?f=${encodeURIComponent(item.relpath)}&priority=1`)
        .catch(() => undefined);
    });
}

function wholeVideo() {
  const span = mediaSpan();
  return { start: 0, end: span || null, name: "", saved: false, untouched: true };
}

/** Start another clip; `complain` says so when there is no room left for one. */
function addClip(complain = true) {
  remember();
  const span = mediaSpan();
  const ends = state.clips.map((clip) => clip.end ?? clip.start ?? 0);
  const lastEnd = ends.length ? Math.max(...ends) : 0;
  if (!state.clips.length || !span) {
    state.clips.push(wholeVideo());
    state.active = state.clips.length - 1;
  } else if (span - lastEnd >= MIN_CLIP) {
    state.clips.push({ start: lastEnd, end: span, name: "", saved: false });
    state.active = state.clips.length - 1;
  } else if (!splitLastClip()) {
    state.history.pop();
    if (complain) toast("no room for another clip — drag a handle in first", true);
    return;
  }
  state.marker = null;
  nameBox.value = activeClip().name;
  drawTimeline();
  updateSaveBar();
  playActiveClip();
}

function deleteClip() {
  const clip = activeClip();
  if (!clip) return;
  if (state.clips.length <= 1) {
    toast("a video keeps at least one clip", true);
    return;
  }
  if (clip.saved) {
    toast("that clip is already saved — it lives in filtered/", true);
    return;
  }
  remember();
  state.clips.splice(state.active, 1);
  state.active = Math.max(0, state.active - 1);
  state.marker = null;
  nameBox.value = activeClip().name;
  drawTimeline();
  updateSaveBar();
  playActiveClip();
}

function playActiveClip() {
  const clip = activeClip();
  if (!clip || clip.start === null || !player.duration) return;
  player.currentTime = clip.start;
  player.play().catch(() => undefined);
}

function splitLastClip() {
  const index = state.clips.reduce((found, clip, at) =>
    !clip.saved && (clip.end ?? 0) - (clip.start ?? 0) >= MIN_CLIP * 2 ? at : found, -1);
  if (index < 0) return false;
  const clip = state.clips[index];
  const middle = (clip.start + clip.end) / 2;
  state.clips.splice(index + 1, 0,
                     { start: middle, end: clip.end, name: "", saved: false });
  clip.end = middle;
  clip.untouched = false;
  state.active = index + 1;
  return true;
}

function selectClip(index) {
  state.active = index;
  state.marker = null;
  nameBox.value = state.clips[index].name;
  drawTimeline();
  updateSaveBar();
  playActiveClip();
}

function drawTimeline() {
  const window = view();
  const span = window.span;
  const bands = element("bands");
  bands.replaceChildren();
  state.clips.forEach((clip, index) => {
    if (clip.start === null || !span) return;
    const end = clip.end === null ? clip.start : clip.end;
    const band = document.createElement("div");
    band.className = "band";
    band.style.left = `${acrossBar(clip.start) * 100}%`;
    band.style.width = `${Math.max(((end - clip.start) / window.width) * 100, 0.4)}%`;
    if (clip.saved) band.dataset.saved = "1";
    if (index === state.active) band.dataset.active = "1";
    band.dataset.index = String(index);
    band.onpointerdown = (event) => {
      // A click inside the clip already being worked on scrubs, as the bare
      // track does; a click on any other clip picks that one up instead.
      if (index === state.active) return;
      event.stopPropagation();
      selectClip(index);
    };
    if (clip.name) {
      const tag = document.createElement("span");
      tag.className = "tag";
      tag.textContent = clip.name;
      band.append(tag);
    }
    bands.append(band);
  });
  const clip = activeClip();
  if (clip && span) {
    for (const edge of ["start", "end"]) {
      if (clip[edge] === null) continue;
      const handle = document.createElement("div");
      handle.className = "handle";
      handle.style.left = `${acrossBar(clip[edge]) * 100}%`;
      handle.dataset.edge = edge;
      if (state.marker === edge) handle.dataset.selected = "1";
      handle.onpointerdown = (event) => startDrag(event, edge);
      bands.append(handle);
    }
  }
  renderChips();
  element("loop-button").setAttribute("aria-pressed", String(state.looping));
  const summary = clip && clip.start !== null
    ? (clip.end !== null
        ? `in ${stamp(clip.start)} → out ${stamp(clip.end)} · ` +
          `${(clip.end - clip.start).toFixed(1)}s`
        : `in ${stamp(clip.start)}`)
    : "";
  element("selected").textContent =
    summary + (state.marker ? `  ·  ${state.marker === "start" ? "IN" : "OUT"} selected` : "");
  const zoomed = state.zoom > 1 ? `  ·  zoom ${state.zoom.toFixed(1)}×` : "";
  element("hint").textContent = (clip
    ? (clip.untouched ? "drag the IN and OUT handles, or press i / o at the playhead"
                      : (state.marker ? "arrows move it 1 s · shift+arrows one frame"
                                      : "click a handle to nudge it with the arrows"))
    : "") + zoomed;
}

function renderChips() {
  const row = element("clips");
  row.replaceChildren();
  state.clips.forEach((clip, index) => {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "chip";
    chip.setAttribute("aria-current", String(index === state.active));
    if (clip.saved) chip.dataset.saved = "1";
    const range = clip.start === null ? "new clip"
      : `${short(clip.start)}–${clip.end === null ? "?" : short(clip.end)}`;
    chip.textContent = `${index + 1}. ${clip.name || range}${clip.saved ? " ✓" : ""}`;
    if (clip.saved && currentItem().saves[clip.saveIndex].starred) {
      const mark = document.createElement("span");
      mark.className = "pk";
      chip.append(mark);
    }
    chip.onclick = () => { chip.blur(); selectClip(index); };
    if (clip.saved) chip.ondblclick = () => nameBox.select();
    row.append(chip);
  });
  const add = document.createElement("button");
  add.type = "button";
  add.className = "chip add";
  add.textContent = "+ clip (a)";
  add.onclick = () => { add.blur(); addClip(); };
  row.append(add);
}

function markIn() {
  remember();
  const clip = activeClip();
  if (!clip || !player.duration) return;
  clip.start = player.currentTime;
  clip.untouched = false;
  if (clip.end !== null && clip.end <= clip.start) clip.end = null;
  state.marker = "start";
  drawTimeline();
  updateSaveBar();
}

function markOut() {
  remember();
  const clip = activeClip();
  if (!clip || !player.duration) return;
  if (clip.start === null) { toast("mark the in point first (i)", true); return; }
  if (player.currentTime <= clip.start) {
    toast("the out point must come after the in point", true);
    return;
  }
  clip.end = player.currentTime;
  clip.untouched = false;
  state.marker = "end";
  drawTimeline();
  updateSaveBar();
}

function clearMarks() {
  remember();
  const clip = activeClip();
  if (!clip) return;
  Object.assign(clip, wholeVideo(), { name: clip.name });
  state.marker = null;
  state.looping = false;
  element("loop-button").setAttribute("aria-pressed", "false");
  drawTimeline();
  updateSaveBar();
}

function moveMarker(delta) {
  remember();
  const clip = activeClip();
  if (!clip || !state.marker || clip[state.marker] === null) return;
  const span = player.duration || 0;
  const other = state.marker === "start" ? clip.end : clip.start;
  let moved = clip[state.marker] + delta;
  moved = Math.min(Math.max(moved, 0), span);
  if (other !== null) {
    moved = state.marker === "start"
      ? Math.min(moved, other - frame())
      : Math.max(moved, clip.start + frame());
  }
  clip[state.marker] = moved;
  clip.untouched = false;
  player.currentTime = moved;
  drawTimeline();
}

function startDrag(event, edge) {
  event.preventDefault();
  event.stopPropagation();
  const clip = activeClip();
  if (!clip) return;
  remember();
  state.marker = edge;
  state.dragging = true;
  const move = (moved) => {
    let at = atFraction(barFraction(moved));
    const other = edge === "start" ? clip.end : clip.start;
    if (other !== null) {
      at = edge === "start" ? Math.min(at, other - frame())
                            : Math.max(at, other + frame());
    }
    clip[edge] = at;
    clip.untouched = false;
    player.currentTime = at;
    drawTimeline();
  };
  const release = () => {
    window.removeEventListener("pointermove", move);
    window.removeEventListener("pointerup", release);
    state.dragging = false;
    updateSaveBar();
    playActiveClip();
  };
  window.addEventListener("pointermove", move);
  window.addEventListener("pointerup", release);
  move(event);
}

function saveable() {
  const item = currentItem();
  if (!item || !nameBox.value.trim()) return false;
  if (item.kind === "photo") return !item.saves.length;
  const clip = activeClip();
  return Boolean(clip && !clip.saved && clip.start !== null && clip.end !== null);
}

/** What re-saving the selected keeper would change, or null when it is as saved. */
function keeperChanges() {
  const keeper = currentKeeper();
  const to = nameBox.value.trim();
  if (!keeper || !to) return null;
  const clip = activeClip();
  const moved = Boolean(clip?.saved)
    && (clip.start !== keeper.start || clip.end !== keeper.end);
  if (moved && (clip.start === null || clip.end === null)) return null;
  if (moved) return { to, start: clip.start, end: clip.end };
  return to !== keeper.name ? { to } : null;
}

function updateSaveBar() {
  const keeper = currentKeeper();
  element("save-button").disabled = !saveable() && !keeperChanges();
  element("save-button").textContent =
    keeper ? (currentItem().kind === "video" ? "Re-save" : "Rename") : "Save";
  const clip = activeClip();
  if (clip && !clip.saved) clip.name = nameBox.value.trim();
  element("star-button").toggleAttribute("data-unsaved", !keeper);
  element("star-button").setAttribute("aria-pressed", String(Boolean(keeper?.starred)));
}

/** The saved keeper rename and star act on: the active clip, or this photo's copy. */
function currentKeeper() {
  const item = currentItem();
  if (!item) return null;
  if (item.kind === "photo") return item.saves[0] || null;
  const clip = activeClip();
  return clip?.saved ? item.saves[clip.saveIndex] : null;
}

/** Apply `changes` to the selected keeper: a new name, new marks, or both. */
async function resave(changes) {
  const keeper = currentKeeper();
  element("save-button").disabled = true;
  const body = await postJSON("/api/update",
                              { relpath: currentItem().relpath, name: keeper.name, ...changes });
  if (!body) {
    updateSaveBar();
    return;
  }
  keeper.name = body.name;
  if ("start" in changes) {
    keeper.start = changes.start;
    keeper.end = changes.end;
  }
  if (activeClip()?.saved) activeClip().name = body.name;
  nameBox.value = body.name;
  toast(`re-saved ${body.saved}`);
  renderStrip();
  drawTimeline();
  updateSaveBar();
}

async function toggleStar() {
  const keeper = currentKeeper();
  if (!keeper) {
    toast("save it first, then star it", true);
    return;
  }
  const body = await postJSON("/api/star",
                              { relpath: currentItem().relpath, name: keeper.name });
  if (!body) return;
  keeper.starred = body.starred;
  toast(body.starred ? `starred: ${body.saved}` : `unstarred: ${body.saved}`);
  drawTimeline();
  updateSaveBar();
}

async function exportFrame() {
  const item = currentItem();
  if (!item || item.kind !== "video") return;
  player.pause();
  const body = await postJSON("/api/frame",
                              { relpath: item.relpath, at: player.currentTime });
  if (body) toast(`frame saved to ${body.saved}`);
}

async function save(event) {
  event.preventDefault();
  const changes = keeperChanges();
  if (changes) {
    resave(changes);
    return;
  }
  if (!saveable()) return;
  const item = currentItem();
  const clip = activeClip();
  const payload = { relpath: item.relpath, name: nameBox.value.trim() };
  if (item.kind === "video") {
    payload.start = clip.start;
    payload.end = clip.end;
  }
  element("save-button").disabled = true;
  const body = await postJSON("/api/save", payload);
  if (!body) {
    updateSaveBar();
    return;
  }
  item.saves.push({ name: body.name, start: payload.start, end: payload.end,
                    starred: false });
  toast(`saved ${body.saved}`);
  renderStrip();
  if (item.kind === "photo") {
    step(1);
    return;
  }
  clip.saved = true;
  clip.name = body.name;
  clip.saveIndex = item.saves.length - 1;
  addClip(false);
  nameBox.value = activeClip().name;
  drawTimeline();
  updateSaveBar();
}

function step(delta) {
  select(state.current + delta);
}

function seek(delta) {
  if (!player.duration) return;
  player.currentTime = Math.min(Math.max(player.currentTime + delta, 0),
                                player.duration);
}

function toggleFullscreen() {
  if (document.fullscreenElement) document.exitFullscreen();
  else element("screen").requestFullscreen().catch(() =>
    toast("fullscreen refused", true));
}

function toggleLoop() {
  state.looping = !state.looping;
  element("loop-button").setAttribute("aria-pressed", String(state.looping));
  if (state.looping) playActiveClip();
}

function onKey(event) {
  if (event.target.matches("input, textarea")) {
    if (event.key === "Escape") event.target.blur();
    return;
  }
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "z") {
    event.preventDefault();
    undo();
    return;
  }
  if ((event.ctrlKey || event.metaKey)
      && (event.key === "ArrowRight" || event.key === "ArrowLeft")) {
    event.preventDefault();
    changeRate(event.key === "ArrowRight" ? 1 : -1);
    return;
  }
  if (event.ctrlKey || event.altKey || event.metaKey) return;
  const pan = PAN_DIRECTIONS[event.key];
  if (pan && state.media.scale > 1 && !state.marker && !event.shiftKey) {
    event.preventDefault();
    panMedia(pan[0] * PAN_STEP, pan[1] * PAN_STEP);
    return;
  }
  const item = currentItem();
  const video = item && item.kind === "video";
  // Shift always means one frame: of the selected marker, or of the playhead.
  const sideways = (delta) => {
    if (!video) {
      step(delta);
    } else if (state.marker) {
      moveMarker(delta * (event.shiftKey ? frame() : NUDGE_STEP));
    } else if (event.shiftKey) {
      player.pause();
      seek(delta * frame());
    } else {
      seek(delta * SEEK_STEP);
    }
  };
  const actions = {
    j: () => step(1),
    k: () => step(-1),
    "]": () => step(1),
    "[": () => step(-1),
    ArrowRight: () => sideways(1),
    ArrowLeft: () => sideways(-1),
    ArrowDown: () => step(1),
    ArrowUp: () => step(-1),
    " ": () => { if (video) player.paused ? player.play() : player.pause(); },
    ",": () => { player.pause(); seek(-frame()); },
    ".": () => { player.pause(); seek(frame()); },
    i: markIn,
    o: markOut,
    a: () => { if (video) addClip(); },
    Delete: () => { if (video) deleteClip(); },
    x: clearMarks,
    l: toggleLoop,
    f: toggleFullscreen,
    n: () => nameBox.focus(),
    r: () => nameBox.select(),
    s: toggleStar,
    e: () => { if (video) exportFrame(); },
    Escape: () => { state.marker = null; drawTimeline(); },
    0: resetMediaZoom,
    1: () => setRate(RATES[0]),
    2: () => setRate(RATES[1]),
    3: () => setRate(RATES[2]),
    4: () => setRate(RATES[3]),
    "?": () => element("help").showModal(),
  };
  const action = actions[event.key];
  if (!action) return;
  event.preventDefault();
  action();
}

function onMetadata() {
  player.playbackRate = state.rate;
  for (const clip of state.clips) {
    if (clip.untouched) {
      clip.start = 0;
      clip.end = mediaSpan();
    }
  }
  drawTimeline();
}

function onEnded() {
  if (state.looping) playActiveClip();
  else step(1);
}

function onTimeUpdate() {
  const span = player.duration || 0;
  element("clock").textContent = stamp(player.currentTime);
  element("played").style.left = span ? `${acrossBar(player.currentTime) * 100}%` : "0";
  keepPlayheadInView();
  const clip = activeClip();
  if (state.looping && !state.dragging && clip && clip.end !== null
      && player.currentTime >= clip.end) {
    player.currentTime = clip.start;
  }
}

function barFraction(event) {
  const box = element("track").getBoundingClientRect();
  return Math.min(Math.max((event.clientX - box.left) / box.width, 0), 1);
}

function scrub(event) {
  if (player.duration) player.currentTime = atFraction(barFraction(event));
}

function setRate(rate) {
  state.rate = rate;
  player.playbackRate = rate;
  renderRates();
}

/** Step through the speeds, slower to the left and faster to the right. */
function changeRate(direction) {
  const at = RATES.indexOf(state.rate);
  const from = at < 0 ? RATES.indexOf(1) : at;
  const next = Math.min(Math.max(from + direction, 0), RATES.length - 1);
  setRate(RATES[next]);
  toast(`${RATES[next]}×`);
}

function renderRates() {
  const row = element("rates");
  row.replaceChildren();
  for (const rate of RATES) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = `${rate}×`;
    button.setAttribute("aria-pressed", String(rate === state.rate));
    button.onclick = () => { button.blur(); setRate(rate); };
    row.append(button);
  }
}

function press(id, action) {
  element(id).onclick = (event) => {
    event.currentTarget.blur();
    action();
  };
}

function wire() {
  element("save-bar").onsubmit = save;
  nameBox.oninput = updateSaveBar;
  element("hide-saved").onchange = () => applyFilter(state.current);
  element("help-button").onclick = () => element("help").showModal();
  press("in-button", markIn);
  press("out-button", markOut);
  press("clear-button", clearMarks);
  press("frame-button", exportFrame);
  press("star-button", toggleStar);
  press("loop-button", toggleLoop);
  press("fullscreen-button", toggleFullscreen);
  element("screen").ondblclick = toggleFullscreen;
  element("screen").addEventListener("wheel", (event) => {
    event.preventDefault();
    zoomMedia(event.deltaY < 0 ? 1.2 : 1 / 1.2, event.clientX, event.clientY);
  }, { passive: false });
  element("screen").onpointerdown = (event) => {
    if (state.media.scale === 1) return;
    event.preventDefault();
    element("screen").setAttribute("data-panning", "1");
    let last = { x: event.clientX, y: event.clientY };
    const move = (moved) => {
      panMedia(moved.clientX - last.x, moved.clientY - last.y);
      last = { x: moved.clientX, y: moved.clientY };
    };
    const release = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", release);
      element("screen").removeAttribute("data-panning");
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", release);
  };
  element("track").addEventListener("wheel", (event) => {
    event.preventDefault();
    zoomBy(event.deltaY < 0 ? 1.25 : 1 / 1.25, barFraction(event));
  }, { passive: false });
  element("track").onpointerdown = (event) => {
    if (event.target.classList.contains("handle")) return;
    state.marker = null;
    scrub(event);
    const move = (moved) => scrub(moved);
    const release = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", release);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", release);
    drawTimeline();
  };
  player.ontimeupdate = onTimeUpdate;
  player.onloadedmetadata = onMetadata;
  player.onended = onEnded;
  document.addEventListener("keydown", onKey);
  renderRates();
}

wire();
load();
