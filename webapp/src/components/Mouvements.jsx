import { useMemo } from "react";
import { Badge, Stat, Reveal, Empty } from "./ui";
import { cx, eur, m2, dateFR, joursDepuis, precisionDe } from "../lib";

/* La page « stats » d'avant partait dans tous les sens. Ici une seule
   question par bloc, en français, et la réponse est cliquable. */

function Ligne({ l, motif }) {
  const p = precisionDe(l);
  return (
    <a href={l.url} target="_blank" rel="noreferrer"
      className="flex items-center gap-3 rounded-xl px-2.5 py-2 transition-colors hover:bg-sunken">
      <div className="h-11 w-14 shrink-0 overflow-hidden rounded-lg bg-sunken">
        {l.image && <img src={l.image} alt="" loading="lazy" className="h-full w-full object-cover" />}
      </div>
      <div className="min-w-0 flex-1">
        <p className="truncate text-[13px] font-bold text-ink">
          {l.location_label || l.commune || "Localisation inconnue"}
        </p>
        <p className="truncate text-[11.5px] text-muted">{l.title}</p>
      </div>
      <div className="shrink-0 text-right">
        <p className="text-[13px] font-extrabold tabular-nums text-ink">{eur(l.rent) || "—"}</p>
        <p className="text-[11px] text-faint">{m2(l.surface) || "—"}</p>
      </div>
      <span className="hidden w-24 shrink-0 text-right text-[11px] text-faint sm:block">{motif}</span>
    </a>
  );
}

function Bloc({ titre, question, reponse, items, motif, tone }) {
  return (
    <Reveal className="rounded-2xl border border-line bg-surface p-4 shadow-[var(--shadow-card)]">
      <div className="mb-3 flex items-start justify-between gap-3">
        <div>
          <h3 className="text-[15px] font-extrabold text-ink">{titre}</h3>
          <p className="mt-0.5 text-[12.5px] leading-snug text-muted">{question}</p>
        </div>
        <span className={cx("shrink-0 text-[30px] font-extrabold leading-none tabular-nums",
          tone === "accent" ? "text-accent" : tone === "danger" ? "text-danger" : "text-ink")}>
          {items.length}
        </span>
      </div>
      {items.length === 0 ? (
        <p className="rounded-xl bg-sunken px-3 py-4 text-center text-[12.5px] text-faint">{reponse}</p>
      ) : (
        <div className="-mx-1 flex flex-col">
          {items.slice(0, 8).map((l) => <Ligne key={l.id} l={l} motif={motif(l)} />)}
          {items.length > 8 && (
            <p className="px-2.5 pt-2 text-[11.5px] text-faint">
              + {items.length - 8} autre{items.length - 8 > 1 ? "s" : ""}
            </p>
          )}
        </div>
      )}
    </Reveal>
  );
}

/* Petit graphe d'activité : une barre par jour sur 30 jours.
   Pas de librairie — c'est 20 lignes et ça reste lisible. */
