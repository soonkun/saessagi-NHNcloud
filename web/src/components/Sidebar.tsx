import { Calendar, FileText, Settings } from "lucide-react";
import { useStore } from "../store";
import type { SidebarView } from "../types";

interface NavItem {
  id: SidebarView;
  label: string;
  icon: React.ReactElement;
}

const NAV_ITEMS: NavItem[] = [
  { id: "calendar", label: "캘린더", icon: <Calendar size={20} /> },
  { id: "documents", label: "문서", icon: <FileText size={20} /> },
  { id: "settings", label: "설정", icon: <Settings size={20} /> },
];

export function Sidebar(): React.ReactElement {
  const activeView = useStore((s) => s.activeView);
  const setActiveView = useStore((s) => s.setActiveView);

  function handleClick(id: SidebarView): void {
    setActiveView(activeView === id ? null : id);
  }

  return (
    <nav
      style={{
        width: 48,
        height: "100%",
        background: "var(--color-sidebar)",
        borderRight: "1px solid var(--color-border)",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        paddingTop: 16,
        gap: 4,
        flexShrink: 0,
        position: "relative",
        zIndex: 10,
      }}
    >
      {NAV_ITEMS.map((item) => {
        const active = activeView === item.id;
        return (
          <div key={item.id} style={{ position: "relative" }} className="group">
            <button
              onClick={() => handleClick(item.id)}
              title={item.label}
              style={{
                width: 36,
                height: 36,
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                borderRadius: 8,
                border: "none",
                background: active ? "var(--color-accent)" : "transparent",
                color: active ? "#fff" : "var(--color-text-muted)",
                cursor: "pointer",
                transition: "background 0.15s, color 0.15s",
              }}
              onMouseEnter={(e) => {
                if (!active) {
                  (e.currentTarget as HTMLButtonElement).style.background =
                    "var(--color-border)";
                  (e.currentTarget as HTMLButtonElement).style.color =
                    "var(--color-text)";
                }
              }}
              onMouseLeave={(e) => {
                if (!active) {
                  (e.currentTarget as HTMLButtonElement).style.background =
                    "transparent";
                  (e.currentTarget as HTMLButtonElement).style.color =
                    "var(--color-text-muted)";
                }
              }}
            >
              {item.icon}
            </button>
            {/* 툴팁 */}
            <div
              style={{
                position: "absolute",
                left: "calc(100% + 8px)",
                top: "50%",
                transform: "translateY(-50%)",
                background: "#111",
                color: "#eee",
                fontSize: 12,
                padding: "4px 8px",
                borderRadius: 6,
                whiteSpace: "nowrap",
                pointerEvents: "none",
                opacity: 0,
                transition: "opacity 0.15s",
                border: "1px solid var(--color-border)",
              }}
              className="sidebar-tooltip"
            >
              {item.label}
            </div>
          </div>
        );
      })}

      <style>{`
        .group:hover .sidebar-tooltip { opacity: 1; }
      `}</style>
    </nav>
  );
}
