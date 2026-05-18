# Pattern: Parallel Parameter Sweep

## When to use
Run the same container with different parameter combinations in parallel.

## Key structure

```yaml
spec:
  entrypoint: sweep
  activeDeadlineSeconds: 7200
  arguments:
    parameters:
      - name: run-id
      - name: image
      - name: param-list
        value: "[]"  # JSON array of param objects
  templates:
    - name: sweep
      steps:
        - - name: run-param
            template: run-single
            withParam: "{{workflow.parameters.param-list}}"
            arguments:
              parameters:
                - name: params-json
                  value: "{{item}}"

    - name: run-single
      inputs:
        parameters:
          - name: params-json
      container:
        image: "{{workflow.parameters.image}}"
        command: [python, /app/runner.py]
        env:
          - name: RUN_ID
            value: "{{workflow.parameters.run-id}}"
          - name: PARAMS
            value: "{{inputs.parameters.params-json}}"
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
          requests: { storage: 5Gi }
```

## What to change
- `param-list` default value — your parameter combinations
- `run-single` — the container command and env vars
- `resources` — per-task resource allocation
- `storage` — larger if each run produces big output files

## Gotchas
- `withParam` iterates over a JSON array — each `{{item}}` is one element
- For many parallel tasks, ensure cluster has enough resources or set `parallelism` in spec
- Each parallel task gets the same PVC — use unique sub-paths per task to avoid conflicts
```