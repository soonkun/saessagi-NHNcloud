// Notion 스타일 블록 에디터 (BlockNote) — 데스크톱 모드 노트 편집 전용 (CR-16).
// lazy import 대상이므로 default export. 마운트 시 1회만 마크다운을 블록으로
// 파싱해 로드하고, 이후 변경은 마크다운으로 직렬화해 부모에 전달한다.
// 노트(slug)가 바뀔 때는 부모가 key를 바꿔 리마운트시킨다.
import { useEffect, useRef } from "react";
import { useCreateBlockNote } from "@blocknote/react";
import { BlockNoteView } from "@blocknote/mantine";
import { ko } from "@blocknote/core/locales";
import "@blocknote/core/fonts/inter.css";
import "@blocknote/mantine/style.css";

interface NoteRichEditorProps {
  markdown: string;
  theme: "light" | "dark";
  onChangeMarkdown: (md: string) => void;
}

export default function NoteRichEditor({
  markdown,
  theme,
  onChangeMarkdown,
}: NoteRichEditorProps): React.ReactElement {
  // 슬래시 메뉴·툴바·placeholder 전부 한국어 (BlockNote 내장 ko 로케일)
  const editor = useCreateBlockNote({ dictionary: ko });
  const loadedRef = useRef(false);
  // 초기 replaceBlocks도 onChange를 발화시키므로, 로드 완료 전 변경은 무시해
  // 노트를 열자마자 dirty가 되는 것을 막는다.
  const suppressRef = useRef(true);

  useEffect(() => {
    if (loadedRef.current) return;
    loadedRef.current = true;
    const blocks = editor.tryParseMarkdownToBlocks(markdown);
    editor.replaceBlocks(editor.document, blocks);
    setTimeout(() => {
      suppressRef.current = false;
    }, 0);
  }, [editor, markdown]);

  return (
    <div
      style={{ flex: 1, overflowY: "auto", minHeight: 0 }}
      onClick={() => window.electronAPI?.restoreFocus()}
    >
      <BlockNoteView
        editor={editor}
        theme={theme}
        onChange={() => {
          if (suppressRef.current) return;
          onChangeMarkdown(editor.blocksToMarkdownLossy(editor.document));
        }}
      />
    </div>
  );
}
