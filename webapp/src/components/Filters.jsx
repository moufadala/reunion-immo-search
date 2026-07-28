import { Field, Input, Select, Toggle, Button, Badge } from "./ui";
import { cx, norm } from "../lib";

/* Le scoring vit côté serveur (scripts/profils.py) : c'est lui qui sait ce que
   valent Duparc, 2 chambres ou un plain-pied. Ici on ne fait que FILTRER sur
   un profil, jamais le redéfinir — un seul endroit où changer les critères. */
export const FILTRES_VIDES = {
  q: "", commune: "", quartiers: [], types: [], profil: "",
  rentMin: "", rentMax: "", surfaceMin: "", chambresMin: "",
  residentiel: true, actives: true, avecCarte: false, jardin: false,
  precisionMin: 0, tri: "pertinence",
};

export function appliquer(listings, f) {
  const q = norm(f.q);
  return listings.filter((l) => {
    if (f.actives && !l.active) return false;
    if (f.residentiel && l.residential === false) return false;
    if (f.commune && norm(l.commune) !== norm(f.commune)) return false;
    if (f.quartiers.length) {
      const blob = norm(`${l.quartier} ${l.location_label} ${l.title}`);
      if (!f.quartiers.some((z) => blob.includes(norm(z)))) return false;
    }
    if (f.types.length && !f.types.some((t) => norm(l.type).includes(norm(t)))) return false;
    if (f.profil && !l.profils?.[f.profil]) return false;
    if (f.rentMin && (l.rent ?? 0) < +f.rentMin) return false;
    if (f.rentMax && (l.rent ?? 1e9) > +f.rentMax) return false;
    if (f.surfaceMin && (l.surface ?? 0) < +f.surfaceMin) return false;
    if (f.chambresMin) {
      const n = l.bedrooms ?? (l.rooms != null ? l.rooms - 1 : null);
      if (n == null || n < +f.chambresMin) return false;
    }
    if (f.jardin && !l.jardin) return false;
    if (f.avecCarte && !l.lat) return false;
    if (f.precisionMin && (l.location_precision_rank ?? 0) < f.precisionMin) return false;
    if (q) {
      const blob = norm(`${l.title} ${l.location_label} ${l.commune} ${l.description} ${l.agency}`);
      if (!q.split(" ").every((mot) => blob.includes(mot))) return false;
    }
    return true;
  });
}

/* Tri par défaut voulu le 27/07 : d'abord les annonces FRAÎCHES qui
   correspondent à un profil, puis le reste des fraîches, puis tout le reste
   par score. C'est un classement souple : un très bon score en zone 5 peut
   passer devant un score moyen en zone 1. */
export function trier(rows, tri, profil) {
  const sc = (l) => (profil ? l.profils?.[profil]?.score ?? 0 : l.meilleur_score ?? 0);
  const c = [...rows];
  const S = {
    pertinence: (a, b) => {
      const fa = a.fraiche && a.active ? 1 : 0;
      const fb = b.fraiche && b.active ? 1 : 0;
      const pa = fa && sc(a) >= 45 ? 2 : fa ? 1 : 0;
      const pb = fb && sc(b) >= 45 ? 2 : fb ? 1 : 0;
      return pb - pa || sc(b) - sc(a) ||
        (b.seen_first || "").localeCompare(a.seen_first || "");
    },
    score: (a, b) => sc(b) - sc(a),
    recent: (a, b) => (b.seen_first || "").localeCompare(a.seen_first || ""),
    prixAsc: (a, b) => (a.rent ?? 1e9) - (b.rent ?? 1e9),
    prixDesc: (a, b) => (b.rent ?? -1) - (a.rent ?? -1),
    surface: (a, b) => (b.surface ?? -1) - (a.surface ?? -1),
    trajet: (a, b) => (a.trajet?.minutes ?? 1e6) - (b.trajet?.minutes ?? 1e6),
    precision: (a, b) =>
      (b.location_precision_rank ?? 0) - (a.location_precision_rank ?? 0),
  };
  return c.sort(S[tri] || S.pertinence);
}

