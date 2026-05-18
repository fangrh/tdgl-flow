"""Kubernetes API wrapper for managing viewer Pod/Service lifecycle."""

import logging

from kubernetes import client, config
from kubernetes.client import V1DeleteOptions

logger = logging.getLogger(__name__)


def _load_k8s_config() -> None:
    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config()


def create_viewer_pod(
    session_id: str,
    run_id: str,
    viewer_type: str,
    image: str,
    namespace: str,
):
    _load_k8s_config()
    core = client.CoreV1Api()
    pod_name = f"viewer-{session_id[:12]}"
    service_name = f"viewer-{session_id[:12]}"

    pod = core.create_namespaced_pod(
        namespace=namespace,
        body=client.V1Pod(
            api_version="v1",
            kind="Pod",
            metadata=client.V1ObjectMeta(
                name=pod_name,
                labels={
                    "app": "viewer-session",
                    "viewer-type": viewer_type,
                    "session-id": session_id,
                },
            ),
            spec=client.V1PodSpec(
                containers=[
                    client.V1Container(
                        name="viewer",
                        image=image,
                        ports=[client.V1ContainerPort(container_port=8000)],
                        env=[
                            client.V1EnvVar(name="VIEWER_SESSION_ID", value=session_id),
                            client.V1EnvVar(name="RUN_ID", value=run_id),
                        ],
                        volume_mounts=[
                            client.V1VolumeMount(name="zarr-data", mount_path="/data/zarr"),
                        ],
                    )
                ],
                volumes=[
                    client.V1Volume(
                        name="zarr-data",
                        persistent_volume_claim=client.V1PersistentVolumeClaimVolumeSource(
                            claim_name="zarr-data",
                        ),
                    )
                ],
                image_pull_secrets=[
                    client.V1LocalObjectReference(name="ghcr-secret"),
                ],
            ),
        ),
    )
    logger.info("Created pod %s in %s", pod_name, namespace)

    svc = core.create_namespaced_service(
        namespace=namespace,
        body=client.V1Service(
            api_version="v1",
            kind="Service",
            metadata=client.V1ObjectMeta(name=service_name),
            spec=client.V1ServiceSpec(
                selector={"session-id": session_id},
                ports=[client.V1ServicePort(port=80, target_port=8000)],
            ),
        ),
    )
    logger.info("Created service %s in %s", service_name, namespace)
    return pod


def delete_viewer_pod(session_id: str, namespace: str) -> None:
    _load_k8s_config()
    core = client.CoreV1Api()
    pod_name = f"viewer-{session_id[:12]}"
    service_name = f"viewer-{session_id[:12]}"

    for name, is_service in [(service_name, True), (pod_name, False)]:
        try:
            if is_service:
                core.delete_namespaced_service(name, namespace)
            else:
                core.delete_namespaced_pod(name, namespace, body=V1DeleteOptions())
            logger.info("Deleted %s", name)
        except client.ApiException as e:
            if e.status != 404:
                logger.warning("Failed to delete %s: %s", name, e)


def is_pod_ready(session_id: str, namespace: str) -> bool:
    _load_k8s_config()
    core = client.CoreV1Api()
    pod_name = f"viewer-{session_id[:12]}"
    try:
        pod = core.read_namespaced_pod(pod_name, namespace)
    except client.ApiException:
        return False

    for cond in (pod.status.conditions or []):
        if cond.type == "Ready" and cond.status == "True":
            return True
    return False


def is_pod_failed(session_id: str, namespace: str) -> bool:
    _load_k8s_config()
    core = client.CoreV1Api()
    pod_name = f"viewer-{session_id[:12]}"
    try:
        pod = core.read_namespaced_pod(pod_name, namespace)
    except client.ApiException:
        return True

    if pod.status.phase in ("Failed", "Unknown"):
        return True
    for cs in (pod.status.container_statuses or []):
        if cs.state and cs.state.terminated and cs.state.terminated.exit_code != 0:
            return True
    return False


def get_pod_failure_reason(session_id: str, namespace: str) -> str | None:
    _load_k8s_config()
    core = client.CoreV1Api()
    pod_name = f"viewer-{session_id[:12]}"
    try:
        pod = core.read_namespaced_pod(pod_name, namespace)
    except client.ApiException as e:
        return f"Pod not found: {e.reason}"

    if pod.status.message:
        return pod.status.message
    for cs in (pod.status.container_statuses or []):
        if cs.state and cs.state.terminated:
            return cs.state.terminated.message or f"exit code {cs.state.terminated.exit_code}"
    return None