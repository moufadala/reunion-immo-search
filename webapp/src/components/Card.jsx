import { useState } from "react";
import { Badge } from "./ui";
import {
  cx, eur, m2, ilYA, precisionDe, prixAuM2, chambresEstimees, typeLisible, osmUrl,
} from "../lib";

/* Tailwind ne voit que les classes écrites en toutes lettres : `text-${x}`
   serait supprimé au build. D'où ces tables. */
const PIN_CLASS = { p5: "text-p5", p4: "text-p4", p3: "text-p3", p2: "text-p2", p0: "text-p0" };

function Pin({ className }) {
  return (
    <svg viewBox="0 0 16 16" className={className} aria-hidden>
      <path d="M8 1.6c-2.4 0-4.3 1.9-4.3 4.3C3.7 9.2 8 14.4 8 14.4s4.3-5.2 4.3-8.5c0-2.4-1.9-4.3-4.3-4.3z"
        fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" />
      <circle cx="8" cy="5.9" r="1.5" fill="currentColor" />
    </svg>
  );
}

function Car({ className }) {
  return (
    <svg viewBox="0 0 16 16" className={className} aria-hidden>
      <path d="M2.6 10.2V7.6l1.3-3a1 1 0 01.9-.6h6.4a1 1 0 01.9.6l1.3 3v2.6"
        fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round" />
      <path d="M2.6 10.2h10.8v1.6a.6.6 0 01-.6.6h-1a.6.6 0 01-.6-.6v-.6H4.8v.6a.6.6 0 01-.6.6h-1a.6.6 0 01-.6-.6v-1.6z"
        fill="currentColor" />
      <circle cx="4.9" cy="8.6" r=".8" fill="currentColor" />
      <circle cx="11.1" cy="8.6" r=".8" fill="currentColor" />
    </svg>
  );
}

/* La bande de critères. Elle DÉFILE AU DOIGT (gauche-droite) — pas de survol,
   on est sur Android. Elle ne montre que ce qu'on sait réellement ; ce qui est
   inconnu est regroupé à la fin, sans mentir sur son absence. */
function Bande({ l }) {
  const sus = [];
  const push = (txt, ton = "neutral") => sus.push({ txt, ton });
  const llm = l.llm_extraction_status === "fresh" ? l.llm_extraction : null;

  if (l.floor) {
    const asc = l.elevator === 1 ? " · ascenseur"
      : l.elevator === 0 ? " · sans ascenseur" : "";
    push(l.floor + asc, l.floor === "RDC" || (/^1/.test(l.floor) && l.elevator !== 0) ? "p5" : "neutral");
  }
  if (l.niveaux) push(`${l.niveaux} niveaux`);
  if (l.nb_sdb) push(l.nb_sdb > 1 ? `${l.nb_sdb} salles de bain` : "1 salle de bain", l.nb_sdb > 1 ? "p5" : "neutral");
  if (l.bathtub != null) push(l.bathtub ? "baignoire" : "douche", "neutral");
  if (l.wc_separe) push("WC séparés", "p5");
  else if (l.nb_wc) push(`${l.nb_wc} WC`);
  if (l.jardin) push("jardin", "p5");
  if (l.veranda) push("véranda", "p5");
  if (l.terrasse) push("terrasse");
  if (l.parking) push("parking");
  if (l.piscine) push("piscine");
  if (l.clim) push("clim");
  for (const axe of (llm?.routes_axes || []).slice(0, 2)) push(axe, "p2");
  for (const prox of (llm?.proximites || []).slice(0, 2)) push(prox, "p5");
  if (l.furnished != null) push(l.furnished ? "meublé" : "non meublé", l.furnished === 0 ? "p5" : "neutral");

  const inconnus = [];
  if (l.floor == null) inconnus.push("étage");
  if (l.elevator == null) inconnus.push("ascenseur");
  if (l.bathtub == null) inconnus.push("sdb");
  if (inconnus.length) push(`${inconnus.join(", ")} ?`, "p0");

  if (!sus.length) return null;

  return (
    <div
      className="-mx-3.5 flex gap-1.5 overflow-x-auto px-3.5 pb-0.5 [scrollbar-width:none] [&::-webkit-scrollbar]{display:none}"
      style={{ WebkitOverflowScrolling: "touch", scrollbarWidth: "none" }}
    >
      {sus.map((s, i) => (
        <Badge key={i} tone={s.ton} className="shrink-0">{s.txt}</Badge>
      ))}
    </div>
  );
}

