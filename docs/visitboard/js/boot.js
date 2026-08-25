/* ESD Visitboard - boot.js

   Start-up and the once-a-minute refresh. Loaded last.
*/

const REFRESH_MS = 60000;

let refreshTimer = null;

async function tick() {
  if (document.hidden) return;               // no point polling a hidden tab
  try {
    const y = window.scrollY;
    await refresh();
    redrawCurrent();
    window.scrollTo(0, y);
  } catch (err) {
    /* A refresh that fails is not worth interrupting anyone over; the next
       one will either succeed or the board will already look wrong. */
  }
}

function redrawCurrent() {
  drawKpis();
  drawSyncBadge();
  drawQueue();
  if (S.section === "team") { drawTeam(); drawDue(); }
  if (S.section === "sync") { drawSync(); drawData(); }
  if (S.section === "logic") { drawLogic(); drawSettings(); }
}

function startAutoRefresh() {
  if (STATIC) return;         // a frozen snapshot has nothing new to fetch
  if (refreshTimer) clearInterval(refreshTimer);
  refreshTimer = setInterval(tick, REFRESH_MS);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) tick();
  });
}

async function refresh() {
  S.board = await api("/api/board");
  drawKpis(); drawQueue(); drawFooter(); drawSyncBadge(); drawModeNote();
  if (S.section === "team") drawTeam();
}

async function detectMode() {
  // A backend answering /api/health means the live board; anything else means
  // the static build. Deciding by probe rather than by build flag keeps one
  // copy of the frontend for both.
  try {
    const res = await fetch(apiUrl("/api/health"), { cache: "no-store" });
    if (res.ok) return false;
  } catch (err) { /* no backend here */ }
  await window.StaticBoard.load();
  return true;
}

async function boot() {
  STATIC = await detectMode();
  if (STATIC) document.body.classList.add("is-static");
  $("filter-search").addEventListener("input", (e) => { S.search = e.target.value; drawQueue(); });
  $("filter-status").querySelectorAll(".chip").forEach((chip) =>
    chip.addEventListener("click", () => {
      S.status = chip.dataset.status;
      $("filter-status").querySelectorAll(".chip").forEach((c) => c.classList.remove("is-on"));
      chip.classList.add("is-on");
      drawQueue();
    }));
  document.querySelectorAll(".navbtn").forEach((b) =>
    b.addEventListener("click", () => setSection(b.dataset.section)));
  const addForm = $("add-visit-form");
  if (addForm) addForm.addEventListener("submit", (e) => {
    e.preventDefault();
    addVisit(addForm);
  });
  $("btn-reset").addEventListener("click", async () => {
    if (!confirm("Reset the board? This clears the assignments made in this session.")) return;
    await api("/api/reset", { method: "POST" });
    S.assignments = {};
    await refresh();
    const first = S.board.queue[0];
    if (first) await selectVisit(first.id);
    toast("Board reset.");
  });

  watchScroll();
  startAutoRefresh();
  window.addEventListener("popstate", () => { applyRoute(); });
  // Safari fires hashchange without popstate for a programmatic hash write.
  window.addEventListener("hashchange", () => { applyRoute(); });

  try {
    await refresh();
    const route = parseRoute();
    const known = S.board.queue.some((v) => v.id === route.visitId);
    const firstOpen = S.board.queue.find((v) => v.status !== "assigned")
      || S.board.queue[0];
    // A link to a visit that no longer exists should land somewhere sensible
    // rather than on an error, so fall back to the first open one.
    const target = known ? route.visitId : (firstOpen && firstOpen.id);
    if (target) await selectVisit(target, { silent: true });
    setSection(route.section, { fromRoute: true });
    syncRoute(true);
    if (route.visitId && !known) {
      toast(`Visit ${route.visitId} is not on this board.`, true);
    }
  } catch (err) {
    $("detail").innerHTML = `<div class="card empty">Could not reach the board.<br>
      <span class="note">${esc(err.message)}</span></div>`;
  }
}

document.addEventListener("DOMContentLoaded", boot);
