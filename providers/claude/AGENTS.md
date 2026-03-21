# Claude Agents

Claude Code용 체크인 산출물과 설치 동작을 정리한 문서입니다.

## 이름 매핑

- `480-architect` -> `480-architect` (`providers/claude/agents/480-architect.md`)
- `480-developer` -> `480-developer` (`providers/claude/agents/480-developer.md`)
- `480-code-reviewer` -> `480-code-reviewer` (`providers/claude/agents/480-code-reviewer.md`)
- `480-code-reviewer2` -> `480-code-reviewer2` (`providers/claude/agents/480-code-reviewer2.md`)
- `480-code-scanner` -> `480-code-scanner` (`providers/claude/agents/480-code-scanner.md`)

## Primary

- `480-architect`
  - maps from: `480-architect`
  - file: `providers/claude/agents/480-architect.md`
  - model: `claude-opus-4-6`
  - effort: `max`

## Subagents

- `480-developer`
  - maps from: `480-developer`
  - file: `providers/claude/agents/480-developer.md`
  - model: `claude-sonnet-4-6`
  - effort: `medium`

- `480-code-reviewer`
  - maps from: `480-code-reviewer`
  - file: `providers/claude/agents/480-code-reviewer.md`
  - model: `claude-opus-4-6`
  - effort: `low`

- `480-code-reviewer2`
  - maps from: `480-code-reviewer2`
  - file: `providers/claude/agents/480-code-reviewer2.md`
  - model: `claude-sonnet-4-6`
  - effort: `low`

- `480-code-scanner`
  - maps from: `480-code-scanner`
  - file: `providers/claude/agents/480-code-scanner.md`
  - model: `haiku`
  - effort: `low`

## 설치 이름과 경로

설치 파일은 위 Claude 전용 이름으로 `~/.claude/agents/` 또는 `<project>/.claude/agents/`에 복사됩니다.
`기본 추천` 설치는 `providers/claude/agents/`의 체크인 산출물을 그대로 사용합니다.
`고급` 설치는 선택한 모델 조합으로 임시 산출물을 렌더링한 뒤 같은 설치 경로에 복사합니다.

## 기본 동작

- 기본 활성화는 선택 사항이며 `--activate-default`를 줄 때만 `agent`를 `480-architect`로 설정합니다.
- 제거는 현재 `agent`가 아직 `480-architect`일 때만 이전 값을 복원합니다.

## 팀 동작

- Claude Code의 agent team 기능이 켜진 환경에서는 `480-architect`가 팀 리더로 기본 3인 팀(`480-developer`, `480-code-reviewer`, `480-code-reviewer2`)을 조율합니다.
- `480-code-scanner`는 저장소 스캔이 실제로 필요할 때만 선택적으로 추가합니다.
- 설치 중 agent teams 실험 플래그 활성화 여부를 물어보고, 동의하면 `settings.json`의 `env`에 `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`을 merge합니다.
- 제거는 이 teams 실험 플래그 env 설정을 건드리지 않습니다.
- 팀 기능이 비활성화되었거나 미지원이면 `480-architect`가 기존 single-orchestrator fallback으로 같은 Task Brief 기반 흐름을 직접 진행합니다.

## Source of truth

- 공통 agent 정의: `bundles/common/agents.json`.
- 기본 instruction 본문: `bundles/common/instructions/`.
- Claude provider 전용 override 본문(있다면): `providers/claude/instructions/`.
- provider별 설치 경로와 모델 선택 스키마: `app/providers.py`.
- provider 산출물 렌더링: `app/render_agents.py`.
- 설치/제거 entrypoint: `app/manage_agents.py`.
- 상태 저장과 복원: `app/installer_core.py`.
- 사용자 안내: `README.md`.