export default function Filters({ f, set, communes, quartiers, types, nb, total, onReset }) {
  const up = (k) => (e) => set({ ...f, [k]: e.target.value });
  const tog = (k) => (v) => set({ ...f, [k]: v });
  const toggleListe = (k, v) =>
    set({ ...f, [k]: f[k].includes(v) ? f[k].filter((x) => x !== v) : [...f[k], v] });

  return (
    <div className="flex flex-col gap-5">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
        <div className="col-span-2 sm:col-span-3 lg:col-span-4">
          <Field label="Recherche libre">
            <Input value={f.q} onChange={up("q")}
              placeholder="Ex : T3 Beauséjour terrasse — cherche aussi dans la description complète" />
          </Field>
        </div>
        <Field label="Commune">
          <Select value={f.commune} onChange={up("commune")}>
            <option value="">Les 4 communes</option>
            {communes.map((c) => <option key={c} value={c}>{c}</option>)}
          </Select>
        </Field>
        <Field label="Loyer min">
          <Input type="number" inputMode="numeric" value={f.rentMin} onChange={up("rentMin")} placeholder="€" />
        </Field>
        <Field label="Loyer max">
          <Input type="number" inputMode="numeric" value={f.rentMax} onChange={up("rentMax")} placeholder="€" />
        </Field>
        <Field label="Surface min">
          <Input type="number" inputMode="numeric" value={f.surfaceMin} onChange={up("surfaceMin")} placeholder="m²" />
        </Field>
        <Field label="Chambres min" hint="déduit des pièces si absent">
          <Input type="number" inputMode="numeric" value={f.chambresMin} onChange={up("chambresMin")} placeholder="ex : 2" />
        </Field>
        <Field label="Précision de localisation">
          <Select value={f.precisionMin} onChange={(e) => set({ ...f, precisionMin: +e.target.value })}>
            <option value={0}>Toutes</option>
            <option value={2}>Quartier au minimum</option>
            <option value={3}>Résidence / point carte</option>
            <option value={4}>Rue ou adresse</option>
            <option value={5}>Adresse exacte seulement</option>
          </Select>
        </Field>
        <Field label="Trier par">
          <Select value={f.tri} onChange={up("tri")}>
            <option value="pertinence">Fraîches + mes critères</option>
            <option value="score">Meilleur score</option>
            <option value="recent">Plus récentes</option>
            <option value="trajet">Plus proches en voiture</option>
            <option value="prixAsc">Loyer croissant</option>
            <option value="prixDesc">Loyer décroissant</option>
            <option value="surface">Plus grandes</option>
            <option value="precision">Mieux localisées</option>
          </Select>
        </Field>
      </div>

      {quartiers.length > 0 && (
        <div className="flex flex-col gap-2">
          <span className="text-[11px] font-bold uppercase tracking-wider text-faint">Quartiers</span>
          <div className="flex flex-wrap gap-1.5">
            {quartiers.map((z) => (
              <button key={z} onClick={() => toggleListe("quartiers", z)}
                className={cx(
                  "rounded-full border px-2.5 py-1 text-[12px] font-semibold transition-colors cursor-pointer",
                  f.quartiers.includes(z)
                    ? "border-accent bg-accent-soft text-accent-ink"
                    : "border-line bg-surface text-muted hover:bg-sunken"
                )}>
                {z}
              </button>
            ))}
          </div>
        </div>
      )}

      <div className="flex flex-col gap-2">
        <span className="text-[11px] font-bold uppercase tracking-wider text-faint">
          Affiner — le confort et l'accès servent au classement, pas à exclure
        </span>
        <div className="flex flex-wrap gap-2">
          <Toggle checked={f.jardin} onChange={tog("jardin")}>Avec jardin</Toggle>
          <Toggle checked={f.avecCarte} onChange={tog("avecCarte")}>Localisée sur carte</Toggle>
          <Toggle checked={f.residentiel} onChange={tog("residentiel")}>Habitation seulement</Toggle>
          <Toggle checked={f.actives} onChange={tog("actives")}>En ligne aujourd'hui</Toggle>
        </div>
      </div>

      <div className="flex items-center justify-between gap-3 border-t border-line pt-3">
        <p className="text-[13px] text-muted">
          <strong className="text-ink tabular-nums">{nb}</strong> annonce{nb > 1 ? "s" : ""}
          <span className="text-faint"> sur {total}</span>
        </p>
        <Button variant="ghost" size="sm" onClick={onReset}>Réinitialiser</Button>
      </div>
    </div>
  );
}
