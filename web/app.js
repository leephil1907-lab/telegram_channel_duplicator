const $ = (id) => document.getElementById(id);
const notice = $("notice");
function message(text, kind = "") { notice.textContent = text; notice.className = "notice " + kind; }
function setLoading(loading) {
  document.body.classList.toggle("is-loading", loading);
  if (loading) {
    $("status").textContent = "Loading";
    $("route-count").textContent = "…";
    $("delay").textContent = "…";
  }
}
function renderRoutes(groups) {
  const host = $("routes");
  host.replaceChildren();
  if (!groups || !groups.length) {
    const empty = document.createElement("div"); empty.className = "empty";
    empty.textContent = "No route groups are configured yet. Add at least one source and destination in config.yaml.";
    host.append(empty); return;
  }
  groups.forEach((group, index) => {
    const card = document.createElement("article"); card.className = "route";
    const top = document.createElement("div"); top.className = "route-top";
    const title = document.createElement("h3"); title.textContent = group.name;
    const num = document.createElement("span"); num.className = "route-index"; num.textContent = String(index + 1).padStart(2, "0");
    top.append(title, num);
    const flow = document.createElement("div"); flow.className = "route-flow";
    const source = document.createElement("div"); source.className = "route-box";
    const sourceLabel = document.createElement("span"); sourceLabel.textContent = "SOURCES";
    const sourceCount = document.createElement("strong"); sourceCount.textContent = group.sources + (group.sources === 1 ? " channel" : " channels");
    source.append(sourceLabel, sourceCount);
    const arrow = document.createElement("div"); arrow.className = "route-arrow"; arrow.textContent = "→";
    const destination = document.createElement("div"); destination.className = "route-box";
    const destLabel = document.createElement("span"); destLabel.textContent = "DESTINATIONS";
    const destCount = document.createElement("strong"); destCount.textContent = group.destinations + (group.destinations === 1 ? " channel" : " channels");
    destination.append(destLabel, destCount); flow.append(source, arrow, destination); card.append(top, flow); host.append(card);
  });
}
async function refresh() {
  setLoading(true);
  try {
    const response = await fetch("/api/status", {cache:"no-store"});
    const data = await response.json();
    if (!data.ok) { message("Configuration needs attention: " + (data.error || "unknown error"), "error"); }
    else { message("Connected. Dashboard status is up to date.", "success"); }
    $("status").textContent = data.running ? "Running" : "Stopped";
    $("status-dot").className = "status-dot" + (data.running ? " running" : "");
    $("pid").textContent = data.pid ? "Worker process · PID " + data.pid : "No forwarding worker is active";
    $("route-count").textContent = data.groups ? data.groups.length : "—";
    $("delay").textContent = data.delay ?? "—";
    renderRoutes(data.groups || []);
    $("start").disabled = !!data.running;
    $("stop").disabled = !data.running;
  } catch (_) { message("Cannot reach the dashboard service. Check the server logs.", "error"); }
  finally { setLoading(false); }
}
async function action(path) {
  $("start").disabled = true; $("stop").disabled = true; message("Sending request…");
  try {
    const response = await fetch(path, {method:"POST"});
    const data = await response.json();
    message(data.message || data.error || "Request completed.", response.ok ? "success" : "error");
  } catch (_) { message("Request failed. Check the server process and logs.", "error"); }
  await refresh();
}
$("start").addEventListener("click", () => action("/api/start"));
$("stop").addEventListener("click", () => action("/api/stop"));
refresh();
setInterval(refresh, 10000);
