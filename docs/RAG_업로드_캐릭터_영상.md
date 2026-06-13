# RAG 등록 캐릭터 영상 — 사용 매뉴얼

> RAG 문서 등록(임베딩) 중 펫 캐릭터 자리에 "포털에 책을 넣는 새싹이" 투명 영상을 재생하는 기능.
> 관련 커밋: `509d296 feat(ui): RAG 문서 등록 중 펫 캐릭터에 투명 영상 재생`

---

## 1. 기능 개요

문서를 RAG에 등록(임베딩)하는 동안, 평소의 정사각 캐릭터 아이콘 대신 **책장 포털에 책을 집어넣는 애니메이션 영상**이 같은 자리에 재생된다. 등록이 끝나면 평소 모습으로 돌아온다.

- **포맷**: 알파(투명) 채널 포함 **VP8 WebM** (`web/public/avatars/uploading.webm`)
- **비율**: 16:9 원본 비율 그대로. 세로를 캐릭터 크기(`charSize`)에 맞추고 가로는 중앙이 아니라 **오른쪽 변 기준**으로 정렬 → 채팅 패널 오른쪽 단과 정확히 일치
- **트리거**: 캐릭터 감정 상태가 `uploading`이 될 때

---

## 2. 사용자 매뉴얼 (앱에서 보는 방법)

### 펫 모드에서 (영상이 보이는 경로)
1. 바탕화면에 떠 있는 **새싹이 캐릭터를 클릭**해 채팅 창을 연다.
2. 채팅에 **문서 파일을 첨부**한다 (드래그앤드롭 또는 첨부 버튼). 첨부한 문서는 "업무노트" 폴더로 자동 분류된다.
3. 업로드/임베딩이 진행되는 동안 캐릭터 자리에 **포털 영상**이 투명 배경으로 재생된다.
4. 등록이 끝나면 자동으로 평소 캐릭터로 복귀한다.
   - 여러 파일을 동시에 올리면 **마지막 파일까지 끝난 뒤** 복귀한다(중간에 깜빡이지 않음).

### 창(데스크톱) 모드에서
- 문서 탭에서 업로드할 때도 내부적으로 `uploading` 상태가 되지만, 창 모드에는 떠 있는 펫 캐릭터가 없어(작은 헤더 아바타만 있음) **이 영상은 펫 모드에서만 보인다.**

---

## 3. 영상 교체 / 업데이트 방법 ★중요★

> 새 영상으로 바꾸려면 **`web/public/avatars/uploading.webm` 파일만 교체**하면 된다(코드 수정 불필요).
> 단, 반드시 **알파 채널이 있는 VP8 WebM**이어야 한다. 아래 절차를 따를 것.

### 3-1. 가장 깔끔한 방법 — 알파 포함 원본을 받아서 변환

영상 제작 단계에서 **배경이 진짜 투명한 파일**로 받는다:
- MOV (ProRes 4444 / HEVC with alpha) ← 권장
- WebM (VP8/VP9 with alpha)
- 투명 PNG 시퀀스

그 다음 키잉 없이 VP8 알파 WebM으로 변환만 한다:

```bash
ffmpeg -y -i 원본.mov -an \
  -vf "scale=960:-2,format=yuva420p" \
  -c:v libvpx -pix_fmt yuva420p -auto-alt-ref 0 -b:v 1800k \
  web/public/avatars/uploading.webm
```

### 3-2. 차선책 — 단색 배경 영상에서 키잉(누끼)

알파 없는 단색 배경 영상밖에 없을 때만. 배경색을 키로 제거한다.

```bash
# 배경색을 먼저 코너 픽셀에서 확인 (예: 검정 0x000000, 회색 0x9e9e9f)
# colorkey=색:similarity:blend — similarity가 클수록 많이 지움(캐릭터 잠식 위험)
ffmpeg -y -i 원본.mp4 -an \
  -vf "colorkey=0x000000:0.12:0.06,scale=960:-2,format=yuva420p" \
  -c:v libvpx -pix_fmt yuva420p -auto-alt-ref 0 -b:v 1500k \
  web/public/avatars/uploading.webm
```

**키잉의 한계 (실제로 겪은 문제):**
- 배경이 **그라데이션**이거나, **캐릭터 그늘 색이 배경색과 비슷**하면 색만으로 분리가 불가능하다.
  similarity를 좁히면 배경이 남고, 넓히면 **캐릭터 몸통 안쪽이 같이 투명해진다.**
- 캐릭터 내부에 배경색과 같은 영역이 있으면 그 부분도 뚫린다.
- → 키잉은 어디까지나 임시방편. **가능하면 3-1(알파 원본)으로 받을 것.**

### 3-3. 변환 후 반드시 검증

`pix_fmt`만 보면 안 된다(VP9/HEVC 알파는 ffmpeg 기본 디코더가 못 읽어 `yuv420p`로 표시됨). **알파를 직접 추출/합성해서 눈으로 확인**한다:

```bash
# (1) 알파 플레인 추출 — libvpx 디코더 강제. 캐릭터=흰색(불투명), 배경=검정(투명)이어야 정상.
#     특히 캐릭터 몸통 안쪽이 '구멍 없이 꽉 찬 흰색'인지 볼 것.
ffmpeg -y -c:v libvpx -ss 3 -i web/public/avatars/uploading.webm \
  -vf "alphaextract" -frames:v 1 /tmp/mask.png

# (2) 마젠타 배경 합성 — 마젠타가 비치면 투명 성공, 검정이면 알파 누락.
ffmpeg -y -f lavfi -i color=c=magenta:s=960x540 \
  -c:v libvpx -ss 3 -i web/public/avatars/uploading.webm \
  -filter_complex "[0:v][1:v]overlay=shortest=1" -frames:v 1 /tmp/check.png
```

