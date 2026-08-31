import { NavLink, Outlet, useLocation } from "react-router-dom";
import { FiChevronDown, FiSettings } from "react-icons/fi";

// Batch-scoped workflow, grouped under one "Settlement Error" hover menu.
const settlementItems = [
  { to: "/upload", label: "Upload" },
  { to: "/batches", label: "Batches" },
  { to: "/analytics", label: "Analytics" },
];

// Outside the dropdown: general config (Partner Mapping) and the success-
// side report (Settlement Type Report) -- neither is part of the
// batch/error-audit workflow the dropdown groups, so both stay top-level.
const topLevelItems = [
  { to: "/partner-mapping", label: "Partner Mapping" },
  { to: "/settlement-type", label: "Settlement Type Report" },
];

// Settings sits apart from the rest of the nav: it is app configuration, not
// a view of the data, so it renders as a gear pushed to the far right rather
// than as another labelled destination competing with them.
const SETTINGS_ITEM = { to: "/settings", label: "Settings" };

// Center-anchored underline that grows in on hover -- shared by every nav
// item (the menu button, Partner Mapping, and each item in the dropdown).
// Scale, not width, so it's a GPU-cheap transform rather than a layout
// change.
function HoverUnderline() {
  return (
    <span
      aria-hidden
      className="pointer-events-none absolute left-2 right-2 -bottom-0.5 h-0.5 rounded-full
                 bg-blue-600 scale-x-0 origin-center opacity-0
                 transition-all duration-300 ease-out
                 group-hover:scale-x-100 group-hover:opacity-100"
    />
  );
}

export default function Layout() {
  const location = useLocation();
  const isInSection = settlementItems.some((item) => location.pathname.startsWith(item.to));

  return (
    <div className="min-h-screen bg-neutral-50">
      <nav className="border-b border-neutral-200 bg-white sticky top-0 z-10">
        <div className="max-w-6xl mx-auto px-8 h-14 flex items-center gap-1">
          <span className="font-semibold text-neutral-900 mr-6">SmartQR Ops</span>

          {/* Hover dropdown: submenu sits directly below with padding (not
              margin) so the gap is still part of the hoverable area -- a
              margin gap there would drop group-hover before the mouse
              reaches the submenu. Fades + slides in via opacity/translate
              (not display:none) so the transition can actually animate.

              Named group/menu here (not plain `group`) is load-bearing: this
              div wraps the button AND every dropdown item, and CSS :hover
              bubbles to ancestors. If this were an unnamed `.group` too, it
              would be :hover any time the mouse is over ANY item inside it,
              so every item's plain `group-hover:` underline (they're all
              descendants of this div) would light up together instead of
              just the one actually being hovered. Naming this one keeps its
              own group-hover: (the dropdown panel) from colliding with the
              button's and each item's independent, unnamed group-hover:. */}
          <div className="group/menu relative">
            <button
              type="button"
              className={`group relative px-3 py-1.5 rounded-md text-sm font-medium transition-colors duration-150 inline-flex items-center gap-1.5 cursor-pointer ${
                isInSection ? "bg-neutral-900 text-white" : "text-neutral-500 hover:text-neutral-900"
              }`}
            >
              Settlement Error
              <FiChevronDown className="text-xs transition-transform duration-200 group-hover:rotate-180" />
              <HoverUnderline />
            </button>

            <div
              className="absolute left-0 top-full pt-2 z-20 invisible opacity-0 -translate-y-1.5
                         transition-all duration-200 ease-out
                         group-hover/menu:visible group-hover/menu:opacity-100 group-hover/menu:translate-y-0"
            >
              <div className="flex items-center gap-1 bg-white border border-neutral-200 rounded-lg shadow-lg p-1.5 whitespace-nowrap">
                {settlementItems.map((item) => (
                  <NavLink
                    key={item.to}
                    to={item.to}
                    className={({ isActive }) =>
                      `group relative px-3 py-1.5 rounded-md text-sm font-medium transition-colors ${
                        isActive
                          ? "bg-neutral-900 text-white"
                          : "text-neutral-500 hover:bg-neutral-100 hover:text-neutral-900"
                      }`
                    }
                  >
                    {item.label}
                    <HoverUnderline />
                  </NavLink>
                ))}
              </div>
            </div>
          </div>

          {topLevelItems.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              className={({ isActive }) =>
                `group relative px-3 py-1.5 rounded-md text-sm font-medium transition-colors duration-150 ${
                  isActive ? "bg-neutral-900 text-white" : "text-neutral-500 hover:text-neutral-900"
                }`
              }
            >
              {item.label}
              <HoverUnderline />
            </NavLink>
          ))}

          <NavLink
            to={SETTINGS_ITEM.to}
            title={SETTINGS_ITEM.label}
            aria-label={SETTINGS_ITEM.label}
            className={({ isActive }) =>
              `group relative ml-auto p-2 rounded-md transition-colors duration-150 ${
                isActive
                  ? "bg-neutral-900 text-white"
                  : "text-neutral-400 hover:text-neutral-900 hover:bg-neutral-100"
              }`
            }
          >
            {/* Spins on hover -- the one bit of motion in the nav, so the gear
                reads as interactive without needing a label beside it. */}
            <FiSettings className="text-base transition-transform duration-500 group-hover:rotate-90" />
          </NavLink>
        </div>
      </nav>
      <Outlet />
    </div>
  );
}
