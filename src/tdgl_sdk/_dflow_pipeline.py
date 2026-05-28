"""DFlowTritonPipeline: submit TDGL simulations to Triton HPC via DFlow.

Uses DFlow DispatcherExecutor for HPC job submission and a parallel
sidecar-sync step for real-time viewer data.
"""
import json
import os
import pickle
import tempfile
import textwrap
import uuid
from datetime import datetime, timezone

from dflow import Step, Workflow, config, upload_artifact
from dflow.plugins.dispatcher import DispatcherExecutor


class DFlowTritonPipeline:
    """Submit TDGL simulations to Triton HPC via DFlow + DispatcherExecutor."""

    def __init__(
        self,
        argo_url: str = "http://localhost:30080",
        minio_endpoint: str = "http://localhost:30900",
        minio_access_key: str = "minioadmin",
        minio_secret_key: str = "minioadmin123",
        minio_bucket: str = "tdgl-results",
        namespace: str = "tdgl",
        triton_host: str = "fangr1@code.triton.aalto.fi",
        triton_work_dir: str = "/scratch/work/fangr1/tdgl-runner",
        sbatch_options: dict | None = None,
        sidecar_interval: int = 5,
    ):
        self.argo_url = argo_url
        self.namespace = namespace
        self.triton_host = triton_host
        self.triton_work_dir = triton_work_dir
        self.sbatch_options = sbatch_options or {
            "partition": "batch-csl",
            "cpus-per-task": "4",
            "mem": "16G",
            "time": "12:00:00",
        }
        self.sidecar_interval = sidecar_interval
        self._minio_endpoint = minio_endpoint
        self._minio_access_key = minio_access_key
        self._minio_secret_key = minio_secret_key
        self._minio_bucket = minio_bucket

        config["host"] = argo_url
        config["namespace"] = namespace

        # Configure DFlow's global S3 client for artifact storage.
        # Upload uses the external endpoint; Argo uses its own artifactRepository config.
        # Must use the same bucket as Argo's artifactRepository (argo-artifacts).
        from dflow.utils import s3_config
        s3_config["endpoint"] = minio_endpoint.replace("http://", "")
        s3_config["access_key"] = minio_access_key
        s3_config["secret_key"] = minio_secret_key
        s3_config["bucket_name"] = "argo-artifacts"
        s3_config["secure"] = False
        # Reset cached client so new config takes effect
        s3_config["storage_client"] = None

    def _generate_run_id(self) -> str:
        return (
            datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            + "-" + uuid.uuid4().hex[:6]
        )

    def submit(
        self,
        device,
        timing_params: dict,
        solver_options: dict | None = None,
        epsilon_params: dict | None = None,
    ) -> tuple[str, str]:
        """Submit a DFlow workflow. Returns (run_id, wf_name)."""
        from dflow import ShellOPTemplate, Inputs, Outputs, InputParameter, InputArtifact, OutputArtifact

        run_id = self._generate_run_id()
        tmp = tempfile.mkdtemp()

        device_path = os.path.join(tmp, "device.pkl")
        with open(device_path, "wb") as f:
            pickle.dump(device, f)

        timing_path = os.path.join(tmp, "timing.json")
        with open(timing_path, "w") as f:
            json.dump(timing_params, f)

        solver_path = os.path.join(tmp, "solver_options.json")
        with open(solver_path, "w") as f:
            json.dump(solver_options or {}, f)

        artifacts = {
            "device": upload_artifact(device_path),
            "timing": upload_artifact(timing_path),
            "solver": upload_artifact(solver_path),
        }

        runner_candidates = [
            os.environ.get("TDGL_SLURM_RUNNER_PATH", ""),
            os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "triton", "slurm_runner.py")),
            "/app/triton/slurm_runner.py",
        ]
        runner_path = next((path for path in runner_candidates if path and os.path.exists(path)), None)
        if runner_path:
            artifacts["slurm_runner"] = upload_artifact(runner_path)

        if epsilon_params:
            eps_path = os.path.join(tmp, "epsilon_params.json")
            with open(eps_path, "w") as f:
                json.dump(epsilon_params, f)
            artifacts["epsilon"] = upload_artifact(eps_path)

        host_parts = self.triton_host.split("@")
        username = host_parts[0]
        hostname = host_parts[-1]

        cpu_count = int(self.sbatch_options.get("cpus-per-task", "4"))
        mem_str = self.sbatch_options.get("mem", "")
        time_str = self.sbatch_options.get("time", "04:00:00")
        # DPDispatcher maps cpu_per_node to --ntasks-per-node (MPI ranks).
        # py-tdgl uses OpenMP, so we need --cpus-per-task via custom_flags.
        custom_flags = [
            f"#SBATCH --cpus-per-task={cpu_count}",
            f"#SBATCH --time={time_str}",
        ]
        if mem_str:
            custom_flags.append(f"#SBATCH --mem={mem_str}")

        executor = DispatcherExecutor(
            host=hostname,
            username=username,
            port=22,
            private_key_file=os.environ.get("SSH_KEY_PATH", "/root/.ssh/id_rsa"),
            queue_name=self.sbatch_options.get("partition", "batch-csl"),
            remote_root=f"{self.triton_work_dir}/jobs/{run_id}",
            resources_dict={
                "number_node": 1,
                "cpu_per_node": 1,
                "gpu_per_node": 0,
                "queue_name": self.sbatch_options.get("partition", "batch-csl"),
                "custom_flags": custom_flags,
            },
            envs={
                "OMP_NUM_THREADS": str(cpu_count),
                "OPENBLAS_NUM_THREADS": str(cpu_count),
            },
        )

        input_artifacts = {
            "device": InputArtifact(path="/tmp/device.pkl"),
            "timing": InputArtifact(path="/tmp/timing.json"),
            "solver": InputArtifact(path="/tmp/solver_options.json"),
        }
        if runner_path:
            input_artifacts["slurm_runner"] = InputArtifact(path="/tmp/slurm_runner.py")
        if epsilon_params:
            input_artifacts["epsilon"] = InputArtifact(path="/tmp/epsilon_params.json")

        sim_step = Step(
            name="simulate",
            template=ShellOPTemplate(
                "simulate",
                inputs=Inputs(artifacts=input_artifacts),
                image="python:3.12-slim",
                script=(
                    # DFlow/DPDispatcher stores artifacts in nested tmp/ dirs.
                    # Search the entire job tree for the actual files.
                    "JOBDIR="
                    + self.triton_work_dir
                    + "/jobs/"
                    + run_id
                    + " && "
                    "find $JOBDIR -type f -name device.pkl -exec cp -v {} $JOBDIR/ \\; && "
                    "find $JOBDIR -type f -name timing.json -exec cp -v {} $JOBDIR/ \\; && "
                    "find $JOBDIR -type f -name solver_options.json -exec cp -v {} $JOBDIR/ \\; && "
                    "find $JOBDIR -type f -name epsilon_params.json -exec cp -v {} $JOBDIR/ \\; && "
                    "find $JOBDIR -type f -name slurm_runner.py -exec cp -v {} $JOBDIR/ \\; && "
                    "ls -la $JOBDIR/ && "
                    "source /scratch/work/fangr1/miniforge3/etc/profile.d/conda.sh && "
                    "conda activate tdgl && "
                    "RUNNER=$JOBDIR/slurm_runner.py && "
                    "[ -f $RUNNER ] || RUNNER="
                    + self.triton_work_dir
                    + "/slurm_runner.py && "
                    "python $RUNNER "
                    + run_id
                    + " --sidecar-interval "
                    + str(self.sidecar_interval)
                ),
            ),
            artifacts=artifacts,
            executor=executor,
        )

        sync_script = textwrap.dedent(f"""\
            python3 -c '
            import json, os, signal, stat, sys, time, tempfile, atexit
            from tdgl_sdk.sidecar_sync import (
                rsync_continuous_h5, rsync_discrete_h5, minio_object_exists,
                upload_to_minio, upload_json_to_minio, build_h5_viewer_index,
                build_h5_iv_data, build_discrete_viewer_index, build_discrete_iv_data,
            )

            run_id = "{run_id}"
            remote_dir = f"/scratch/work/fangr1/tdgl-runner/jobs/{{run_id}}"
            local_dir = f"/tmp/triton-{{run_id}}/discrete"
            ssh_key = os.environ.get("SSH_KEY_PATH", "/root/.ssh/id_rsa")
            if os.path.exists(ssh_key) and not os.access(ssh_key, os.W_OK):
                import shutil
                writable_key = os.path.join(tempfile.gettempdir(), "ssh_key")
                shutil.copy2(ssh_key, writable_key)
                os.chmod(writable_key, stat.S_IRUSR | stat.S_IWUSR)
                ssh_key = writable_key
            host = os.environ.get("TRITON_HOST", "fangr1@code.triton.aalto.fi")
            bucket = os.environ.get("MINIO_BUCKET", "tdgl-results")
            endpoint = os.environ.get("MINIO_ENDPOINT", "http://minio.tdgl.svc.cluster.local:9000")
            timeout = int(os.environ.get("SYNC_TIMEOUT", "14400"))

            _final_upload_done = False

            def _upload_if_changed(local_path, key):
                local_size = os.path.getsize(local_path)
                from tdgl_sdk.sidecar_sync import _get_minio_client
                need_upload = True
                try:
                    s3 = _get_minio_client(endpoint)
                    meta = s3.head_object(Bucket=bucket, Key=key)
                    remote_size = meta.get("ContentLength", 0)
                    if remote_size == local_size:
                        need_upload = False
                except Exception:
                    pass
                if need_upload:
                    upload_to_minio(local_path, bucket, key, endpoint)

            def _build_viewer_index():
                index = build_h5_viewer_index(local_dir, run_id)
                if index:
                    return index
                return build_discrete_viewer_index(local_dir, run_id)

            def _build_iv_data():
                iv = build_h5_iv_data(local_dir)
                if iv:
                    return iv
                return build_discrete_iv_data(local_dir)

            def _current_status():
                for fname in ("continuous_index.json", "discrete_index.json"):
                    path = os.path.join(local_dir, fname)
                    if os.path.exists(path):
                        with open(path) as f:
                            return json.load(f).get("status", "running")
                return "running"

            def _sync_outputs():
                rsync_continuous_h5(remote_dir, local_dir, ssh_key, host)
                rsync_discrete_h5(remote_dir, local_dir, ssh_key, host)
                output_h5 = os.path.join(local_dir, "output.h5")
                if os.path.exists(output_h5):
                    _upload_if_changed(output_h5, f"tdgl-runs/{{run_id}}/output.h5")
                h5_files = sorted(f for f in os.listdir(local_dir) if f.startswith("je_") and f.endswith(".h5"))
                for fname in h5_files:
                    _upload_if_changed(os.path.join(local_dir, fname), f"tdgl-runs/{{run_id}}/{{fname}}")

            def _upload_snapshot(final_status=None):
                try:
                    _sync_outputs()
                    index = _build_viewer_index()
                    if final_status and index and index.get("status") == "running":
                        index["status"] = final_status
                    if index:
                        upload_json_to_minio(index, bucket, f"tdgl-runs/{{run_id}}/viewer-index.json", endpoint)
                    iv = _build_iv_data()
                    if iv:
                        upload_json_to_minio(iv, bucket, f"tdgl-runs/{{run_id}}/iv.json", endpoint)
                    return True
                except Exception as e:
                    print(f"SYNC_EXIT: final upload failed: {{e}}")
                    return False

            def _final_upload(final_status="failed"):
                global _final_upload_done
                if _final_upload_done:
                    return
                _final_upload_done = True
                if _upload_snapshot(final_status):
                    print(f"SYNC_EXIT: final upload done status={{final_status}}")

            def _shutdown(signum, frame):
                print(f"SYNC_SIGNAL: received {{signal.Signals(signum).name}}, marking run failed")
                _final_upload("failed")
                sys.exit(128 + signum)

            signal.signal(signal.SIGTERM, _shutdown)
            signal.signal(signal.SIGINT, _shutdown)

            atexit.register(_final_upload)

            start = time.time()
            while True:
                if time.time() - start > timeout:
                    print("SYNC_TIMEOUT")
                    _final_upload("failed")
                    break
                try:
                    _sync_outputs()
                    index = _build_viewer_index()
                    if index:
                        upload_json_to_minio(index, bucket, f"tdgl-runs/{{run_id}}/viewer-index.json", endpoint)
                    iv = _build_iv_data()
                    if iv:
                        upload_json_to_minio(iv, bucket, f"tdgl-runs/{{run_id}}/iv.json", endpoint)

                    status = _current_status()
                    if status in ("completed", "failed"):
                        print(f"SYNC_DONE: {{status}}")
                        break
                except Exception as e:
                    print(f"h5-sync error (will retry): {{e}}")
                time.sleep(5)
            '
        """)

        sync_step = Step(
            name="sidecar-sync",
            template=ShellOPTemplate(
                "sidecar-sync",
                image="172.22.133.208:30500/triton-runner:202605281434",
                script=sync_script,
                volumes=[
                    {
                        "name": "ssh-key",
                        "secret": {"secretName": "triton-ssh-key"},
                    },
                ],
                mounts=[
                    {"name": "ssh-key", "mountPath": "/root/.ssh"},
                ],
            ),
        )

        wf = Workflow(name=f"triton-tdgl-{run_id}")
        wf.add([sim_step, sync_step])
        wf.submit()

        wf_name = wf.name if hasattr(wf, "name") else f"triton-tdgl-{run_id}"
        return run_id, wf_name
