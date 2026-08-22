/* ESD Visitboard - where the API lives.

   The page and the API are on different hosts once this is deployed: the page
   is on GitHub Pages, the engine is on an app host. Everything else in the
   frontend asks this file rather than assuming same-origin, so moving the
   backend is a one-line edit here and nothing else changes.

   Leave API_BASE empty for local use. `make serve` puts the page and the API
   on the same port, and an empty base means "same origin", which is what it
   has always done.

   After the first backend deploy, put its URL here, with no trailing slash:

       window.ESD_CONFIG = { API_BASE: "https://esd-visitboard.fly.dev" };

   If the API cannot be reached at all, the page still opens: it falls back to
   the frozen demo snapshot in static-board.js, exactly as before.
*/
window.ESD_CONFIG = {
  API_BASE: "",
};
