import { useState } from "react";
import { Outlet, useLocation } from "react-router-dom";
import { Sidebar } from "./Sidebar";
import { Header } from "./Header";

const titleByPath: Record<string, string> = {
  "/dashboard": "Command Center",
  "/courses": "Courses",
  "/assignments": "Assignments",
  "/documents": "Knowledge Base",
  "/chat": "Conversations",
  "/settings": "Settings",
};

function resolveTitle(pathname: string): string {
  if (titleByPath[pathname]) return titleByPath[pathname];
  if (pathname.startsWith("/courses/")) return "Course Workspace";
  return "Esmerelda";
}

export function AppShell() {
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [collapsed, setCollapsed] = useState(false);
  const location = useLocation();

  return (
    <div className="ambient-field flex min-h-screen">
      <Sidebar
        open={sidebarOpen}
        onClose={() => setSidebarOpen(false)}
        collapsed={collapsed}
        onToggleCollapsed={() => setCollapsed((c) => !c)}
      />

      <div className="flex min-w-0 flex-1 flex-col">
        <Header title={resolveTitle(location.pathname)} onMenuClick={() => setSidebarOpen(true)} />
        <main className="flex-1 px-4 py-6 sm:px-6 lg:px-10">
          <div className="mx-auto w-full max-w-[1280px]">
            <Outlet />
          </div>
        </main>
      </div>
    </div>
  );
}
