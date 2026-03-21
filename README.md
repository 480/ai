# 480 agents

480 에이전트 5종을 OpenCode, Claude Code, Codex CLI에 설치하여 플래닝 > 구현 > 리뷰 루프에 최적화된 개발용 에이전트를 제공합니다.

## 480 에이전트?

- 플래닝 > 구현 > 리뷰 루프에 최적화된 개발용 에이전트
- https://5k.gg/480ai

## Providers

- OpenCode: user 범위 설치, 기본으로 `480-architect` 활성화
- Claude Code: user / project 범위 설치, 선택 시 `480-architect` 활성화. 설치 중 agent teams 실험 플래그를 물어보고, 동의 시 `settings.json`의 `env`에 반영
- Codex CLI: user / project 범위 설치, 루트 `AGENTS.md` 480ai 관리 블록이 architect 메인 프롬프트를 맡고 custom agent는 4개 서브에이전트만 설치. reviewer 흐름은 기본적으로 `480-code-reviewer` -> `480-code-reviewer2` 순차이며 동시 agent budget을 좁게 유지합니다. 설치 시 `config.toml`에는 `features.multi_agent = true`, `agents.max_depth = 2`만 최소 merge

## 설치

```bash
sh -c "$(curl -fsSL https://raw.githubusercontent.com/480/ai/main/bootstrap/install-remote.sh)"
```

실행하면 TUI에서 여러 provider를 함께 선택할 수 있습니다.

## 삭제

```bash
curl -fsSL "https://raw.githubusercontent.com/480/ai/main/bootstrap/uninstall-remote.sh" | sh
```

## License

MIT
