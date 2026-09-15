# 한국어 → 중국어·영어·일본어 동시 번역기

한국어를 입력하면 Claude API 한 번 호출로 간체 중국어, 영어, 일본어를 동시에 돌려주는 작은 웹앱입니다.

## 키 없이 쓰는 방법

1. **Claude 아티팩트 (권장, 안드로이드에서 바로 사용)** — `translator/artifact/samgugeo.html`을 claude.ai 아티팩트로 게시하면
   여는 사람의 Claude 구독으로 번역이 실행됩니다. API 키 불필요. Chrome에서 "홈 화면에 추가"로 앱처럼 사용.
2. **무료 백엔드** — `ANTHROPIC_API_KEY`가 없으면 Flask 앱이 자동으로 `deep-translator`(구글 번역 웹 엔드포인트)로 동작합니다.
   비공식 엔드포인트라 품질과 안정성은 낮습니다. `TRANSLATOR_BACKEND=free|claude|auto`로 강제할 수 있습니다.

## 실행

```bash
pip install -r translator/requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...
python -m translator.app          # http://localhost:5000
```

CLI로도 쓸 수 있습니다.

```bash
python -m translator.cli "오늘 회의는 오후 3시로 변경되었습니다."
echo "안녕하세요" | python -m translator.cli
```

## 구조

| 파일 | 역할 |
|---|---|
| `translator/translate.py` | Claude 호출. JSON 스키마(`zh`/`en`/`ja`)로 구조화된 출력을 받음 |
| `translator/app.py` | Flask 서버. `GET /`, `POST /api/translate`, `GET /health` |
| `translator/templates/index.html` | 입력창 + 3개 결과 카드 UI |
| `translator/cli.py` | 터미널용 |

## 환경변수

| 이름 | 설명 |
|---|---|
| `ANTHROPIC_API_KEY` | 선택. 없으면 무료 백엔드로 전환 |
| `TRANSLATOR_MODEL` | 선택. 기본값 `claude-opus-5` |
| `TRANSLATOR_BACKEND` | 선택. `auto`(기본)·`claude`·`free` |
| `PORT` | 선택. 기본값 5000 |

## 테스트

```bash
python -m pytest translator/tests -q
```
