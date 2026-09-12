# 한국어 → 중국어·영어·일본어 동시 번역기

한국어를 입력하면 Claude API 한 번 호출로 간체 중국어, 영어, 일본어를 동시에 돌려주는 작은 웹앱입니다.

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
| `ANTHROPIC_API_KEY` | 필수. Anthropic API 키 |
| `TRANSLATOR_MODEL` | 선택. 기본값 `claude-opus-5` |
| `PORT` | 선택. 기본값 5000 |

## 테스트

```bash
python -m pytest translator/tests -q
```
