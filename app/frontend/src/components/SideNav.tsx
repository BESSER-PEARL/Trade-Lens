import React from "react";
import { NavLink } from "react-router-dom";
import "./SideNav.css";

// The pages of the app, kept out of sight so the pages keep the whole screen.
// Add a page here and in App.tsx's routes.
const PAGES: Array<{ to: string; end?: boolean; label: string; icon: React.ReactNode }> = [
  {
    to: "/",
    end: true,
    label: "Overview",
    icon: (
      <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
        <rect x="3" y="3" width="8" height="8" rx="1.5" />
        <rect x="13" y="3" width="8" height="5" rx="1.5" />
        <rect x="13" y="10" width="8" height="11" rx="1.5" />
        <rect x="3" y="13" width="8" height="8" rx="1.5" />
      </svg>
    ),
  },
  {
    to: "/trends",
    label: "Trends",
    icon: (
      <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
        <path d="M3 17l5-6 4 4 8-9" />
        <path d="M3 21h18" />
      </svg>
    ),
  },
];

/**
 * On a phone the corner pill steps aside while the reader scrolls down (it would sit on the text) and
 * comes back when they scroll up, stop near the top, or reach the end of the page, where the page
 * leaves room for it.
 */
function useTucked(): boolean {
  const [tucked, setTucked] = React.useState(false);
  React.useEffect(() => {
    let last = window.scrollY;
    const onScroll = () => {
      const y = window.scrollY;
      const atEnd = window.innerHeight + y >= document.documentElement.scrollHeight - 80;
      if (y < 80 || atEnd || y < last - 6) setTucked(false);
      else if (y > last + 6) setTucked(true);
      if (Math.abs(y - last) > 6) last = y;
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);
  return tucked;
}

// Out of sight: it slides in from the left edge (hover or keyboard); a faint corner pill on touch screens.
const SideNav: React.FC = () => {
  const tucked = useTucked();
  return (
    <nav className={"side-nav" + (tucked ? " is-tucked" : "")} aria-label="Pages">
      <ul>
        {PAGES.map((p) => (
          <li key={p.to}>
            <NavLink to={p.to} end={p.end} className={({ isActive }) => "side-nav-link" + (isActive ? " is-active" : "")}>
              {p.icon}
              <span>{p.label}</span>
            </NavLink>
          </li>
        ))}
      </ul>
    </nav>
  );
};

export default SideNav;
