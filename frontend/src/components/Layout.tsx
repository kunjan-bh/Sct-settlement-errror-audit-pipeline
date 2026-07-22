import { NavLink, Outlet } from "react-router-dom";

const navItems = [
  { to: "/upload", label: "Upload" },
  { to: "/batches", label: "Batches" },
  { to: "/partner-mapping", label: "Partner Mapping" },
];

export default function Layout() {
  return (
    <div className="min-h-screen bg-neutral-50">
      <nav className="border-b border-neutral-200 bg-white sticky top-0 z-10">
        <div className="max-w-6xl mx-auto px-8 h-14 flex items-center gap-1">
          <span className="font-semibold text-neutral-900 mr-6">SmartQR Ops</span>
          {navItems.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              className={({ isActive }) =>
                `px-3 py-1.5 rounded-md text-sm font-medium transition-colors ${
                  isActive ? "bg-neutral-900 text-white" : "text-neutral-500 hover:text-neutral-900"
                }`
              }
            >
              {item.label}
            </NavLink>
          ))}
        </div>
      </nav>
      <Outlet />
    </div>
  );
}
