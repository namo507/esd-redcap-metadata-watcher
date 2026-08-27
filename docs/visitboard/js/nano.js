/* ESD Visitboard - nano.js

   The NANO study's own screen: pick a family, pick a time point, get a pair.

   The rest of the board starts from a visit that already exists. This starts
   from the study: two hundred participants, each with eight checkpoints whose
   windows are worked out from their anchor date, and the question a
   coordinator actually arrives with is "who takes 5901's nine month visit".

   Two dropdowns and a button, in that order, because that is the order the
   question is asked in. The second dropdown cannot be filled until the first
   is answered -- a time point means nothing without a family -- so it stays
   disabled rather than showing a list that would be wrong.

   The prediction is not computed here. It is the same engine and the same
   mindmap the assign screen uses; this only chooses the window and hands it
   over. A second scoring path would drift from the first, and then two
   screens would disagree about who should take a visit.
*/

/* -----------------------------------------------------------------------
   SCREEN C  --  PICK A PARTICIPANT
  
     before  /api/nano/families answered with the study's participants
     here    two dropdowns in the order the question is asked: which
             family, then which of that family's eight time points
     after   assign.js shows the decision for the window that was chosen
  
     worked example
       filter chips: window closed 124, open now 36, not open yet 44
       the time-point dropdown stays disabled until a family is chosen:
         a time point means nothing without one
       'Who should take it?' -> POST /api/nano/plan -> the assign screen
   ----------------------------------------------------------------------- */

const NANO_STATE_ORDER = ["missed", "open", "upcoming", "done"];

function drawNano() {
  const card = $("nano-card");
  if (!card) return;
  api("/api/nano/families").then((data) => {
    S.nano = S.nano || {};
    S.nano.summary = data;
    renderNanoPickers(data);
  }).catch((err) => {
    $("nano-status").innerHTML =
      `<div class="notice notice-warn"><span>&#9432;</span><span>${
        esc(err.message)}</span></div>`;
  });
}

function renderNanoPickers(data) {
  const families = data.families || [];
  if (!data.fetched_at) {
    $("nano-status").innerHTML = `
      <div class="notice notice-warn"><span>&#9432;</span><span>
        No study export on file yet. Run <b>make redcap-sync</b> and reload.
        ${esc(data.reason || "")}</span></div>`;
    $("nano-pickers").innerHTML = "";
    return;
  }

  const counts = data.counts_by_state || {};
  const filter = (S.nano && S.nano.filter) || "all";
  /* The counts are the filter. A list of two hundred participants in id order
     is a list nobody can act on; "124 windows have closed" is where somebody
     starts, and clicking it is the obvious next move. */
  const chips = ["all", ...NANO_STATE_ORDER.filter((s) => counts[s])]
    .map((state) => `
      <button class="chip${filter === state ? " is-on" : ""}" type="button"
              data-nano-filter="${esc(state)}">
        ${state === "all" ? `All ${data.count}` : `${esc(NANO_LABEL[state] || state)} ${counts[state]}`}
      </button>`).join("");

  $("nano-status").innerHTML = `
    <div class="chipbar" role="group" aria-label="Filter families">${chips}</div>
    <p class="note">${data.count} participants, synced ${
      esc((data.fetched_at || "").replace("T", " "))}. Windows are worked out
      from each family's anchor date and the protocol schedule, not stored in
      the study record.</p>`;

  const shown = families.filter((f) => filter === "all" || f.state === filter);
  const chosen = (S.nano && S.nano.family) || "";

  $("nano-pickers").innerHTML = `
    <div class="nanopick">
      <label class="tweakrow">
        <span class="tweaklabel"><b>Family</b>
          <span class="note">${shown.length} shown${
            filter === "all" ? "" : `, filtered to ${esc(NANO_LABEL[filter] || filter)}`}</span></span>
        <select class="input select" id="nano-family">
          <option value="">Choose a participant&hellip;</option>
          ${shown.map((f) => `
            <option value="${esc(f.family_id)}" ${f.family_id === chosen ? "selected" : ""}>
              ${esc(f.family_id)} &middot; ${esc(f.participant_status)}${
                f.next ? ` &middot; ${esc(f.next.checkpoint)} ${esc(f.state_label)}` : ""}
            </option>`).join("")}
        </select>
      </label>
      <label class="tweakrow">
        <span class="tweaklabel"><b>Time point</b>
          <span class="note">Which of this family's eight NANO checkpoints</span></span>
        <select class="input select" id="nano-checkpoint" disabled>
          <option value="">Pick a family first</option>
        </select>
      </label>
    </div>
    <div id="nano-window"></div>`;

  document.querySelectorAll("[data-nano-filter]").forEach((b) =>
    b.addEventListener("click", () => {
      S.nano.filter = b.dataset.nanoFilter;
      S.nano.family = "";
      renderNanoPickers(S.nano.summary);
    }));
  $("nano-family").addEventListener("change", (e) => chooseNanoFamily(e.target.value));
  if (chosen) chooseNanoFamily(chosen);
}

