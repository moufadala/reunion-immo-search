import { useEffect, useMemo, useState } from "react";
import Lenis from "lenis";
import Card from "./components/Card";
import Detail from "./components/Detail";
import Mouvements from "./components/Mouvements";
import Filters, { FILTRES_VIDES, appliquer, trier } from "./components/Filters";
import { Badge, Button, Reveal, Stat, Empty } from "./components/ui";
import { alerteFeedPerime, cx, dateFR, feedEstPerime, ilYA, norm } from "./lib";

const ONGLETS = [
  { id: "annonces", nom: "Annonces" },
  { id: "mouvements", nom: "Mouvements" },
  { id: "sources", nom: "Sources" },
];
const PORTAILS_ATTENDUS = [
  ["97immo", "97 Immo"],
  ["adrezio", "Adrezio"],
  ["alter", "Alter Immobilier"],
  ["bienici", "Bien'ici"],
  ["citya", "Citya"],
  ["domimmo", "Domimmo"],
  ["fnaim", "FNAIM"],
  ["immo974", "Immo 974"],
  ["leboncoin", "Leboncoin"],
  ["locamoi", "LocaMoi"],
  ["ofim", "OFIM"],
  ["seloger", "SeLoger"],
  ["superimmo", "Superimmo"],
  ["zimo", "Zimo"],
];

const ETATS_SOURCE = {
  fresh: ["à jour", "p5"],
  aging: ["à surveiller", "warn"],
  stale: ["en retard", "danger"],
  "coverage-low": ["collecte partielle", "danger"],
  empty: ["aucune annonce", "warn"],
  unknown: ["état inconnu", "neutral"],
};


function ThemeToggle() {
  // Moufadal préfère le clair : c'est le défaut, pas "auto".
  const [t, setT] = useState(() => localStorage.getItem("theme") || "light");
  useEffect(() => {
    const r = document.documentElement;
    if (t === "auto") r.removeAttribute("data-theme");
    else r.setAttribute("data-theme", t);
    localStorage.setItem("theme", t);
  }, [t]);
  const suivant = { light: "dark", dark: "auto", auto: "light" };
  const icone = { auto: "◐", light: "☀", dark: "☾" };
  return (
    <button onClick={() => setT(suivant[t])} aria-label={`Thème : ${t}`}
      title={`Thème : ${t} — toucher pour changer`}
      className="grid h-8 w-8 shrink-0 place-items-center rounded-lg border border-line
                 text-[13px] text-muted active:bg-sunken cursor-pointer">
      {icone[t]}
    </button>
  );
}

function Header({ meta, onglet, setOnglet }) {
  const alerte = alerteFeedPerime(meta?.genere_le);
  return (
    <header className="sticky top-0 z-30 border-b border-line bg-canvas/85 backdrop-blur-xl">
      <div className="mx-auto flex max-w-[1400px] flex-col gap-3 px-4 py-3 sm:px-6">
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-baseline gap-2.5">
            <span className="text-[15px] font-extrabold tracking-tight text-ink">Veille locative</span>
            <span className="hidden text-[12.5px] font-semibold text-accent sm:inline">Saint-Denis + Sainte-Marie</span>
          </div>
          <div className="flex items-center gap-2">
            <span className={cx("text-[11.5px]", alerte ? "font-bold text-danger" : "text-faint")}>
              {alerte || (meta?.genere_le ? `mis à jour ${ilYA(meta.genere_le)}` : "")}
            </span>
            <ThemeToggle />
          </div>
        </div>
        <nav aria-label="Navigation principale" className="flex gap-1">
          {ONGLETS.map((o) => (
            <button key={o.id} onClick={() => setOnglet(o.id)}
              aria-current={onglet === o.id ? "page" : undefined}
              className={cx(
                "relative rounded-lg px-3 py-1.5 text-[13.5px] font-bold transition-colors cursor-pointer",
                onglet === o.id ? "text-accent" : "text-muted hover:text-ink"
              )}>
              {o.nom}
              {onglet === o.id && (
                <span className="absolute inset-x-2 -bottom-[13px] h-[2px] rounded-full bg-accent" />
              )}
            </button>
          ))}
        </nav>
      </div>
    </header>
  );
}

