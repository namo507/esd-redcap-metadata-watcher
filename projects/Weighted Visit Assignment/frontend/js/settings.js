/* ESD Visitboard - settings.js

   The knobs the lab may turn, as dropdowns.

   Three things this file deliberately does not do:

   - It does not know the name of a single setting. The catalogue arrives from
     /api/settings carrying its own labels, help text and allowed values, so
     adding a knob is a backend change and this file is untouched. The same
     rule the read table's person dropdown follows.
   - It does not patch its own controls after a change. It redraws from the
     board's answer, because setting one weight moves the other three, and a
     page that updated only the control that was touched would display a set
     of numbers that never existed.
   - It does not show everything at once. Groups start closed. A scheduler
     opening this looking for one number should see three headings, not forty
     controls.
*/

/* -----------------------------------------------------------------------
   SCREEN F  --  CHANGE THE NUMBERS
  
     before  /api/settings listed every knob and its allowed values
     here    the tuning dropdowns. This file knows no setting's name --
             the catalogue carries its own labels, so a knob added on the
             server appears here with no change
     after   a change redraws every section from the board's answer
  
     worked example
       groups start closed: somebody after one number should meet three
         headings, not eighteen controls
       setting a weight rescales the other three, so the page redraws from
         the response rather than patching the control that was touched
   ----------------------------------------------------------------------- */

function drawSettings() {
  const card = $("settings-card");
  if (!card) return;
  api("/api/settings").then((data) => {
    card.hidden = false;
    renderSettings(data);
  }).catch(() => {
    /* The published snapshot has no backend to write to. Saying so is better
       than presenting controls that would silently do nothing. */
    card.hidden = !STATIC;
    if (STATIC) {
      $("settings-groups").innerHTML =
        `<p class="note">This is a published snapshot, so the numbers below it
         was built with are fixed. Run the board locally to change them.</p>`;
      const v = $("settings-vector");
      if (v) v.textContent = "";
    }
  });
}

function renderSettings(data) {
  const knobs = data.knobs || [];
  const vector = $("settings-vector");
  if (vector) vector.textContent = data.weight_vector_id || "";

  $("settings-groups").innerHTML = (data.groups || []).map((g) => {
    const rows = knobs.filter((k) => k.group === g.id);
    if (!rows.length) return "";
    return `
      <details class="tweakgroup">
        <summary><b>${esc(g.title)}</b><span class="note">${esc(g.note)}</span></summary>
        <div class="tweakrows">
          ${rows.map(tweakRow).join("")}
        </div>
      </details>`;
  }).join("");

  document.querySelectorAll(".tweak").forEach((sel) => {
    sel.onchange = () => applySetting(sel);
  });
}

function tweakRow(k) {
  /* Values ride as JSON in the option's value attribute, so a number stays a
     number and true stays a boolean. Reading them back as strings is how "2"
     ends up in a config file that wants 2. */
  return `
    <label class="tweakrow">
      <span class="tweaklabel">
        <b>${esc(k.label)}</b>${k.alias ? `<span class="alias">${esc(k.alias)}</span>` : ""}
        <span class="note">${esc(k.help || "")}</span>
      </span>
      <select class="input select tweak" data-key="${esc(k.key)}">
        ${(k.options || []).map((o) => `
          <option value='${esc(JSON.stringify(o.value))}'
            ${sameValue(o.value, k.value) ? "selected" : ""}>${esc(o.label)}</option>`).join("")}
      </select>
    </label>`;
}

function sameValue(a, b) {
  if (typeof a === "number" && typeof b === "number") return Math.abs(a - b) < 1e-9;
  return a === b;
}

async function applySetting(sel) {
  const key = sel.dataset.key;
  const value = JSON.parse(sel.value);
  sel.disabled = true;
  try {
    const out = await api("/api/settings", {
      method: "POST",
      body: JSON.stringify({ key, value }),
    });
    renderSettings(out.settings);
    await refresh();
    redrawEverything();
    toast(describeChange(key, out.changed));
  } catch (err) {
    toast(err.message, true);
    drawSettings();                 // put the control back to what is in force
  } finally {
    sel.disabled = false;
  }
}

function describeChange(key, changed) {
  /* Setting a weight moves the other three, and the toast says so. A change
     that quietly altered three numbers the scheduler did not touch would be
     the board doing something behind their back. */
  const keys = Object.keys(changed || {});
  const others = keys.filter((k) => k !== key).length;
  return others
    ? `Updated. ${others} other weight${others === 1 ? "" : "s"} rescaled so they still sum to 1.`
    : "Updated. Every section below now uses it.";
}
