import type { ReactNode } from "react";

export type SiteHeaderLink = {
  label: string;
  href: string;
  style?: "text" | "login" | "button";
};

type SiteHeaderProps = {
  links: SiteHeaderLink[];
  className?: string;
  brand?: ReactNode;
  context?: ReactNode;
};

export default function SiteHeader({ links, className = "", brand, context }: SiteHeaderProps) {
  return (
    <header className={`wl-nav-wrap ${className}`}>
      <nav className="wl-nav" aria-label="Main navigation">
        <div className="wl-header-brand-row">
        {brand ?? (
          <a className="wl-brand" href="/" aria-label="Sentry home">
            <span className="wl-brand-mark">S</span>
            <span>Sentry<span className="wl-brand-period">.</span></span>
          </a>
        )}
        {context && <div className="wl-header-context">{context}</div>}
        </div>
        <div className="wl-nav-links">
          {links.map((link) => (
            <a key={`${link.label}-${link.href}`} className={link.style === "button" ? "wl-nav-cta" : link.style === "login" ? "wl-nav-login" : "wl-nav-item"} href={link.href}>
              {link.label}
            </a>
          ))}
        </div>
      </nav>
    </header>
  );
}