function Sources({ sources, meta, health, healthError }) {
  const feedBySource = new Map((sources || []).map((s) => [norm(s.nom), s]));
  const healthSources = Array.isArray(health?.sources) ? health.sources : [];
  const healthBySource = new Map(healthSources.map((s) => [norm(s.source), s]));
  const rows = PORTAILS_ATTENDUS.map(([id, label]) => ({
    id,
    label,
    feed: feedBySource.get(norm(id)) || {},
    health: healthBySource.get(norm(id)) || null,
  }));
  const auxiliaries = healthSources.filter((s) => norm(s.source) === "ofim rss");
  const freshCount = rows.filter((r) => r.health?.status === "fresh").length;
  const attentionCount = rows.filter((r) => r.health && r.health.status !== "fresh").length;
  const missingCount = rows.filter((r) => !r.health).length;
  const statusLabel = (source) => ETATS_SOURCE[source?.status] || ETATS_SOURCE.unknown;
  const coverage = (source) => source?.active_coverage_ratio == null
    ? "non mesurée"
    : `${Math.round(source.active_coverage_ratio * 100)} %`;

  return (
    <div data-testid="sources-panel" className="flex flex-col gap-4">
      <Reveal className="rounded-2xl border border-line bg-surface p-5 shadow-[var(--shadow-card)]">
        <h3 className="text-[15px] font-extrabold text-ink">Qualité de la localisation</h3>
        <p className="mt-0.5 text-[12.5px] text-muted">
          À quel point sait-on <em>où</em> se trouvent les biens ? Plus la barre est à droite, mieux c'est.
        </p>
        <div className="mt-4 flex flex-col gap-2">
          {Object.entries(meta?.precision || {}).map(([nom, n]) => (
            <div key={nom} className="flex items-center gap-3">
              <span className="w-44 shrink-0 text-[12.5px] font-semibold text-muted">{nom}</span>
              <div className="h-2.5 flex-1 overflow-hidden rounded-full bg-sunken">
                <div className="h-full rounded-full bg-accent"
                  style={{ width: `${(n / (meta.total || 1)) * 100}%` }} />
              </div>
              <span className="w-10 shrink-0 text-right text-[12.5px] font-bold tabular-nums text-ink">{n}</span>
            </div>
          ))}
        </div>
      </Reveal>

      <Reveal data-testid="source-health-summary"
        className="rounded-2xl border border-line bg-surface p-5 shadow-[var(--shadow-card)]">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h3 className="text-[15px] font-extrabold text-ink">État réel des collectes</h3>
            <p className="mt-0.5 text-[12.5px] text-muted">
              Les statuts viennent du dernier contrôle source_health, pas d'une liste codée dans la page.
            </p>
          </div>
          <Badge tone={health?.ok ? "p5" : "danger"}>
            {health ? (health.ok ? "contrôle global OK" : "attention requise") : "contrôle indisponible"}
          </Badge>
        </div>
        <div className="mt-4 grid grid-cols-2 gap-4 sm:grid-cols-4">
          <Stat value={PORTAILS_ATTENDUS.length} label="Portails attendus" />
          <Stat value={freshCount} label="À jour" tone="accent" />
          <Stat value={attentionCount} label="À contrôler" />
          <Stat value={missingCount} label="Sans mesure" />
        </div>
        {health?.generated_at && (
          <p className="mt-3 text-[11.5px] text-faint">
            Contrôle généré {ilYA(health.generated_at)} · {dateFR(health.generated_at)}
          </p>
        )}
        {healthError && (
          <div data-testid="source-health-error" className="mt-3 rounded-xl border border-warn/30 bg-warn/10 p-3 text-[12.5px] text-ink">
            Le contrôle des sources n'a pas pu être chargé ({healthError}). Les annonces restent consultables, mais leur collecte n'est pas vérifiable ici.
          </div>
        )}
      </Reveal>
      <Reveal className="overflow-hidden rounded-2xl border border-line bg-surface shadow-[var(--shadow-card)]">
        <div className="border-b border-line p-4">
          <h3 className="text-[15px] font-extrabold text-ink">Portails suivis</h3>
          <p className="mt-0.5 text-[12.5px] text-muted">
            « Description vérifiée » = le texte publié vient d'un bloc descriptif complet
            identifié sur la page de l'annonce, pas seulement d'un HTTP 200 ou d'une balise SEO.
          </p>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-[13px]">
            <thead>
              <tr className="border-b border-line text-left text-[11px] uppercase tracking-wider text-faint">
                <th className="px-4 py-2 font-bold">Portail</th>
                <th className="px-4 py-2 text-right font-bold">Actives DB</th>
                <th className="px-4 py-2 text-right font-bold">Vues récemment</th>
                <th className="px-4 py-2 text-right font-bold">Couverture</th>
                <th className="px-4 py-2 font-bold">Dernière collecte</th>
                <th className="px-4 py-2 text-right font-bold">Description vérifiée</th>
                <th className="px-4 py-2 font-bold">Critique</th>
                <th className="px-4 py-2 font-bold">État</th>
              </tr>
            </thead>
            <tbody>
              {rows.map(({ id, label, feed, health: source }) => {
                const [stateText, stateTone] = statusLabel(source);
                const detailPct = feed.total ? Math.round(((feed.detail_lu || 0) / feed.total) * 100) : null;
                const lastCollection = source?.last_fetched_at || source?.last_seen_at;
                return (
                  <tr key={id} data-testid="source-portal-row" data-source={id}
                    className="border-b border-line/60 last:border-0">
                    <td className="px-4 py-2.5 font-bold text-ink">{label}</td>
                    <td className="px-4 py-2.5 text-right tabular-nums text-muted">{source?.active_rows ?? "—"}</td>
                    <td className="px-4 py-2.5 text-right tabular-nums text-muted">{source?.active_recent_rows ?? "—"}</td>
                    <td className="px-4 py-2.5 text-right tabular-nums font-semibold text-ink">{coverage(source)}</td>
                    <td className="px-4 py-2.5 text-muted" title={lastCollection || undefined}>
                      {ilYA(lastCollection) || "date absente"}
                    </td>
                    <td className="px-4 py-2.5 text-right tabular-nums text-muted"
                      title={feed.total ? `${feed.detail_lu || 0}/${feed.total} annonces du feed` : "absent du feed publié"}>
                      {detailPct == null ? "—" : `${detailPct} %`}
                    </td>
                    <td className="px-4 py-2.5">
                      {source ? <Badge tone={source.is_critical ? "danger" : "neutral"}>
                        {source.is_critical ? "oui" : "non"}
                      </Badge> : "—"}
                    </td>
                    <td className="px-4 py-2.5">
                      <Badge tone={stateTone} title={source?.reason || "aucune mesure source_health"}>{stateText}</Badge>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </Reveal>
      {auxiliaries.length > 0 && (
        <Reveal className="rounded-2xl border border-line bg-surface p-4 shadow-[var(--shadow-card)]">
          <h3 className="text-[14px] font-extrabold text-ink">Canaux auxiliaires</h3>
          <p className="mt-0.5 text-[12px] text-muted">
            OFIM RSS complète le portail OFIM. Il ne compte pas comme un quinzième portail.
          </p>
          <div className="mt-3 flex flex-col gap-2">
            {auxiliaries.map((source) => {
              const [stateText, stateTone] = statusLabel(source);
              const lastCollection = source.last_fetched_at || source.last_seen_at;
              return (
                <div key={source.source} data-testid="source-auxiliary-row"
                  className="flex flex-wrap items-center justify-between gap-2 rounded-xl bg-sunken px-3 py-2">
                  <span className="text-[12.5px] font-bold text-ink">OFIM RSS · canal auxiliaire</span>
                  <span className="text-[12px] text-muted">{source.active_rows ?? 0} actives · {ilYA(lastCollection) || "date absente"}</span>
                  <Badge tone={stateTone} title={source.reason}>{stateText}</Badge>
                </div>
              );
            })}
          </div>
        </Reveal>
      )}
    </div>
  );
}

export default function App() {
  const [data, setData] = useState(null);
  const [err, setErr] = useState(null);
  const [health, setHealth] = useState(null);
  const [healthError, setHealthError] = useState(null);
  const [onglet, setOnglet] = useState("annonces");
  const [f, setF] = useState(FILTRES_VIDES);
  const [ouvert, setOuvert] = useState(null);
  const [page, setPage] = useState(1);

  useEffect(() => {
    const l = new Lenis({ duration: 1.05, smoothWheel: true });
    let id;
    const raf = (t) => { l.raf(t); id = requestAnimationFrame(raf); };
    id = requestAnimationFrame(raf);
    return () => { cancelAnimationFrame(id); l.destroy(); };
  }, []);

  useEffect(() => {
    fetch("feed.json", { cache: "no-store" })
      .then((r) => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
      .then(setData)
      .catch((e) => setErr(e.message));
  }, []);
  useEffect(() => {
    fetch("source_health.json", { cache: "no-store" })
      .then((r) => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
      .then(setHealth)
      .catch((e) => setHealthError(e.message));
  }, []);


  const listings = data?.listings || [];
  const feedPerime = feedEstPerime(data?.meta?.genere_le);
  const communes = useMemo(
    () => [...new Set(listings.map((l) => l.commune).filter(Boolean))].sort(), [listings]);
  const quartiers = useMemo(() => {
    const c = new Map();
    for (const l of listings) {
      const z = l.quartier;
      if (z && norm(z) !== norm(l.commune)) c.set(z, (c.get(z) || 0) + 1);
    }
    return [...c.entries()].sort((a, b) => b[1] - a[1]).slice(0, 18).map(([z]) => z);
  }, [listings]);

  const filtrees = useMemo(
    () => trier(appliquer(listings, f), f.tri, f.profil), [listings, f]);
  useEffect(() => { setPage(1); }, [f]);

  const profilsFeed = Object.entries(data?.meta?.profils || {});
  const marche = data?.meta?.marche || {};

  if (err) {
    return (
      <div className="grid min-h-screen place-items-center p-6">
        <Empty titre="Impossible de charger les données"
          texte={`feed.json n'a pas répondu (${err}). Relance scripts/export_feed.py sur le VPS.`} />
      </div>
    );
  }
  if (!data) {
    return (
      <div className="grid min-h-screen place-items-center">
        <div className="h-7 w-7 animate-spin rounded-full border-2 border-line border-t-accent" />
      </div>
    );
  }

  const visibles = filtrees.slice(0, page * 36);

  return (
    <>
      <Header meta={data.meta} onglet={onglet} setOnglet={setOnglet} />

      <main data-testid="app-root" className="mx-auto flex max-w-[1400px] flex-col gap-5 px-4 py-5 sm:px-6 sm:py-7">
        {onglet === "annonces" && (
          <>
            <Reveal className="grid grid-cols-2 gap-5 rounded-2xl border border-line bg-surface p-4 shadow-[var(--shadow-card)] sm:grid-cols-4 sm:p-5">
              <Stat value={data.meta.actives} label="En ligne" tone="accent"
                sub={`sur ${data.meta.total} suivies`} />
              <Stat value={feedPerime ? "—" : data.meta.fraiches} label="Fraîches"
                sub={feedPerime ? "masquées : feed périmé" : "apparues ≤ 3 jours"} />
              <Stat value={data.meta.detail_lu} label="Descriptions vérifiées"
                sub="texte complet identifié sur la page source" />
              <Stat value={data.meta.avec_trajet} label="Temps de trajet"
                sub="calculé en voiture" />
            </Reveal>
            <p className="-mt-2 text-[12.5px] font-medium text-faint">
              {marche.retirees_7j ?? 0} annonces retirées · {marche.nouvelles_7j ?? 0} nouvelles sur 7 jours
            </p>

            {/* Sélection de profil : bande qui défile au doigt sur mobile */}
            <div className="-mx-4 flex gap-2 overflow-x-auto px-4 sm:mx-0 sm:flex-wrap sm:px-0"
              style={{ WebkitOverflowScrolling: "touch", scrollbarWidth: "none" }}>
              <button onClick={() => setF({ ...f, profil: "" })}
                className={cx(
                  "shrink-0 rounded-xl border px-3 py-2 text-left transition-colors cursor-pointer",
                  !f.profil ? "border-accent bg-accent-soft" : "border-line bg-surface"
                )}>
                <span className={cx("block text-[12.5px] font-bold",
                  !f.profil ? "text-accent-ink" : "text-ink")}>Tout</span>
                <span className="block text-[11px] text-faint">{listings.filter((l) => l.active).length} en ligne</span>
              </button>
              {profilsFeed.map(([cle, p]) => (
                <button key={cle} onClick={() => setF({ ...f, profil: f.profil === cle ? "" : cle })}
                  className={cx(
                    "shrink-0 rounded-xl border px-3 py-2 text-left transition-colors cursor-pointer",
                    f.profil === cle ? "border-accent bg-accent-soft" : "border-line bg-surface"
                  )}>
                  <span className={cx("block text-[12.5px] font-bold",
                    f.profil === cle ? "text-accent-ink" : "text-ink")}>★ {p.nom}</span>
                  <span className="block text-[11px] text-faint">
                    {p.correspondances} corresp. · {p.resume}
                  </span>
                </button>
              ))}
            </div>

            {/* Panneau de filtres retiré le 27/07 à la demande de Moufadal.
                Le tri et les profils suffisent pour l'instant. Pour le remettre :
                réafficher <Filters f={f} set={setF} ... /> — la logique est intacte. */}
            <div className="flex items-center justify-between gap-3">
              <p className="text-[13px] text-muted">
                <strong data-testid="listing-count" className="text-ink tabular-nums">{filtrees.length}</strong> annonce
                {filtrees.length > 1 ? "s" : ""}
                <span className="text-faint"> · les plus fraîches et les mieux notées d'abord</span>
              </p>
              <select aria-label="Trier les annonces" value={f.tri} onChange={(e) => setF({ ...f, tri: e.target.value })}
                className="h-9 rounded-xl border border-line bg-surface px-2.5 text-[13px]
                           font-semibold text-ink outline-none">
                <option value="pertinence">Fraîches + mes critères</option>
                <option value="score">Meilleur score</option>
                <option value="recent">Plus récentes</option>
                <option value="trajet">Plus proches en voiture</option>
                <option value="prixAsc">Loyer croissant</option>
                <option value="surface">Plus grandes</option>
              </select>
            </div>

            {filtrees.length === 0 ? (
              <Empty titre="Aucune annonce ne correspond"
                texte="Les critères sont peut-être trop serrés. Les annonces dont l'information est inconnue ne sont jamais exclues — donc si tu ne vois rien, c'est un filtre chiffré qui coince.">
                <Button variant="outline" size="sm"
                  onClick={() => setF(FILTRES_VIDES)}>Tout réafficher</Button>
              </Empty>
            ) : (
              <>
                <div data-testid="listings-grid" className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
                  {visibles.map((l, i) => (
                    <Reveal key={l.id} delay={Math.min(i % 12, 8) * 25}>
                      <Card l={l} feedPerime={feedPerime} onOuvrir={setOuvert} />
                    </Reveal>
                  ))}
                </div>
                {visibles.length < filtrees.length && (
                  <div className="flex justify-center pt-1">
                    <Button variant="outline" onClick={() => setPage((p) => p + 1)}>
                      Afficher {Math.min(36, filtrees.length - visibles.length)} de plus
                    </Button>
                  </div>
                )}
              </>
            )}
          </>
        )}

        {onglet === "mouvements" && <Mouvements listings={listings} movements={data?.movements} />}
        {onglet === "sources" && <Sources sources={data.sources} meta={data.meta}
          health={health} healthError={healthError} />}

        <footer className="mt-4 border-t border-line pt-4 text-[11.5px] leading-relaxed text-faint">
          Périmètre : {data.meta.perimetre.join(" · ")}. Historique conservé 1 mois.
          Feed généré le {dateFR(data.meta.genere_le)}. Usage privé.
        </footer>
      </main>

      {ouvert && <Detail l={ouvert} onClose={() => setOuvert(null)} />}
    </>
  );
}
