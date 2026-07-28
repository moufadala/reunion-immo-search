import { useEffect, useState } from "react";
import { Badge, Button } from "./ui";
import { cx, eur, m2, dateFR, ilYA, precisionDe, chambresEstimees, typeLisible, osmUrl } from "../lib";

/* Carrousel minimal : avant, une seule <img> etait servie meme quand
   l'annonce a plusieurs photos (galerie deja recuperee cote pipeline,
   jamais exposee ici). Remonte par Moufadal : "je pouvais pas toutes
   les regarder". */
function Galerie({ images }) {
  const [i, setI] = useState(0);
  if (!images?.length) return null;
  const n = images.length;
  return (
    <div className="relative mb-4">
      <img src={images[i]} alt="" className="aspect-[16/9] w-full rounded-2xl object-cover" />
      {n > 1 && (
        <>
          <button type="button" aria-label="Photo précédente"
            onClick={() => setI((i - 1 + n) % n)}
            className="absolute left-2 top-1/2 flex h-8 w-8 -translate-y-1/2 items-center justify-center rounded-full bg-ink/55 text-white backdrop-blur-sm">
            ‹
          </button>
          <button type="button" aria-label="Photo suivante"
            onClick={() => setI((i + 1) % n)}
            className="absolute right-2 top-1/2 flex h-8 w-8 -translate-y-1/2 items-center justify-center rounded-full bg-ink/55 text-white backdrop-blur-sm">
            ›
          </button>
          <div className="absolute bottom-2 left-1/2 flex -translate-x-1/2 gap-1.5">
            {images.map((_, idx) => (
              <button key={idx} type="button" aria-label={`Photo ${idx + 1}`}
                onClick={() => setI(idx)}
                className={cx("h-1.5 w-1.5 rounded-full", idx === i ? "bg-white" : "bg-white/50")} />
            ))}
          </div>
          <span className="absolute right-2 top-2 rounded-full bg-ink/55 px-2 py-0.5 text-[11px] font-semibold text-white">
            {i + 1}/{n}
          </span>
        </>
      )}
    </div>
  );
}

function Ligne({ k, v, note }) {
  return (
    <div className="flex items-baseline justify-between gap-4 border-b border-line py-2 last:border-0">
      <span className="text-[12.5px] font-semibold text-muted">{k}</span>
      <span className="text-right text-[13px] font-bold text-ink">
        {v ?? <span className="font-medium text-faint">non précisé</span>}
        {note && <span className="ml-1.5 text-[11px] font-medium text-faint">{note}</span>}
      </span>
    </div>
  );
}

