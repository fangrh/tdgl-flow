# Pattern: Single Task

## When to use
One container runs to completion with input parameters. Simplest workflow.

## Key structure

```yaml
spec:
  entrypoint: main
  activeDeadlineSeconds: 3600
  arguments:
    parameters:
      - name: run-id
      - name: image
  templates:
    - name: main
      container:
        image: "{{workflow.parameters.image}}"
        command: [python, /app/runner.py]
        env:
          - name: RUN_ID
            value: "{{workflow.parameters.run-id}}"
        resources:
          requests: { cpu: "1", memory: "2Gi" }
          limits: { cpu: "2", memory: "4Gi" }
        volumeMounts:
          - name: run-data
            mountPath: /data
  volumeClaimTemplates:
    - metadata: { name: run-data }
      spec:
        accessModes: ["ReadWriteOnce"]
        resources:
          requests: { storage: 1Gi }
```

## What to change
- `metadata.name` — workflow name
- `command` — entry point command
- `env` — pass parameters as env vars
- `resources` — adjust CPU/memory to workload
- `activeDeadlineSeconds` — set appropriate timeout

## Gotchas
- If `run-id` is empty, the task will likely fail — validate in runner.py
- Use `volumeClaimTemplates` for per-run storage, not shared PVCs
```