import { NavLink } from "react-router-dom";
import {
  Radar,
  BookOpen,
  ClipboardList,
  Database,
  MessagesSquare,
  Settings,
  PanelLeftClose,
  PanelLeftOpen,
  X,
} from "lucide-react";
import { cn } from "@/lib/utils";

const navItems = [
  { to: "/dashboard", label: "Command Center", icon: Radar },
  { to: "/courses", label: "Courses", icon: BookOpen },
  { to: "/assignments", label: "Assignments", icon: ClipboardList },
  { to: "/documents", label: "Knowledge Base", icon: Database },
  { to: "/chat", label: "Conversations", icon: MessagesSquare },
  { to: "/settings", label: "Settings", icon: Settings },
];

interface SidebarProps {
  open: boolean;
  onClose: () => void;
  collapsed: boolean;
  onToggleCollapsed: () => void;
}

export function Sidebar({ open, onClose, collapsed, onToggleCollapsed }: SidebarProps) {
  return (
    <>
      {open && (
        <div
          className="fixed inset-0 z-30 bg-black/70 backdrop-blur-sm lg:hidden"
          onClick={onClose}
          aria-hidden="true"
        />
      )}

      <aside
        className={cn(
          "fixed inset-y-0 left-0 z-40 flex flex-col border-r border-graphite-700/70 bg-graphite-900/95 py-5 transition-[width,transform] duration-300 ease-out lg:static lg:z-auto lg:translate-x-0 lg:bg-graphite-900/60",
          collapsed ? "w-[76px] px-3" : "w-64 px-4",
          open ? "translate-x-0" : "-translate-x-full"
        )}
        aria-label="Primary"
      >
        <div className={cn("flex items-center", collapsed ? "justify-center" : "justify-between px-1")}>
          <div className="flex items-center gap-2.5 overflow-hidden">
            <div
              className="flex size-8 shrink-0 items-center justify-center rounded-full"
              style={{
                background: "radial-gradient(circle, var(--color-cyan-400), var(--color-violet-600))",
                boxShadow: "0 0 16px color-mix(in oklab, var(--color-cyan-400) 55%, transparent)",
              }}
            >
              <span className="size-2 rounded-full bg-graphite-950" />
            </div>
            {!collapsed && (
              <span className="truncate font-display text-[15px] font-semibold tracking-tight text-warm-50">
                Esmerelda
              </span>
            )}
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close sidebar"
            className="rounded-lg p-1.5 text-graphite-300 hover:bg-graphite-700/60 hover:text-warm-50 lg:hidden"
          >
            <X size={18} />
          </button>
        </div>

        <nav className="mt-8 flex flex-1 flex-col gap-1">
          {navItems.map(({ to, label, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              onClick={onClose}
              title={collapsed ? label : undefined}
              className={({ isActive }) =>
                cn(
                  "group relative flex items-center gap-3 rounded-xl px-3 py-2.5 text-sm font-medium transition-colors",
                  collapsed && "justify-center px-0",
                  isActive
                    ? "bg-cyan-500/10 text-cyan-300"
                    : "text-graphite-300 hover:bg-graphite-700/50 hover:text-warm-50"
                )
              }
              end={to === "/dashboard"}
            >
              {({ isActive }) => (
                <>
                  {isActive && (
                    <span
                      className="absolute left-0 top-1/2 h-5 w-[2.5px] -translate-y-1/2 rounded-full"
                      style={{
                        background: "var(--color-cyan-400)",
                        boxShadow: "0 0 10px color-mix(in oklab, var(--color-cyan-400) 80%, transparent)",
                      }}
                    />
                  )}
                  <Icon size={17} strokeWidth={1.85} className="shrink-0" />
                  {!collapsed && <span className="truncate">{label}</span>}
                  {collapsed && (
                    <span className="pointer-events-none absolute left-full ml-3 whitespace-nowrap rounded-lg border border-graphite-600 bg-graphite-800 px-2.5 py-1.5 text-xs font-medium text-warm-50 opacity-0 shadow-lg transition-opacity duration-150 group-hover:opacity-100 z-50">
                      {label}
                    </span>
                  )}
                </>
              )}
            </NavLink>
          ))}
        </nav>

        <button
          type="button"
          onClick={onToggleCollapsed}
          className={cn(
            "hidden items-center gap-2 rounded-xl px-3 py-2.5 text-xs font-medium text-graphite-400 transition-colors hover:bg-graphite-700/50 hover:text-warm-50 lg:flex",
            collapsed && "justify-center px-0"
          )}
          aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
        >
          {collapsed ? <PanelLeftOpen size={17} /> : <PanelLeftClose size={17} />}
          {!collapsed && "Collapse"}
        </button>

        {!collapsed && (
          <div className="mt-4 rounded-xl border border-graphite-700/70 bg-graphite-850/80 p-3.5">
            <p className="text-xs font-medium text-graphite-200">Signed in</p>
            <p className="mt-0.5 truncate text-xs text-graphite-400">student.design1@flame.edu.in</p>
          </div>
        )}
      </aside>
    </>
  );
}