function Activite({ events }) {
  const jours = useMemo(() => {
    const t = new Map();
    for (let i = 29; i >= 0; i--) {
      const d = new Date(Date.now() - i * 86400000);
      t.set(d.toISOString().slice(0, 10), { entrees: 0, sorties: 0, d });
    }
    for (const event of events) {
      const day = (event.event_at || "").slice(0, 10);
      if (t.has(day) && event.event_type === "new") t.get(day).entrees++;
      if (t.has(day) && event.event_type === "disappeared") t.get(day).sorties++;
    }
    return [...t.values()];
  }, [events]);

  const max = Math.max(1, ...jours.map((j) => Math.max(j.entrees, j.sorties)));

  return (
    <Reveal className="rounded-2xl border border-line bg-surface p-4 shadow-[var(--shadow-card)]">
      <h3 className="text-[15px] font-extrabold text-ink">Rythme du marché — 30 jours</h3>
      <p className="mt-0.5 text-[12.5px] text-muted">
        Chaque barre = un jour. Vers le haut : annonces apparues. Vers le bas : retirées.
      </p>
      <div className="mt-4 flex items-end gap-[3px]" style={{ height: 96 }}>
        {jours.map((j, i) => (
          <div key={i} className="group relative flex flex-1 flex-col justify-end"
            title={`${j.d.toLocaleDateString("fr-FR", { day: "2-digit", month: "short" })} — ${j.entrees} apparue(s), ${j.sorties} retirée(s)`}>
            <div className="w-full rounded-t-[3px] bg-accent transition-opacity group-hover:opacity-70"
              style={{ height: `${(j.entrees / max) * 46}px`, minHeight: j.entrees ? 2 : 0 }} />
            <div className="w-full rounded-b-[3px] bg-danger/55 transition-opacity group-hover:opacity-70"
              style={{ height: `${(j.sorties / max) * 46}px`, minHeight: j.sorties ? 2 : 0 }} />
          </div>
        ))}
      </div>
      <div className="mt-2 flex justify-between text-[11px] text-faint">
        <span>{jours[0]?.d.toLocaleDateString("fr-FR", { day: "2-digit", month: "short" })}</span>
        <span>aujourd'hui</span>
      </div>
    </Reveal>
  );
}

export default function Mouvements({ listings, movements }) {
  const events = movements?.events || [];
  const { nouvelles, retirees, revenues, longues } = useMemo(() => {
    const recent = (e) => (joursDepuis(e.event_at) ?? 999) <= 7;
    return {
      nouvelles: events.filter((e) => e.event_type === "new" && recent(e)),
      retirees: events.filter((e) => e.event_type === "disappeared" && recent(e)),
      revenues: events.filter((e) => e.event_type === "reappeared" && recent(e)),
      longues: listings.filter((l) => l.active && (joursDepuis(l.seen_first) ?? 0) > 45)
        .sort((a, b) => (a.seen_first || "").localeCompare(b.seen_first || "")),
    };
  }, [listings, events]);

  if (!listings.length && !events.length) {
    return <Empty titre="Aucune donnée" texte="Le feed est vide — relance l'export." />;
  }

  return (
    <div className="flex flex-col gap-4">
      <Reveal className="grid grid-cols-2 gap-5 rounded-2xl border border-line bg-surface p-5 shadow-[var(--shadow-card)] sm:grid-cols-4">
        <Stat value={listings.filter((l) => l.active).length} label="En ligne" tone="accent"
          sub="annonces disponibles maintenant" />
        <Stat value={nouvelles.length} label="Nouvelles" sub="apparues ces 7 derniers jours" />
        <Stat value={retirees.length} label="Retirées" sub="disparues ces 7 derniers jours" />
        <Stat value={revenues.length} label="Réapparues" sub="revenues ces 7 derniers jours" />
      </Reveal>

      <Activite events={events} />

      <div className="grid gap-4 lg:grid-cols-2">
        <Bloc titre="Nouvelles annonces" tone="accent"
          question="Qu'est-ce qui est apparu depuis une semaine ?"
          reponse="Rien de neuf cette semaine."
          items={nouvelles} motif={(l) => dateFR(l.event_at)} />
        <Bloc titre="Annonces retirées" tone="danger"
          question="Qu'est-ce qui a disparu des portails cette semaine ?"
          reponse="Aucune annonce retirée cette semaine."
          items={retirees} motif={(l) => dateFR(l.event_at)} />
      </div>

      <Bloc titre="Toujours en ligne après 45 jours"
        question="Qu'est-ce qui ne part pas ? Souvent un prix trop haut — donc négociable."
        reponse="Aucune annonce ancienne encore en ligne."
        items={longues} motif={(l) => `depuis ${joursDepuis(l.seen_first)} j`} />

      <Bloc titre="Annonces réapparues"
        question="Qu'est-ce qui est revenu sur un portail cette semaine ?"
        reponse="Aucune annonce réapparue cette semaine."
        items={revenues} motif={(l) => dateFR(l.event_at)} />
    </div>
  );
}
