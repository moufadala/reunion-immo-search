import { useEffect, useRef, useState } from "react";
import { cx } from "../lib";

/* Primitives dans les conventions shadcn (mêmes noms, mêmes props),
   pour qu'un composant copié depuis 21st.dev s'y branche sans friction. */

export function Badge({ children, tone = "neutral", className, title }) {
  const tones = {
    neutral: "bg-sunken text-muted border-line",
    accent: "bg-accent-soft text-accent-ink border-transparent",
    p5: "bg-p5/12 text-p5 border-p5/25",
    p4: "bg-p4/12 text-p4 border-p4/25",
    p3: "bg-p3/12 text-p3 border-p3/25",
    p2: "bg-p2/14 text-p2 border-p2/28",
    p0: "bg-p0/12 text-p0 border-p0/22",
    danger: "bg-danger/10 text-danger border-danger/25",
    warn: "bg-warn/14 text-warn border-warn/28",
  };
  return (
    <span
      title={title}
      className={cx(
        "inline-flex items-center gap-1 rounded-full border px-2 py-[3px]",
        "text-[11px] font-semibold leading-none whitespace-nowrap",
        tones[tone] || tones.neutral,
        className
      )}
    >
      {children}
    </span>
  );
}

export function Button({ variant = "default", size = "md", className, ...p }) {
  const variants = {
    default: "bg-accent text-white hover:brightness-110 border-transparent",
    outline: "bg-surface text-ink border-line hover:bg-sunken",
    ghost: "bg-transparent text-muted border-transparent hover:bg-sunken hover:text-ink",
    soft: "bg-accent-soft text-accent-ink border-transparent hover:brightness-95",
  };
  const sizes = { sm: "h-8 px-3 text-[13px]", md: "h-10 px-4 text-sm", lg: "h-11 px-5 text-[15px]" };
  return (
    <button
      {...p}
      className={cx(
        "inline-flex items-center justify-center gap-2 rounded-xl border font-semibold",
        "transition-[background,filter,transform] active:scale-[0.98] disabled:opacity-45",
        "disabled:pointer-events-none cursor-pointer",
        variants[variant], sizes[size], className
      )}
    />
  );
}

export function Field({ label, hint, children }) {
  return (
    <label className="flex flex-col gap-1.5">
      <span className="text-[11px] font-bold uppercase tracking-wider text-faint">{label}</span>
      {children}
      {hint && <span className="text-[11px] text-faint">{hint}</span>}
    </label>
  );
}

const inputBase =
  "h-10 w-full rounded-xl border border-line bg-surface px-3 text-sm text-ink " +
  "placeholder:text-faint outline-none transition-colors focus:border-accent";

export const Input = (p) => <input {...p} className={cx(inputBase, p.className)} />;
export const Select = ({ children, ...p }) => (
  <select {...p} className={cx(inputBase, "cursor-pointer pr-8", p.className)}>{children}</select>
);

export function Toggle({ checked, onChange, children }) {
  return (
    <button
      type="button"
      onClick={() => onChange(!checked)}
      aria-pressed={checked}
      className={cx(
        "inline-flex items-center gap-2 rounded-xl border px-3 py-2 text-[13px] font-semibold",
        "transition-colors cursor-pointer",
        checked
          ? "border-accent bg-accent-soft text-accent-ink"
          : "border-line bg-surface text-muted hover:bg-sunken"
      )}
    >
      <span
        className={cx(
          "grid h-4 w-4 place-items-center rounded-[5px] border transition-colors",
          checked ? "border-accent bg-accent text-white" : "border-line"
        )}
      >
        {checked && (
          <svg viewBox="0 0 12 12" className="h-3 w-3" aria-hidden>
            <path d="M2.5 6.2l2.3 2.3 4.7-5" fill="none" stroke="currentColor"
              strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        )}
      </span>
      {children}
    </button>
  );
}

/* Stat lisible : un nombre, un mot, et — si utile — d'où il sort.
   Pas de jauge décorative : le chiffre EST la donnée. */
export function Stat({ value, label, sub, tone }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className={cx("text-[26px] font-extrabold leading-none tabular-nums tracking-tight",
        tone === "accent" ? "text-accent" : "text-ink")}>
        {value}
      </span>
      <span className="text-[12.5px] font-semibold text-ink/80">{label}</span>
      {sub && <span className="text-[11.5px] leading-snug text-faint">{sub}</span>}
    </div>
  );
}

/* Une apparition au scroll ne doit JAMAIS pouvoir laisser du contenu invisible.
   Prouvé le 27/07 : dans un onglet en arrière-plan, Chrome gèle les transitions
   et l'IntersectionObserver ne se déclenche pas — la page reste blanche.
   D'où le filet : au bout de 1,2 s, on affiche quoi qu'il arrive. */
export function Reveal({ children, className, delay = 0, ...props }) {
  const ref = useRef(null);
  const [vu, setVu] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el || typeof IntersectionObserver !== "function") { setVu(true); return; }
    const filet = setTimeout(() => setVu(true), 1200);
    const io = new IntersectionObserver(
      ([e]) => { if (e.isIntersecting) { setVu(true); io.disconnect(); } },
      { rootMargin: "0px 0px -8% 0px" }
    );
    io.observe(el);
    return () => { clearTimeout(filet); io.disconnect(); };
  }, []);
  return (
    <div {...props} ref={ref} style={{ transitionDelay: `${delay}ms` }}
      className={cx("reveal", vu && "in", className)}>
      {children}
    </div>
  );
}

export function Empty({ titre, texte, children }) {
  return (
    <div className="rounded-2xl border border-dashed border-line bg-surface/60 px-6 py-14 text-center">
      <p className="text-[15px] font-bold text-ink">{titre}</p>
      {texte && <p className="mx-auto mt-1.5 max-w-md text-[13.5px] leading-relaxed text-muted">{texte}</p>}
      {children && <div className="mt-4 flex justify-center gap-2">{children}</div>}
    </div>
  );
}
