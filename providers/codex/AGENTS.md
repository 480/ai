# Codex CLI Agents

Codex CLI용 체크인 산출물과 설치 동작을 정리한 문서입니다.

## Main Prompt

Codex는 루트 `AGENTS.md`의 480ai 관리 블록을 architect 메인 프롬프트로 사용합니다.
관리 블록 소스는 Codex 전용 architect instruction 본문(`providers/codex/instructions/480-architect.md`)이며 architect custom agent는 따로 만들지 않습니다.

## 이름 매핑

- `480-developer` -> `480-developer` (`providers/codex/agents/480-developer.toml`)
- `480-code-reviewer` -> `480-code-reviewer` (`providers/codex/agents/480-code-reviewer.toml`)
- `480-code-reviewer2` -> `480-code-reviewer2` (`providers/codex/agents/480-code-reviewer2.toml`)
- `480-code-scanner` -> `480-code-scanner` (`providers/codex/agents/480-code-scanner.toml`)

## Custom agents

Codex custom agent는 아래 4개 서브에이전트만 제공합니다.

- `480-developer`
  - maps from: `480-developer`
  - file: `providers/codex/agents/480-developer.toml`
  - model: `gpt-5.4`
  - reasoning: `medium`
  - sandbox: `workspace-write`

- `480-code-reviewer`
  - maps from: `480-code-reviewer`
  - file: `providers/codex/agents/480-code-reviewer.toml`
  - model: `gpt-5.4`
  - reasoning: `high`
  - sandbox: `read-only`

- `480-code-reviewer2`
  - maps from: `480-code-reviewer2`
  - file: `providers/codex/agents/480-code-reviewer2.toml`
  - model: `gpt-5.4-mini`
  - reasoning: `medium`
  - sandbox: `read-only`

- `480-code-scanner`
  - maps from: `480-code-scanner`
  - file: `providers/codex/agents/480-code-scanner.toml`
  - model: `gpt-5.4-mini`
  - reasoning: `low`
  - sandbox: `workspace-write`

## 설치 이름과 경로

설치 파일은 `~/.codex/agents/` 또는 `<project>/.codex/agents/`에 복사됩니다.
user 범위는 `~/.codex/AGENTS.md`, project 범위는 저장소 루트 `AGENTS.md`에 480ai 관리 블록을 추가합니다.
Codex 설정은 공식 계약에 맞춰 `~/.codex/config.toml` 또는 `<project>/.codex/config.toml`에 최소 merge만 적용합니다.
설치 시 기존 설정은 보존한 채 `features.multi_agent = true`와 `agents.max_depth = 2`만 반영합니다.
Codex CLI는 각 TOML의 `name` 필드를 custom agent 이름으로 사용합니다.
루트 `AGENTS.md` 480ai 관리 블록은 architect 메인 프롬프트 본문을 그대로 사용합니다.
기존 사용자 내용은 보존한 채 480ai 관리 블록만 덧붙입니다.
재설치 시에는 기존 480ai 관리 블록을 교체하여 중복을 만들지 않습니다.
제거 시에는 480ai 관리 블록만 삭제합니다.

## Codex delegation model

- Codex는 native subagent workflow를 사용합니다. architect는 `480-developer`를 spawn하고, developer는 필요할 때만 reviewer/scanner 서브에이전트를 씁니다.
- 기본 delegation depth는 2단계입니다: architect(depth 0) -> developer(depth 1) -> reviewer/scanner(depth 2).
- reviewer 기본 흐름은 순차입니다: `480-code-reviewer` 먼저, 그다음 `480-code-reviewer2`입니다. 병렬 리뷰는 기본값이 아닙니다.
- 동시 agent budget은 좁게 유지합니다. 기본 경로는 한 번에 하나의 child agent만 활성화하는 것입니다.
- spawn 응답에 `agent_id`가 없거나 구조화 응답이 아니면 `spawn_failure`로 간주합니다.
- `spawn_failure`, thread limit, usage limit는 코드 구현 문제가 아니라 위임 인프라 blocker로 분류합니다.
- 같은 세션 안에서 1회 재시도 후에도 blocker가 남으면 parent architect로 구조화된 blocker report만 반환합니다.
- 사용자에게 `새 세션`이나 `예외 허용`을 기본 경로로 제시하지 않습니다.

다음처럼 Codex CLI 프롬프트에서 바로 호출할 수 있습니다.
문서와 예시는 Codex의 실제 자연어 호출 패턴을 기준으로 작성합니다.

```text
Plan the next work for docs/480ai/example-topic/001-example-task.md.
Have 480-developer implement docs/480ai/example-topic/001-example-task.md.
Have 480-developer request review from 480-code-reviewer first, then 480-code-reviewer2 after the first review is clear, and return a completion report.
```
`기본 추천` 설치는 `providers/codex/agents/`의 체크인 산출물을 그대로 사용합니다.
`고급` 설치는 선택한 모델 조합으로 임시 산출물을 렌더링한 뒤 같은 설치 경로에 복사합니다.

## 범위 메모

Codex CLI 설치기는 custom agent와 480ai 관리 AGENTS 블록만 관리합니다.
사용자 작성 내용이나 480ai 관리 블록 밖의 AGENTS.md 내용은 건드리지 않습니다.

## Source of truth

- 공통 agent 정의: `bundles/common/agents.json`.
- 공통 instruction 본문: `bundles/common/instructions/`.
- Codex provider 전용 override 본문(있다면): `providers/codex/instructions/`.
- provider별 설치 경로와 모델 선택 스키마: `app/providers.py`.
- provider 산출물 렌더링: `app/render_agents.py`.
- 설치/제거 entrypoint: `app/manage_agents.py`.
- 상태 저장과 복원: `app/installer_core.py`.
- 사용자 안내: `README.md`.
