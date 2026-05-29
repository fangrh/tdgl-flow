# kubeflow-tdgl

End-to-end TDGL simulation: build device → run py-tdgl → store in MinIO → view in notebook.

## Project Structure

| Path | Purpose |
|------|---------|
| `services/py-tdgl-runner/` | Argo Workflow runner image |
| `workflows/rectangle-device-builder.yaml` | Standalone device-builder workflow |
| `src/tdgl_sdk/` | Notebook SDK: pipeline, MinIO access, viewer, diagnostics |
| `src/tdgl_workflow/` | Shared: mesh builder, timing schedule |
| `notebooks/e2e_sim_test.py` | Main end-to-end test + live viewer |
| `infra/` | Namespace, Argo Workflows, MinIO, nginx |
| `clusters/argocd/` | Argo CD app definitions |
## Dev

1. **本地验证** — 逻辑清晰、输入输出数据结构明确，本地 notebook 跑通即可直接 CI/CD：
   ```bash
   pip install -e ".[dev]"
   python notebooks/e2e_sim_test.py
   ```
2. **K8s 验证**（仅确有必要时） — 涉及集群环境、资源调度等无法本地验证的改动：
   ```bash
   docker build -f services/py-tdgl-runner/Dockerfile -t ghcr.io/fangrh/py-tdgl-runner:dev .
   docker push ghcr.io/fangrh/py-tdgl-runner:dev
   kubectl -n tdgl submit workflow --from workflowtemplate/py-tdgl-sim -p image=ghcr.io/fangrh/py-tdgl-runner:dev
   ```
3. **上线** — push main → CI/CD 自动构建部署。

## CI

Path trigger: `services/py-tdgl-runner/**`, `src/**`, `pyproject.toml`。

## Adding a New Service or Workflow

Use the existing `services/py-tdgl-runner/` and `workflows/rectangle-device-builder.yaml` as reference. Copy and adapt.

<!-- gitnexus:start -->
# GitNexus — Code Intelligence

This project is indexed by GitNexus as **tdgl-flow** (3652 symbols, 5848 relationships, 169 execution flows). Use the GitNexus MCP tools to understand code, assess impact, and navigate safely.

> If any GitNexus tool warns the index is stale, run `npx gitnexus analyze` in terminal first.

## Always Do

- **MUST run impact analysis before editing any symbol.** Before modifying a function, class, or method, run `gitnexus_impact({target: "symbolName", direction: "upstream"})` and report the blast radius (direct callers, affected processes, risk level) to the user.
- **MUST run `gitnexus_detect_changes()` before committing** to verify your changes only affect expected symbols and execution flows.
- **MUST warn the user** if impact analysis returns HIGH or CRITICAL risk before proceeding with edits.
- When exploring unfamiliar code, use `gitnexus_query({query: "concept"})` to find execution flows instead of grepping. It returns process-grouped results ranked by relevance.
- When you need full context on a specific symbol — callers, callees, which execution flows it participates in — use `gitnexus_context({name: "symbolName"})`.

## Never Do

- NEVER edit a function, class, or method without first running `gitnexus_impact` on it.
- NEVER ignore HIGH or CRITICAL risk warnings from impact analysis.
- NEVER rename symbols with find-and-replace — use `gitnexus_rename` which understands the call graph.
- NEVER commit changes without running `gitnexus_detect_changes()` to check affected scope.

## Resources

| Resource | Use for |
|----------|---------|
| `gitnexus://repo/tdgl-flow/context` | Codebase overview, check index freshness |
| `gitnexus://repo/tdgl-flow/clusters` | All functional areas |
| `gitnexus://repo/tdgl-flow/processes` | All execution flows |
| `gitnexus://repo/tdgl-flow/process/{name}` | Step-by-step execution trace |

## CLI

| Task | Read this skill file |
|------|---------------------|
| Understand architecture / "How does X work?" | `.claude/skills/gitnexus/gitnexus-exploring/SKILL.md` |
| Blast radius / "What breaks if I change X?" | `.claude/skills/gitnexus/gitnexus-impact-analysis/SKILL.md` |
| Trace bugs / "Why is X failing?" | `.claude/skills/gitnexus/gitnexus-debugging/SKILL.md` |
| Rename / extract / split / refactor | `.claude/skills/gitnexus/gitnexus-refactoring/SKILL.md` |
| Tools, resources, schema reference | `.claude/skills/gitnexus/gitnexus-guide/SKILL.md` |
| Index, status, clean, wiki CLI commands | `.claude/skills/gitnexus/gitnexus-cli/SKILL.md` |

<!-- gitnexus:end -->
