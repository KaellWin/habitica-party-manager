"use strict";
let token = "";
const $ = id => document.getElementById(id);
async function api(path, body) {
  const response = await fetch("/api/" + path, {
    method: body === undefined ? "GET" : "POST",
    headers: {Authorization: "Bearer " + token, "Content-Type": "application/json"},
    ...(body === undefined ? {} : {body: JSON.stringify(body)})
  });
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail));
  return data;
}
function run(fn) { return async () => {
  try { $("message").textContent = ""; await fn(); }
  catch (error) { $("message").textContent = error.message; }
}; }
function button(text, action) {
  const node = document.createElement("button"); node.textContent = text; node.onclick = run(action); return node;
}
async function refresh() {
  const state = await api("status");
  $("panel").hidden = false;
  $("mode").value = state.mode;
  $("mode-title").textContent = state.master + " · " + state.mode;
  $("counts").textContent = `${state.events.unique_events} événements distincts · ${state.events.receipts} réceptions · ` +
    state.operations.map(x => `${x.count} ${x.status}`).join(" · ");
  $("companions").replaceChildren();
  for (const c of state.companions.filter(x => x.configured)) {
    const card = document.createElement("div"); card.className = "card";
    const title = document.createElement("strong"); title.textContent = c.name;
    const status = document.createElement("span");
    status.textContent = `${c.enabled ? "Activé" : "Désactivé"} · ${c.prepared ? "Préparé" : "À préparer"}`;
    card.append(title, status, button(c.enabled ? "Désactiver" : "Activer", async () => {
      await api(`companions/${c.name}/enabled`, {enabled: !c.enabled}); await refresh();
    }), button("Auditer les tâches", async () => {
      const report = await api(`companions/${c.name}/audit`, {});
      $("report").textContent = JSON.stringify(report, null, 2); await refresh();
    }));
    if (c.audit) card.append(button("Préparer l’habitude technique", async () => {
      if (!confirm("Avez-vous examiné le rapport et les automatisations existantes ? Une habitude positive sera créée si nécessaire. Aucune tâche existante ne sera modifiée.")) return;
      $("report").textContent = JSON.stringify(await api(`companions/${c.name}/prepare`, {
        audit_id: c.audit.audit_id, create: true, acknowledge_conflicts: true
      }), null, 2); await refresh();
    }));
    const info = document.createElement("small"); info.textContent = c.user_id + (c.task_id ? " · " + c.task_id : "");
    card.append(info); $("companions").append(card);
  }
  $("operations").replaceChildren();
  for (const op of state.last_operations) {
    const tr = document.createElement("tr");
    for (const value of [op.id, op.companion, op.status, op.attempts, op.error || "—"]) {
      const td = document.createElement("td"); td.textContent = value; tr.append(td);
    }
    const actions = document.createElement("td");
    if (["uncertain", "failed"].includes(op.status)) {
      for (const [action, label] of [["confirm", "Déjà attribuée"], ["abandon", "Abandonner"], ["retry", "Réessayer"]]) {
        actions.append(button(label, async () => {
          const note = prompt("Expliquez votre vérification (10 caractères minimum). Une reprise peut donner une double récompense.");
          if (!note) return;
          if (action === "retry" && !confirm("J’accepte le risque d’une double récompense pour cette seule opération.")) return;
          await api(`operations/${op.id}/resolve`, {action, note, acknowledge_risk: action === "retry"}); await refresh();
        }));
      }
    }
    tr.append(actions); $("operations").append(tr);
  }
  $("events").textContent = JSON.stringify(await api("events?limit=10"), null, 2);
}
$("login").onsubmit = async e => { e.preventDefault(); token = $("token").value; $("token").value = ""; await run(refresh)(); };
$("refresh").onclick = run(refresh);
$("connection").onclick = run(async () => { $("report").textContent = JSON.stringify(await api("connection"), null, 2); });
$("apply-mode").onclick = run(async () => {
  const mode = $("mode").value;
  if (mode === "live" && !confirm("Activer les récompenses pour les nouveaux événements uniquement ?")) return;
  await api("mode", {mode}); await refresh();
});
$("stop").onclick = run(async () => { await api("mode", {mode: "off"}); await refresh(); });