const NANO_LABEL = {
  missed: "Window closed", open: "Open now",
  upcoming: "Not open yet", done: "Done",
};

async function chooseNanoFamily(familyId) {
  S.nano = S.nano || {};
  S.nano.family = familyId;
  const picker = $("nano-checkpoint");
  if (!familyId) {
    picker.disabled = true;
    picker.innerHTML = `<option value="">Pick a family first</option>`;
    $("nano-window").innerHTML = "";
    return;
  }
  const data = await api("/api/nano/family?id=" + encodeURIComponent(familyId));
  S.nano.windows = data;
  const windows = data.windows || [];
  picker.disabled = false;
  picker.innerHTML = `<option value="">Choose a time point&hellip;</option>` +
    windows.map((w) => `
      <option value="${esc(w.checkpoint)}" ${w.selectable ? "" : "disabled"}>
        ${esc(w.checkpoint)} &middot; ${esc(w.state_label)}${
          w.window_start ? ` &middot; ${esc(w.window_start)} to ${esc(w.window_end)}` : ""}
      </option>`).join("");
  picker.onchange = (e) => chooseNanoWindow(e.target.value);
  $("nano-window").innerHTML = `
    <p class="note">${esc(familyId)} is <b>${esc(data.participant_status)}</b>.
      ${data.completed.length
        ? `Completed: ${data.completed.map(esc).join(", ")}.`
        : "No checkpoint recorded as done yet."}
      A preterm participant's windows up to 24m count from the due date; every
      36m visit counts from the birthday.</p>`;
}

async function chooseNanoWindow(checkpoint) {
  if (!checkpoint) { $("nano-window").innerHTML = ""; return; }
  const familyId = S.nano.family;
  const window_ = (S.nano.windows.windows || [])
    .find((w) => w.checkpoint === checkpoint);
  $("nano-window").innerHTML = `
    <div class="nanowindow">
      <div>
        <p class="eyebrow">${esc(familyId)} &middot; ${esc(checkpoint)}</p>
        <h3>${esc(window_.window_start)} to ${esc(window_.window_end)}</h3>
        <p class="note">Ideal date ${esc(window_.ideal)} &middot; ${
          esc(window_.state_label)}${window_.remote
            ? " &middot; remote checkpoint, nobody travels" : ""}</p>
      </div>
      <button class="btn btn-primary" type="button" id="nano-go">
        Who should take it?</button>
    </div>
    <div id="nano-result"></div>`;
  $("nano-go").addEventListener("click", () => planNanoVisit(familyId, checkpoint));
}

async function planNanoVisit(familyId, checkpoint) {
  const button = $("nano-go");
  if (button) { button.disabled = true; button.textContent = "Working it out…"; }
  try {
    const out = await api("/api/nano/plan", {
      method: "POST",
      body: JSON.stringify({ family_id: familyId, checkpoint }),
    });
    /* Hand over to the assign screen rather than drawing a second tree here.

       Rendering the mindmap in this section too put two of them in the
       document with the same element ids, so clicking a branch bound to
       whichever the browser found first and the other went stale. One tree,
       one place: this screen's job is choosing the window, and the decision
       has a screen already. */
    S.selected = out.visit_id;
    S.detail = out.detail;
    resetMindmap();
    await refresh();
    setSection("assign");
    await selectVisit(out.visit_id);
    toast(`${familyId} ${checkpoint} is on the board.`);
  } catch (err) {
    $("nano-result").innerHTML =
      `<div class="notice notice-alert"><span>&#9888;</span><span>${
        esc(err.message)}</span></div>`;
  } finally {
    if (button) { button.disabled = false; button.textContent = "Who should take it?"; }
  }
}