export default function Detail({ l, onClose }) {
  useEffect(() => {
    const k = (e) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", k);
    document.body.style.overflow = "hidden";
    return () => { document.removeEventListener("keydown", k); document.body.style.overflow = ""; };
  }, [onClose]);

  if (!l) return null;
  const p = precisionDe(l);
  const ch = chambresEstimees(l);
  const carte = osmUrl(l.lat, l.lon);

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center sm:items-center"
      onClick={onClose} role="dialog" aria-modal="true">
      <div className="absolute inset-0 bg-ink/45 backdrop-blur-[3px]" />
      <div onClick={(e) => e.stopPropagation()}
        className={cx(
          "relative flex max-h-[92vh] w-full max-w-2xl flex-col overflow-hidden rounded-t-3xl",
          "border border-line bg-surface shadow-[var(--shadow-lift)] sm:rounded-3xl"
        )}>
        <div className="flex items-start justify-between gap-3 border-b border-line p-4">
          <div className="min-w-0">
            <h2 className="text-[16px] font-extrabold leading-tight text-ink">
              {l.location_label || "Localisation non déterminée"}
            </h2>
            <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
              <Badge tone={p.couleur}>{p.mot}</Badge>
              {l.location_source && (
                <span className="text-[11px] text-faint">
                  source : {l.location_source.replace(/\+/g, " + ")}
                </span>
              )}
              {!l.active && <Badge tone="danger">Retirée des portails</Badge>}
            </div>
          </div>
          <Button variant="ghost" size="sm" onClick={onClose} aria-label="Fermer">✕</Button>
        </div>

        <div className="flex-1 overflow-y-auto p-4">
          <Galerie images={l.images?.length ? l.images : (l.image ? [l.image] : [])} />

          <h3 className="text-[14px] font-bold leading-snug text-ink">{l.title}</h3>

          <div className="mt-4 grid gap-x-8 sm:grid-cols-2">
            <div>
              <p className="mb-1 text-[11px] font-bold uppercase tracking-wider text-faint">Le bien</p>
              <Ligne k="Loyer" v={eur(l.rent)} />
              <Ligne k="Charges" v={l.charges != null ? eur(l.charges) : null} />
              <Ligne k="Surface" v={m2(l.surface)} />
              <Ligne k="Pièces" v={l.rooms} />
              <Ligne k="Chambres" v={ch.n} note={ch.deduit ? "déduit" : null} />
              <Ligne k="Type" v={typeLisible(l.type)} />
            </div>
            <div>
              <p className="mb-1 mt-4 text-[11px] font-bold uppercase tracking-wider text-faint sm:mt-0">
                Tes critères
              </p>
              <Ligne k="Étage" v={l.floor} />
              <Ligne k="Ascenseur" v={l.elevator == null ? null : l.elevator ? "oui" : "non"} />
              <Ligne k="Salle de bain"
                v={l.bathtub == null ? null : l.bathtub ? "baignoire" : "douche"} />
              <Ligne k="Meublé" v={l.furnished == null ? null : l.furnished ? "oui" : "non"} />
              <Ligne k="Commune" v={l.commune} />
              <Ligne k="Quartier" v={l.quartier} />
            </div>
          </div>

          {(l.address || l.street || l.residence || carte) && (
            <div className="mt-4 rounded-2xl border border-line bg-sunken p-3.5">
              <p className="text-[11px] font-bold uppercase tracking-wider text-faint">Localisation</p>
              {l.address && <p className="mt-1.5 text-[13.5px] font-bold text-ink">{l.address}</p>}
              {!l.address && l.street && <p className="mt-1.5 text-[13.5px] font-bold text-ink">{l.street}</p>}
              {l.residence && <p className="text-[12.5px] text-muted">Résidence {l.residence}</p>}
              <p className="mt-1.5 text-[11.5px] leading-relaxed text-muted">
                Niveau retenu : <strong className="text-ink">{p.mot}</strong>. Rien n'est deviné —
                si l'annonce ne donne pas l'adresse, elle n'est pas inventée.
              </p>
              {carte && (
                <a href={carte} target="_blank" rel="noreferrer"
                  className="mt-2 inline-block text-[12.5px] font-bold text-accent hover:underline">
                  Ouvrir sur OpenStreetMap →
                </a>
              )}
            </div>
          )}

          {l.description && (
            <div className="mt-4">
              <p className="mb-1.5 text-[11px] font-bold uppercase tracking-wider text-faint">
                Description {l.detail_read && <span className="text-p5">· page lue en entier</span>}
              </p>
              <p className="whitespace-pre-line text-[13px] leading-relaxed text-muted">
                {l.description}
              </p>
            </div>
          )}

          <div className="mt-4 flex flex-wrap gap-x-4 gap-y-1 text-[11.5px] text-faint">
            <span>Source : <strong className="text-muted">{l.source}</strong></span>
            {l.agency && <span>Annonceur : {l.agency}</span>}
            <span>Vue pour la 1re fois {dateFR(l.seen_first)}</span>
            <span>Dernière vue {ilYA(l.seen_last)}</span>
          </div>
        </div>

        <div className="border-t border-line p-3">
          <a href={l.url} target="_blank" rel="noreferrer"
            className="flex h-11 w-full items-center justify-center rounded-xl bg-accent text-[14px] font-bold text-white transition-[filter] hover:brightness-110">
            Ouvrir l'annonce sur {l.source}
          </a>
        </div>
      </div>
    </div>
  );
}
