# Agents

OpenCode용 체크인 산출물과 설치 동작을 정리한 문서입니다.

## Primary

- `480-architect`
  - file: `providers/opencode/agents/480-architect.md`
  - model: `openai/gpt-5.4`
  - reasoning: `xhigh`
  - role: planning, scoping, and orchestrating the implementation/review loop

## Subagents

- `480-developer`
  - file: `providers/opencode/agents/480-developer.md`
  - model: `openai/gpt-5.4`
  - reasoning: `medium`
  - role: implementation

- `480-code-reviewer`
  - file: `providers/opencode/agents/480-code-reviewer.md`
  - model: `openai/gpt-5.4`
  - reasoning: `high`
  - role: primary code review

- `480-code-reviewer2`
  - file: `providers/opencode/agents/480-code-reviewer2.md`
  - model: `google/gemini-3-flash-preview`
  - reasoning: `high`
  - role: secondary code review

- `480-code-scanner`
  - file: `providers/opencode/agents/480-code-scanner.md`
  - model: `openai/gpt-5.4-nano`
  - reasoning: `high`
  - role: repository scanning and stack discovery

## 설치 이름과 경로

설치 파일 이름은 체크인 산출물과 동일하며 항상 `~/.config/opencode/agents/`에 복사됩니다.
`기본 추천` 설치는 `providers/opencode/agents/`의 체크인 산출물을 그대로 사용합니다.
`고급` 설치는 선택한 모델 조합으로 임시 산출물을 렌더링한 뒤 같은 설치 경로에 복사합니다.

## 기본 동작

- 기본값으로 `480-architect`를 활성화하며 설치 시 `default_agent`를 설정합니다.
- `--no-activate-default` 또는 `BOOTSTRAP_ACTIVATE_DEFAULT=0`을 주면 `default_agent`를 바꾸지 않습니다.
- 제거는 bootstrap 상태에 활성화 기록이 있고 현재 설정이 아직 `480-architect`일 때만 이전 기본값을 복원합니다.

## Source of truth

- 공통 agent 정의: `bundles/common/agents.json`.
- 공통 instruction 본문: `bundles/common/instructions/`.
- provider별 설치 경로와 모델 선택 스키마: `app/providers.py`.
- provider 산출물 렌더링: `app/render_agents.py`.
- 설치/제거 entrypoint: `app/manage_agents.py`.
- 상태 저장과 복원: `app/installer_core.py`.
- 사용자 안내: `README.md`.
