type FooterLink = { label: string; href: string };
type FooterColumn = { title: string; links: FooterLink[] };

const defaultColumns: FooterColumn[] = [
  {
    title: "Product",
    links: [
      { label: "Live inspection", href: "/inspect" },
      { label: "Review a past call", href: "/past" },
      { label: "Check a caller", href: "/check" },
    ],
  },
  {
    title: "Explore",
    links: [
      { label: "How it works", href: "#how-it-works" },
      { label: "Privacy", href: "#privacy" },
      { label: "Log in", href: "/auth" },
    ],
  },
];

const socials = [
  {
    label: "LinkedIn",
    href: "#",
    icon: (
      <svg viewBox="0 0 24 24" className="h-4 w-4" fill="currentColor" aria-hidden="true">
        <path d="M20.447 20.452h-3.554v-5.569c0-1.328-.027-3.037-1.852-3.037-1.853 0-2.136 1.445-2.136 2.939v5.667H9.351V9h3.414v1.561h.046c.477-.9 1.637-1.85 3.37-1.85 3.601 0 4.267 2.37 4.267 5.455v6.286zM5.337 7.433a2.062 2.062 0 01-2.063-2.065 2.064 2.064 0 112.063 2.065zm1.782 13.019H3.555V9h3.564v11.452zM22.225 0H1.771C.792 0 0 .774 0 1.729v20.542C0 23.227.792 24 1.771 24h20.451C23.2 24 24 23.227 24 22.271V1.729C24 .774 23.2 0 22.222 0h.003z" />
      </svg>
    ),
  },
  {
    label: "Instagram",
    href: "#",
    icon: (
      <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
        <rect x="3" y="3" width="18" height="18" rx="5" />
        <circle cx="12" cy="12" r="4" />
        <circle cx="17.5" cy="6.5" r="1" fill="currentColor" stroke="none" />
      </svg>
    ),
  },
  {
    label: "X",
    href: "#",
    icon: (
      <svg viewBox="0 0 24 24" className="h-4 w-4" fill="currentColor" aria-hidden="true">
        <path d="M18.244 2.25h3.308l-7.227 8.26 8.502 11.24H16.17l-5.214-6.817L4.99 21.75H1.68l7.73-8.835L1.254 2.25H8.08l4.713 6.231zm-1.161 17.52h1.833L7.084 4.126H5.117z" />
      </svg>
    ),
  },
];

type SiteFooterProps = {
  brand?: string;
  columns?: FooterColumn[];
  className?: string;
};

export function SiteFooter({
  brand = "Sentry",
  columns = defaultColumns,
  className = "",
}: SiteFooterProps) {
  const year = new Date().getFullYear();

  return (
    <footer className={`wl-site-footer relative border-t border-[#334155] bg-[#0a0a0a] text-white ${className}`}>
      <div className="mx-auto max-w-7xl px-6 pt-16 md:px-12">
        <div className="flex flex-col gap-12 md:flex-row md:justify-between">
          <div>
            <p className="text-sm font-semibold">{brand}</p>
            <p className="mt-6 text-sm text-[#94A3B8]">© {year} {brand}</p>
            <p className="mt-2 text-sm text-[#94A3B8]">All rights reserved.</p>

            <div className="mt-6 flex items-center gap-3">
              {socials.map((social) => (
                <a
                  key={social.label}
                  href={social.href}
                  aria-label={social.label}
                  onClick={(event) => event.preventDefault()}
                  className="flex h-9 w-9 items-center justify-center rounded-full border border-[#334155] text-[#94A3B8] transition-colors hover:border-[#CC8066] hover:text-[#CC8066]"
                >
                  {social.icon}
                </a>
              ))}
            </div>
          </div>

          <nav aria-label="Footer" className="grid grid-cols-2 gap-x-12 gap-y-10 sm:grid-cols-2">
            {columns.map((column) => (
              <div key={column.title}>
                <h3 className="text-sm font-semibold">{column.title}</h3>
                <ul className="mt-4 space-y-3">
                  {column.links.map((link) => (
                    <li key={link.label}>
                      <a
                        href={link.href}
                        className="text-sm text-[#94A3B8] transition-colors hover:text-[#CC8066]"
                      >
                        {link.label}
                      </a>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </nav>
        </div>
      </div>

      <div
        aria-hidden="true"
        className="wl-site-wordmark pointer-events-none mt-16 select-none text-center font-bold leading-[0.85]"
        style={{
          fontSize: "clamp(4.5rem, 18vw, 16rem)",
          color: "transparent",
          WebkitTextStroke: "1px #334155",
          filter: "drop-shadow(0 0 6px rgba(204,128,102,0.18))",
        }}
      >
        {brand}
      </div>
    </footer>
  );
}

export default SiteFooter;