/* Pas de nom de profil en dur ici (ex. "Maman") : ce fichier part sur un
   depot GitHub public, le nom du profil (donnee personnelle) ne doit vivre
   que cote serveur (scripts/profils.py, hors depot). On derive juste un
   libelle a partir de la cle. */
function libelleProfil(cle) {
  return cle ? cle.charAt(0).toUpperCase() + cle.slice(1) : "";
}

function datePrixCourt(iso) {
  if (!iso) return "";
  const m = String(iso).match(/^(\d{4})-(\d{2})-(\d{2})/);
  return m ? `${m[3]}/${m[2]}` : String(iso).slice(0, 10);
}

function deltaPrix(delta) {
  const n = Math.abs(Number(delta || 0));
  return `−${n.toLocaleString("fr-FR")} €`;
}

export default function Card({ l, feedPerime = false, onOuvrir }) {
  const [imgKo, setImgKo] = useState(false);
  const [imageIndex, setImageIndex] = useState(0);
  const images = Array.from(new Set([
    l.image,
    ...(Array.isArray(l.images) ? l.images : []),
  ].filter(Boolean)));
  const currentImage = images.length ? images[imageIndex % images.length] : null;
  const p = precisionDe(l);
  const ch = chambresEstimees(l);
  const m = prixAuM2(l);
  const carte = osmUrl(l.lat, l.lon);
  const best = l.meilleur_profil ? l.profils[l.meilleur_profil] : null;
  const changerPhoto = (e, delta) => {
    e.stopPropagation();
    setImgKo(false);
    setImageIndex((index) => (index + delta + images.length) % images.length);
  };

  return (
    <article
      data-testid="listing-card"
      data-listing-id={l.id}
      onClick={() => onOuvrir(l)}
      className={cx(
        "group flex cursor-pointer flex-col overflow-hidden rounded-[16px] border bg-surface",
        "shadow-[var(--shadow-card)] transition-[box-shadow,transform] duration-300",
        "active:scale-[0.995] sm:hover:-translate-y-0.5 sm:hover:shadow-[var(--shadow-lift)]",
        // une annonce fraîche ne se rate pas : bordure et fond distincts
        !feedPerime && l.fraiche && l.active
          ? "border-accent/45 bg-accent-soft/35 ring-1 ring-accent/15"
          : "border-line",
        !l.active && "opacity-[0.68]"
      )}
    >
      <div className="relative aspect-[16/10] overflow-hidden bg-sunken">
        {currentImage && !imgKo ? (
          <img data-testid="card-photo" src={currentImage}
            alt={images.length > 1 ? `Photo ${imageIndex % images.length + 1} sur ${images.length}` : ""}
            loading="lazy" onError={() => setImgKo(true)}
            className="h-full w-full object-cover transition-transform duration-500 sm:group-hover:scale-[1.045]" />
        ) : (
          <div className="grid h-full place-items-center text-[12px] text-faint">Photo indisponible</div>
        )}

        {images.length > 1 && (
          <>
            <button type="button" aria-label="Photo précédente" onClick={(e) => changerPhoto(e, -1)}
              className="absolute left-2 top-1/2 grid h-9 w-9 -translate-y-1/2 place-items-center rounded-full bg-ink/70 text-xl font-bold text-canvas shadow backdrop-blur-sm active:bg-ink/90">
              ‹
            </button>
            <button type="button" aria-label="Photo suivante" onClick={(e) => changerPhoto(e, 1)}
              className="absolute right-2 top-1/2 grid h-9 w-9 -translate-y-1/2 place-items-center rounded-full bg-ink/70 text-xl font-bold text-canvas shadow backdrop-blur-sm active:bg-ink/90">
              ›
            </button>
            <span className="absolute bottom-2.5 left-2.5 rounded-full bg-ink/70 px-2 py-1 text-[11px] font-bold tabular-nums text-canvas backdrop-blur-sm">
              {imageIndex % images.length + 1}/{images.length}
            </span>
          </>
        )}

        <div className="absolute left-2.5 top-2.5 flex max-w-[75%] flex-wrap gap-1.5">
          {!feedPerime && l.fraiche && l.active && <Badge tone="accent">Nouvelle</Badge>}
          {!l.active && <Badge tone="danger">Retirée</Badge>}
          {best && (
            <Badge tone="p5" title={`Score ${best.score}/100 pour ce profil`}>
              ★ {libelleProfil(l.meilleur_profil)}
            </Badge>
          )}
        </div>

        {l.rent != null && (
          <div className="absolute bottom-2.5 right-2.5 rounded-xl bg-ink/85 px-2.5 py-1.5 text-right backdrop-blur-sm">
            {l.changement_prix ? (
              <>
                <div className="flex items-baseline justify-end gap-1.5">
                  <span className="text-[11px] font-semibold tabular-nums text-canvas/55 line-through">
                    {eur(l.changement_prix.ancien)}
                  </span>
                  <span className="text-[15px] font-extrabold tabular-nums text-canvas">{eur(l.rent)}</span>
                </div>
                <div className="mt-0.5 text-[11px] font-bold tabular-nums text-p5">
                  ▼ {deltaPrix(l.changement_prix.delta)} depuis le {datePrixCourt(l.changement_prix.depuis)}
                </div>
              </>
            ) : (
              <span className="text-[15px] font-extrabold tabular-nums text-canvas">{eur(l.rent)}</span>
            )}
            {m && <span className="ml-1 text-[11px] font-medium text-canvas/70">{m} €/m²</span>}
          </div>
        )}
      </div>

      <div className="flex flex-1 flex-col gap-2 p-3.5">
        {/* localisation — la ligne la plus importante */}
        <div className="flex items-start gap-1.5">
          <Pin className={cx("mt-[1px] h-4 w-4 shrink-0", PIN_CLASS[p.couleur] || "text-p0")} />
          <div className="min-w-0 flex-1">
            <p className="truncate text-[13.5px] font-bold leading-tight text-ink">
              {l.location_label || "Localisation non déterminée"}
            </p>
            <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1">
              <Badge tone={p.couleur} title={`Précision : ${p.mot}`}>{p.court}</Badge>
              {l.commune && l.location_label !== l.commune && (
                <span className="text-[11.5px] text-muted">{l.commune}</span>
              )}
            </div>
          </div>
        </div>

        {l.trajet && (
          <div className="flex items-center gap-1.5 text-[12px] text-muted">
            <Car className="h-3.5 w-3.5 shrink-0 text-faint" />
            <span className="font-semibold text-ink/85">≈ {l.trajet.minutes} min</span>
            <span className="text-faint">
              {l.trajet.km} km{l.trajet.estime && " · estimé depuis le quartier"}
            </span>
          </div>
        )}

        <div className="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[12.5px] font-semibold text-muted">
          {l.surface != null && <span className="tabular-nums">{m2(l.surface)}</span>}
          {l.rooms != null && <span className="tabular-nums">{l.rooms} pièces</span>}
          {ch.n != null && (
            <span className={cx("tabular-nums", ch.deduit && "text-faint")}
              title={ch.deduit ? "Déduit du nombre de pièces" : "Indiqué par l'annonce"}>
              {ch.n} ch.{ch.deduit && "~"}
            </span>
          )}
          {l.charges != null && <span className="tabular-nums text-faint">+{eur(l.charges)} ch.</span>}
        </div>

        <Bande l={l} />

        {l.description && (
          <p className="line-clamp-2 text-[12px] leading-relaxed text-muted">
            {l.description}
          </p>
        )}

        {best?.raisons?.length > 0 && (
          <p className="text-[11.5px] font-semibold text-p5">
            ★ {best.raisons.join(" · ")}
          </p>
        )}
        {best?.alertes?.length > 0 && (
          <p className="text-[11px] text-warn">⚠ {best.alertes.join(" · ")}</p>
        )}

        <div className="mt-auto flex items-center justify-between gap-2 border-t border-line pt-2">
          <span className="truncate text-[11px] text-faint">
            {l.source}
            {l.also_on?.length > 1 && <> / {l.also_on.length} portails</>}
            {l.detail_read && <span className="ml-1 text-p5" title="Description complète récupérée">✓</span>}
            {l.published ? <> · publiée {ilYA(l.published)}</> : <> · repérée {ilYA(l.seen_first)}</>}
          </span>
          <div className="flex shrink-0 gap-1.5">
            <button type="button" onClick={(e) => { e.stopPropagation(); onOuvrir(l); }}
              className="rounded-lg px-2 py-1.5 text-[12px] font-bold text-muted active:bg-sunken">
              Détails
            </button>
            {carte && (
              <a href={carte} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()}
                className="rounded-lg px-2 py-1.5 text-[12px] font-bold text-muted active:bg-sunken">
                Carte
              </a>
            )}
            <a href={l.url} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()}
              className="rounded-lg bg-accent-soft px-2.5 py-1.5 text-[12px] font-bold text-accent-ink">
              Annonce
            </a>
          </div>
        </div>
      </div>
    </article>
  );
}
