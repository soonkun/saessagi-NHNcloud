

# ── CR-25 큰 주제 단위 청킹 ───────────────────────────────────────────────────


class TestTopicChunking:
    def test_topic_boundaries_split_chunks(self) -> None:
        """번호 제목·□ 등 큰 주제 시작에서 청크가 분리된다 (상한 이내라도)."""
        from document_ingest.segments import chunk_meta_segments

        topic1 = [("1. 연구개발 목표", None)] + [(f"- 목표 항목 {i} " + "내용 " * 30, None) for i in range(4)]
        topic2 = [("2. 연구개발 내용", None)] + [(f"- 내용 항목 {i} " + "본문 " * 30, None) for i in range(4)]
        topic3 = [("□ 기대 효과", None)] + [(f"- 효과 {i} " + "설명 " * 30, None) for i in range(4)]
        chunks = chunk_meta_segments(topic1 + topic2 + topic3, chunk_chars=2000, overlap_chars=0)

        assert len(chunks) == 3, f"주제 3개 → 청크 3개여야 함: {len(chunks)}"
        assert chunks[0][0].startswith("1. 연구개발 목표")
        assert chunks[1][0].startswith("2. 연구개발 내용")
        assert chunks[2][0].startswith("□ 기대 효과")

    def test_sub_bullets_do_not_split(self) -> None:
        """하위 불릿(-·○)은 주제 경계가 아니다 — 한 주제로 병합."""
        from document_ingest.segments import chunk_meta_segments

        segs = [("1. 주제", None)] + [(f"- 불릿 {i} " + "짧은 내용 " * 20, None) for i in range(5)]
        chunks = chunk_meta_segments(segs, chunk_chars=3000, overlap_chars=0)
        assert len(chunks) == 1

    def test_tiny_buffer_ignores_topic_break(self) -> None:
        """연속 제목처럼 버퍼가 작으면(_TOPIC_MIN_CHARS 미만) 경계를 무시하고 병합."""
        from document_ingest.segments import chunk_meta_segments

        segs = [("1. 제목만", None), ("2. 바로 다음 제목", None), ("본문 " * 100, None)]
        chunks = chunk_meta_segments(segs, chunk_chars=2000, overlap_chars=0)
        assert len(chunks) == 1

    def test_chunk_chars_still_caps(self) -> None:
        """주제가 상한을 넘으면 chunk_chars에서 분할된다."""
        from document_ingest.segments import chunk_meta_segments

        segs = [("1. 큰 주제", None)] + [(f"- 항목 {i} " + "내용 " * 80, None) for i in range(10)]
        chunks = chunk_meta_segments(segs, chunk_chars=1000, overlap_chars=0)
        assert len(chunks) > 1
        assert all(len(c[0]) <= 1400 for c in chunks)  # 상한 + 여유