### 3-4. 빌드 & 실행

```bash
cd web && ELECTRON_BUILD=1 npm run build   # ELECTRON_BUILD=1 필수 (흰 화면 사고 방지)
# 그 다음 새싹이.command 로 앱 실행
```

---

## 4. 흔한 함정 (반드시 기억)

| 증상 | 원인 | 해결 |
|------|------|------|
| 키잉했는데 앱에선 검은 박스 | **MP4(H.264/HEVC)는 알파를 못 담음.** 투명 영역이 export 시 단색으로 합쳐짐 | WebM/MOV(알파) 또는 키잉 후 VP8 WebM |
| webm 만들었는데 `pix_fmt=yuv420p`로 나옴 | ffmpeg **기본 디코더가 webm 알파를 못 읽음** (인코딩은 됐을 수 있음) | `-c:v libvpx` 디코더로 `alphaextract`/합성 검증 |
| VP9로 인코딩하니 알파 안 실림 | 이 ffmpeg 빌드의 libvpx-vp9 알파 이슈 | **VP8(`-c:v libvpx`)** 사용 |
| 캐릭터 몸통이 부분 투명 | 키잉 similarity 과다 또는 몸통-배경 색 충돌 | similarity 낮추거나, 알파 원본 사용 |
| 영상 오른쪽이 패널과 안 맞음 | 중앙 정렬됨 | `CharacterWidget`에서 `right:0` 정렬 유지 (아래 5번) |

---

## 5. 기술 동작 원리 (개발자용)

- **렌더링**: `web/src/components/CharacterWidget.tsx`
  - `displayEmotion === "uploading"` 이고 영상 로드 실패가 아니면 `<img>` 대신 `<video autoPlay loop muted playsInline>` 렌더.
  - 스타일: `position:absolute; top:0; right:0; height:100%; width:auto`. 정사각 박스의 오른쪽 변에 영상 오른쪽 변을 맞춤. 박스를 넘는 가로폭은 왼쪽으로 오버플로(부모에 `overflow:hidden` 없음). 드래그/클릭 영역은 정사각 그대로 유지(`pointerEvents:none`).
  - 영상 로드 실패 시 `videoFailed` 플래그로 `uploading.png` 폴백.
  - **정렬 근거**: 채팅 패널 오른쪽 단 = `charPos.x + charSize` (`ChatPanel.tsx`의 `calcPanelStyle`), 박스 오른쪽 변도 동일 좌표 → `right:0`이면 두 오른쪽 단이 일치.

- **트리거**: `web/src/components/ChatPanel.tsx` `uploadOneFile()`
  - 업로드 시작 시 `setEmotion("uploading")`, 동시 업로드 카운터(`activeUploadsRef`)로 **마지막 업로드 완료 시에만** `neutral` 복귀.
  - `DocumentsView`(창 모드)도 `setEmotion("uploading")`을 호출하지만 그 화면엔 펫 캐릭터가 없어 영상은 안 보인다.

- **에셋 비율**: 영상은 16:9가 아니어도 됨(현재 원본은 1550×1080 등). `height:100%, width:auto`라 어떤 비율이든 세로에 맞춰 표시되고 오른쪽 정렬은 유지된다.

---

## 6. 활용 예시 (시나리오)

### 예시 1 — 회의 자료를 채팅으로 등록
1. 펫 모드에서 새싹이 클릭 → 채팅 열기
2. 방금 받은 회의 자료 PDF를 채팅 입력창에 드래그앤드롭
3. 포털 영상이 재생되며 "업무노트" 폴더로 임베딩됨
4. 등록 완료 후 "방금 올린 회의 자료에서 결정사항만 뽑아줘" 라고 질문 → RAG가 해당 문서를 근거로 답변

### 예시 2 — 여러 문서 한꺼번에 등록
1. 분기 보고서 3개 파일을 한 번에 드래그
2. 영상이 끊김 없이 재생(중간에 평소 모습으로 깜빡이지 않음)
3. 3개 모두 임베딩이 끝나면 한 번에 평소 캐릭터로 복귀

### 예시 3 — 등록 진행을 시각적으로 인지
- 영상이 떠 있는 동안 = "지금 새싹이가 문서를 책장에 넣는 중" → 사용자는 등록이 진행 중임을 직관적으로 알 수 있고, 영상이 사라지면 완료 신호로 받아들인다.
- 진행률 숫자(%)는 채팅의 업로드 칩에 함께 표시되므로, 영상은 "분위기/상태", 칩은 "정확한 진행률" 역할 분담.

### 예시 4 — 영상 교체(브랜딩/시즌 연출)
- 명절·이벤트 때 다른 연출 영상으로 바꾸고 싶다면, 알파 포함 영상을 만들어 3-1 절차로 `uploading.webm`만 교체 → 빌드. 코드 변경 없음.

---

## 7. 관련 파일
- 영상 에셋: `web/public/avatars/uploading.webm`
- 렌더링: `web/src/components/CharacterWidget.tsx`
- 트리거: `web/src/components/ChatPanel.tsx`
- 빌드 주의: 루트 `CLAUDE.md` [사고 1] web/dist 재빌드 시 `ELECTRON_BUILD=1` 필수
