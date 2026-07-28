export const cx = (...a) => a.filter(Boolean).join(" ");

export const eur = (n) =>
  n == null ? null : new Intl.NumberFormat("fr-FR", {
    style: "currency", currency: "EUR", maximumFractionDigits: 0,
  }).format(n);

export const m2 = (n) => (n == null ? null : `${Math.round(n)} m²`);

export const dateFR = (s) => {
  if (!s) return null;
  const d = new Date(s);
  if (Number.isNaN(+d)) return null;
  return d.toLocaleDateString("fr-FR", { day: "2-digit", month: "short", year: "numeric" });
};

export const joursDepuis = (s) => {
  if (!s) return null;
  const d = new Date(s);
  if (Number.isNaN(+d)) return null;
  return Math.floor((Date.now() - +d) / 86400000);
};

export const ilYA = (s) => {
  const j = joursDepuis(s);
  if (j == null) return null;
  if (j <= 0) return "aujourd'hui";
  if (j === 1) return "hier";
  if (j < 7) return `il y a ${j} j`;
  if (j < 31) return `il y a ${Math.floor(j / 7)} sem.`;
  return `il y a ${Math.floor(j / 30)} mois`;
};

/* Précision de localisation : 5 = adresse exacte, 0 = inconnu.
   Chaque niveau a sa couleur ET son mot. On n'affiche jamais un point
   sur une carte sans dire à quel point il est fiable. */
export const PRECISION = {
  5: { cle: "adresse_exacte", mot: "Adresse exacte", court: "Adresse", couleur: "p5" },
  4: { cle: "rue", mot: "Rue identifiée", court: "Rue", couleur: "p4" },
  3: { cle: "residence", mot: "Résidence / point cartographique", court: "Résidence", couleur: "p3" },
  2: { cle: "quartier", mot: "Quartier", court: "Quartier", couleur: "p2" },
  1: { cle: "commune", mot: "Commune seule", court: "Commune", couleur: "p0" },
  0: { cle: "inconnu", mot: "Localisation inconnue", court: "Inconnu", couleur: "p0" },
};

export const precisionDe = (l) => PRECISION[l?.location_precision_rank ?? 0] || PRECISION[0];

export const prixAuM2 = (l) =>
  l.rent && l.surface ? Math.round(l.rent / l.surface) : null;

/* "T3" -> 2 chambres quand bedrooms est absent (89 % des annonces ont rooms,
   38 % seulement ont bedrooms). On le signale comme DEDUIT, jamais comme su. */
export const chambresEstimees = (l) => {
  if (l.bedrooms != null) return { n: l.bedrooms, deduit: false };
  if (l.rooms != null && l.rooms >= 1) return { n: Math.max(l.rooms - 1, 0), deduit: true };
  return { n: null, deduit: false };
};

export const TYPE_LABEL = {
  flat: "Appartement", apartment: "Appartement", house: "Maison",
  villa: "Villa", commercial: "Local commercial", box: "Garage",
  duplex: "Duplex", studio: "Studio", other: "Autre",
};
export const typeLisible = (t) => {
  if (!t) return null;
  const k = String(t).toLowerCase().trim();
  return TYPE_LABEL[k] || t.charAt(0).toUpperCase() + t.slice(1);
};

export const osmUrl = (lat, lon) =>
  lat && lon ? `https://www.openstreetmap.org/?mlat=${lat}&mlon=${lon}#map=16/${lat}/${lon}` : null;

export const norm = (s) =>
  (s || "").toString().normalize("NFD").replace(/[̀-ͯ]/g, "")
    .toLowerCase().replace(/\bst\b/g, "saint").replace(/\bste\b/g, "sainte")
    .replace(/[^a-z0-9]+/g, " ").trim();
