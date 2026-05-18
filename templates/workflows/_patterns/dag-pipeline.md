# Pattern: Multi-Step DAG Pipeline

## When to use
Multiple sequential steps with dependencies (e.g., prepare → process → postprocess).

## Key structure

```yaml
spec:
  entrypoint: pipeline
  activeDeadlineSeconds: 7200
  arguments:
    parameters:
      - name: run-id
      - name: image
  templates:
    - name: pipeline
      steps:
        - - name: prepare
            template: prepare-step
        - - name: process
            template: process-step
        - - name: finalize
            template: finalize-step

    - name: prepare-step
      container:
        image: "{{workflow.parameters.image}}"
        command: [python, /app/prepare.py]
        env:
          - name: RUN_ID
            value: "{{workflow.parameters.run-id}}"
        volumeMounts:
          - name: run-data
            mountPath: /data

    - name: process-step
      container:
        image: "{{workflow.parameters.image}}"
        command: [python, /app/runner.py]
        resources:
          requests: { cpu: "2", memory: "4Gi" }
          limits: { cpu: "2", memory: "4Gi" }
        volumeMounts:
          - name: run-data
            mountPath: /data

    - name: finalize-step
      container:
        image: "{{workflow.parameters.image}}"
        command: [python, /app/finalize.py]
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
- Number of steps and their names
- Each step's `command`, `env`, `resources`
- `activeDeadlineSeconds` — sum of expected step durations + buffer

## Gotchas
- Steps syntax uses double-list `- -` for sequential (see example). Single `-` means parallel.
- All steps share the same PVC via `volumeClaimTemplates` — intermediate data goes through `/data`
- The existing `cpp-tdgl-sim.yaml` is a real example of this pattern
```