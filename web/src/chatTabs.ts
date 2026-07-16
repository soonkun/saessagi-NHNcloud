import type { ElementType } from "react";
import {
  MessageCircle,
  Calendar,
  BookOpen,
  FolderOpen,
  Network,
  Telescope,
  FileAudio,
  Settings,
} from "lucide-react";
import type { ChatTab } from "./types";

// 펫/데스크톱 두 모드의 탭 메뉴 단일 소스.
// 한쪽 목록에만 탭을 추가해 두 모드 UI가 어긋나는 회귀 방지 — 새 탭은 여기에만 추가한다.
export interface ChatTabDef {
  id: ChatTab;
  petLabel: string;
  desktopLabel: string;
  Icon: ElementType;
}

export const CHAT_TABS: ChatTabDef[] = [
  { id: "chat", petLabel: "새싹이", desktopLabel: "새싹이", Icon: MessageCircle },
  { id: "calendar", petLabel: "일정표", desktopLabel: "일정표", Icon: Calendar },
  { id: "notes", petLabel: "노트", desktopLabel: "업무 노트", Icon: BookOpen },
  { id: "documents", petLabel: "문서", desktopLabel: "문서", Icon: FolderOpen },
  { id: "graph", petLabel: "그래프", desktopLabel: "지식그래프", Icon: Network },
  { id: "research", petLabel: "리서치", desktopLabel: "딥 리서치", Icon: Telescope },
  { id: "meeting", petLabel: "회의록", desktopLabel: "회의록", Icon: FileAudio },
  { id: "settings", petLabel: "설정", desktopLabel: "설정", Icon: Settings },
];
