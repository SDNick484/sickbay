// Entry point: hash routing and the shared header. Each screen module exports `render(ctx)` with
// ctx = { view, parts, query, alive, rerender }: `view` is a fresh element for this visit, `alive()` is false once
// the user has moved on, and `rerender()` draws the same route again.
import { el, errorNotice, loadMe, parseHash, state } from "./core.js";

// One line per route, keyed by the first path segment; the screen reads the rest from ctx.parts. Tasks 11 and 12 add
// their lines here (summary, settings, import).
const SCREENS = {
  "": () => import("./home.js"),
  event: () => import("./event.js"),
};

const host = typeof document === "undefined" ? null : document.getElementById("app");
let visit = 0;

export async function route() {
  const mine = ++visit;
  const { parts, query } = parseHash(location.hash);
  const view = el("div", { class: "view" });
  host.replaceChildren(view);
  const ctx = { view, parts, query, alive: () => visit === mine && view.isConnected, rerender: route };
  const load = SCREENS[parts[0] ?? ""];
  if (!load) {
    view.append(el("div", { class: "notice" }, "There is nothing at this address. ", el("a", { href: "#/" }, "Home")));
    return;
  }
  try {
    const screen = await load();
    if (ctx.alive()) await screen.render(ctx);
  } catch {
    if (ctx.alive()) view.replaceChildren(errorNotice("Something went wrong drawing this page. Reload and try again."));
  }
  if (ctx.alive()) window.scrollTo(0, 0);
}

async function start() {
  const me = await loadMe();
  if (me.error) {
    host.replaceChildren(errorNotice(me.error));
    return;
  }
  document.title = state.me.app_name; // the only place the title is set: APP_NAME, never anything from a record
  document.getElementById("appName").textContent = state.me.app_name;
  window.addEventListener("hashchange", route);
  route();
}

if (host) start();
