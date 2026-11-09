"""Focused tests for M7: n8n Integration workflow validation.

Validates:
- The n8n workflow JSON is well-formed and parseable
- All required node types are present (cron, httpRequest, if, set)
- HTTP Request nodes reference actual API endpoints defined in routes.py
- Auth headers (X-API-Key via INTAKE_API_KEY env) are configured
- Cron trigger is bounded (fixed interval, no recursive polling)
- Connections between nodes are consistent
- Workflow references match the actual FastAPI route paths
"""
import json
import os
import re
import sys

# Ensure `src` package is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest

WORKFLOW_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "n8n", "workflows",
    "product-intelligence-scan.json",
)

# Actual API route paths from src/api/routes.py (M0–M6, no invention)
EXPECTED_ENDPOINTS = {
    "/api/v1/scan",
    "/api/v1/batches",
    "/api/v1/jobs/{job_id}",
}

IMAGE_MATCH_ENDPOINT = "/api/v1/batches/{batch_id}/image-matches"
BATCH_STATUS_ENDPOINT = "/api/v1/batches/{batch_id}/status"


def load_workflow():
    with open(WORKFLOW_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def collect_nodes(workflow):
    return {node["name"]: node for node in workflow.get("nodes", [])}


def collect_urls(workflow):
    """Extract all HTTP Request URLs from the workflow."""
    urls = []
    for node in workflow.get("nodes", []):
        if node.get("type") == "n8n-nodes-base.httpRequest":
            urls.append(node["parameters"].get("url", ""))
    return urls


class TestWorkflowStructure:
    """Validate the workflow file structure."""

    def test_workflow_file_exists(self):
        assert os.path.exists(WORKFLOW_PATH), f"Workflow not found: {WORKFLOW_PATH}"

    def test_workflow_is_valid_json(self):
        workflow = load_workflow()
        assert isinstance(workflow, dict)
        assert "name" in workflow
        assert "nodes" in workflow
        assert "connections" in workflow

    def test_workflow_has_name(self):
        workflow = load_workflow()
        assert "Product Intelligence" in workflow["name"]

    def test_workflow_is_inactive_by_default(self):
        workflow = load_workflow()
        # Safety: workflow should not be active on import (user must activate)
        assert workflow.get("active") is False

    def test_required_node_types_present(self):
        workflow = load_workflow()
        node_types = {n.get("type") for n in workflow.get("nodes", [])}
        assert "n8n-nodes-base.cron" in node_types, "Missing Cron Trigger"
        assert "n8n-nodes-base.httpRequest" in node_types, "Missing HTTP Request"
        assert "n8n-nodes-base.if" in node_types, "Missing IF nodes"
        assert "n8n-nodes-base.set" in node_types, "Missing Set (notification) nodes"

    def test_cron_trigger_present(self):
        workflow = load_workflow()
        nodes = collect_nodes(workflow)
        cron = nodes.get("Cron Trigger")
        assert cron is not None, "Cron Trigger node missing"
        assert cron["type"] == "n8n-nodes-base.cron"
        # Bounded interval: every 30 minutes, not recursive
        trigger_times = cron["parameters"].get("triggerTimes", {})
        items = trigger_times.get("item", [])
        assert len(items) == 1
        assert items[0]["mode"] == "everyMinute"
        assert items[0]["value"] == 30


class TestEndpoints:
    """Validate HTTP Request nodes reference real API endpoints."""

    def test_http_request_nodes_have_urls(self):
        workflow = load_workflow()
        urls = collect_urls(workflow)
        assert len(urls) > 0, "No HTTP Request URLs found"
        for url in urls:
            assert url.startswith("={{ $env.INTAKE_URL }}"), (
                f"URL should use INTAKE_URL env var: {url}"
            )

    def test_scan_endpoint_referenced(self):
        workflow = load_workflow()
        urls = collect_urls(workflow)
        assert any("/api/v1/scan" in u for u in urls), (
            "Missing POST /api/v1/scan reference"
        )

    def test_batches_list_endpoint_referenced(self):
        workflow = load_workflow()
        urls = collect_urls(workflow)
        assert any("/api/v1/batches" in u and "image-matches" not in u
                   and "/status" not in u for u in urls), (
            "Missing GET /api/v1/batches reference"
        )

    def test_batch_status_endpoint_referenced(self):
        workflow = load_workflow()
        urls = collect_urls(workflow)
        expected = BATCH_STATUS_ENDPOINT.replace("{batch_id}", "{{ $json.batch_id }}")
        assert any(expected in u for u in urls), (
            f"Missing {BATCH_STATUS_ENDPOINT} reference"
        )

    def test_image_matches_endpoint_referenced(self):
        workflow = load_workflow()
        urls = collect_urls(workflow)
        expected = IMAGE_MATCH_ENDPOINT.replace("{batch_id}", "{{ $json.batch_id }}")
        assert any(expected in u for u in urls), (
            f"Missing {IMAGE_MATCH_ENDPOINT} reference"
        )

    def test_all_endpoints_match_actual_api(self):
        """Every workflow URL path must correspond to a real route in routes.py."""
        import src.api.routes as routes_mod

        # Collect all route paths defined in the router (without /api/v1 prefix)
        actual_paths = set()
        for route in routes_mod.router.routes:
            if hasattr(route, "path"):
                actual_paths.add(route.path)

        workflow = load_workflow()
        for url in collect_urls(workflow):
            # Extract the path portion after INTAKE_URL
            path = url.replace("={{ $env.INTAKE_URL }}", "")
            # Remove template variable syntax for comparison
            path_normalized = re.sub(r"\{\{ \$json\.([a-z_]+) \}\}", r"{\1}", path)
            # Strip the /api/v1 router prefix to match route.path
            if path_normalized.startswith("/api/v1"):
                path_normalized = path_normalized[len("/api/v1"):]
            # n8n uses {{ $json.batch_id }}; FastAPI uses {batch_id}
            assert path_normalized in actual_paths, (
                f"Workflow endpoint '{path}' does not match any actual API route. "
                f"Actual routes: {sorted(actual_paths)}"
            )

    def test_no_invented_endpoints(self):
        """Ensure no endpoints outside the actual API surface are referenced."""
        workflow = load_workflow()
        urls = collect_urls(workflow)
        invented = []
        for url in urls:
            path = url.replace("={{ $env.INTAKE_URL }}", "")
            path_normalized = re.sub(r"\{\{ \$json\.([a-z_]+) \}\}", r"{\1}", path)
            if path_normalized not in EXPECTED_ENDPOINTS and \
               not path_normalized.startswith("/api/v1/batches/") and \
               path_normalized != "/api/v1/batches":
                invented.append(path_normalized)
        # All batch-related paths are covered by EXPECTED_ENDPOINTS or the param patterns
        assert len(invented) == 0, f"Invented endpoints found: {invented}"


class TestAuthentication:
    """Validate auth is configured on all HTTP Request nodes."""

    def test_http_nodes_use_header_auth(self):
        workflow = load_workflow()
        for node in workflow.get("nodes", []):
            if node.get("type") == "n8n-nodes-base.httpRequest":
                params = node["parameters"]
                assert params.get("authentication") == "genericCredentialType", (
                    f"Node '{node['name']}' should use generic auth"
                )
                assert params.get("genericAuthType") == "httpHeaderAuth", (
                    f"Node '{node['name']}' should use httpHeaderAuth"
                )
                assert "INTAKE_API_KEY" in params.get("httpHeaderAuth", ""), (
                    f"Node '{node['name']}' must use INTAKE_API_KEY env var"
                )

    def test_auth_uses_env_var_not_hardcoded(self):
        workflow = load_workflow()
        for node in workflow.get("nodes", []):
            if node.get("type") == "n8n-nodes-base.httpRequest":
                auth = node["parameters"].get("httpHeaderAuth", "")
                assert "{{ $env.INTAKE_API_KEY }}" in auth, (
                    f"Node '{node['name']}' auth must reference $env.INTAKE_API_KEY, "
                    f"not a hardcoded key: {auth}"
                )


class TestConnections:
    """Validate node connections are consistent."""

    def test_connections_reference_existing_nodes(self):
        workflow = load_workflow()
        node_names = {n["name"] for n in workflow.get("nodes", [])}
        for source, edges in workflow.get("connections", {}).items():
            assert source in node_names, f"Connection source '{source}' not a node"
            for edge_list in edges.get("main", []):
                for edge in edge_list:
                    assert edge["node"] in node_names, (
                        f"Connection target '{edge['node']}' not a node"
                    )

    def test_cron_fans_out_to_scan_and_list(self):
        workflow = load_workflow()
        connections = workflow["connections"]
        cron_edges = connections.get("Cron Trigger", {}).get("main", [])
        targets = set()
        for edge_list in cron_edges:
            for edge in edge_list:
                targets.add(edge["node"])
        assert "Scan for Batches" in targets, "Cron must connect to Scan for Batches"
        assert "List All Batches" in targets, "Cron must connect to List All Batches"

    def test_no_recursive_loops(self):
        """Ensure no node connects back to itself or creates an infinite loop."""
        workflow = load_workflow()
        connections = workflow.get("connections", {})
        for source, edges in connections.items():
            for edge_list in edges.get("main", []):
                for edge in edge_list:
                    assert edge["node"] != source, (
                        f"Recursive loop: '{source}' connects to itself"
                    )


class TestStatusRouting:
    """Validate status-based routing logic."""

    def test_if_nodes_for_all_statuses(self):
        workflow = load_workflow()
        node_names = {n["name"] for n in workflow.get("nodes", [])}
        expected = [
            "Batch Completed?", "Batch Failed?", "Awaiting Review?",
            "Still Processing?", "Is Discovered?", "Is Pending?",
        ]
        for name in expected:
            assert name in node_names, f"Missing routing node: {name}"

    def test_failed_routes_to_notification(self):
        workflow = load_workflow()
        connections = workflow["connections"]
        failed_edges = connections.get("Batch Failed?", {}).get("main", [])
        targets = []
        for edge_list in failed_edges:
            for edge in edge_list:
                targets.append(edge["node"])
        assert "Notify: Batch Dead-Lettered" in targets, (
            "Failed status must route to dead-lettered notification (terminal, no retry)"
        )

    def test_awaiting_review_routes_to_image_matches(self):
        workflow = load_workflow()
        connections = workflow["connections"]
        review_edges = connections.get("Awaiting Review?", {}).get("main", [])
        targets = []
        for edge_list in review_edges:
            for edge in edge_list:
                targets.append(edge["node"])
        assert "Get Unmatched Images" in targets, (
            "Awaiting review must route to image-matches endpoint"
        )

    def test_processing_routes_to_job_status_detail(self):
        workflow = load_workflow()
        connections = workflow["connections"]
        proc_edges = connections.get("Still Processing?", {}).get("main", [])
        targets = []
        for edge_list in proc_edges:
            for edge in edge_list:
                targets.append(edge["node"])
        assert "Get Job Status Detail" in targets, (
            "Processing status must route to job status detail"
        )


class TestEnvConfiguration:
    """Validate environment variable configuration."""

    def test_intake_url_in_env(self):
        """INTAKE_URL should be set in .env for Docker service discovery."""
        env_path = os.path.join(
            os.path.dirname(__file__), "..", "..", "..", ".env",
        )
        assert os.path.exists(env_path), ".env file not found"
        with open(env_path, "r", encoding="utf-8") as f:
            content = f.read()
        assert "INTAKE_URL=http://intake:8000" in content, (
            "INTAKE_URL must be set in .env for n8n → intake connectivity"
        )

    def test_intake_api_key_in_env(self):
        env_path = os.path.join(
            os.path.dirname(__file__), "..", "..", "..", ".env",
        )
        with open(env_path, "r", encoding="utf-8") as f:
            content = f.read()
        assert "INTAKE_API_KEY=" in content, (
            "INTAKE_API_KEY must be set in .env"
        )


# ---------------------------------------------------------------------------
# M9: n8n Reliability Integration tests
# ---------------------------------------------------------------------------
class TestRetryRouting:
    """Validate that only retryable batches route to POST /process."""

    def test_process_endpoint_referenced(self):
        workflow = load_workflow()
        urls = collect_urls(workflow)
        expected = "/api/v1/batches/{{ $json.batch_id }}/process"
        assert any(expected in u for u in urls), (
            "Missing POST /api/v1/batches/{batch_id}/process reference"
        )

    def test_process_node_uses_post(self):
        workflow = load_workflow()
        process_node = None
        for node in workflow.get("nodes", []):
            if node.get("name") == "Process Batch":
                process_node = node
                break
        assert process_node is not None, "Process Batch node missing"
        assert process_node["parameters"].get("requestMethod") == "POST", (
            "Process Batch must use POST"
        )

    def test_process_node_uses_intake_url(self):
        workflow = load_workflow()
        process_url = None
        for node in workflow.get("nodes", []):
            if node.get("name") == "Process Batch":
                process_url = node["parameters"].get("url", "")
                break
        assert process_url is not None
        assert process_url.startswith("={{ $env.INTAKE_URL }}"), (
            "Process Batch URL must use INTAKE_URL env var"
        )
        assert "/api/v1/batches/" in process_url and "/process" in process_url

    def test_process_node_auth_configured(self):
        workflow = load_workflow()
        for node in workflow.get("nodes", []):
            if node.get("name") == "Process Batch":
                params = node["parameters"]
                assert params.get("authentication") == "genericCredentialType"
                assert params.get("genericAuthType") == "httpHeaderAuth"
                assert "{{ $env.INTAKE_API_KEY }}" in params.get("httpHeaderAuth", "")
                break

    def test_process_node_has_timeout(self):
        workflow = load_workflow()
        for node in workflow.get("nodes", []):
            if node.get("name") == "Process Batch":
                options = node["parameters"].get("options", {})
                assert "requestTimeout" in options, (
                    "Process Batch should set requestTimeout for safety"
                )
                break

    def test_discovered_routes_to_process(self):
        workflow = load_workflow()
        connections = workflow["connections"]
        discovered_edges = connections.get("Is Discovered?", {}).get("main", [])
        targets = []
        for edge_list in discovered_edges:
            for edge in edge_list:
                targets.append(edge["node"])
        assert "Process Batch" in targets, (
            "Discovered status must route to Process Batch"
        )

    def test_pending_routes_to_process(self):
        workflow = load_workflow()
        connections = workflow["connections"]
        pending_edges = connections.get("Is Pending?", {}).get("main", [])
        targets = []
        for edge_list in pending_edges:
            for edge in edge_list:
                targets.append(edge["node"])
        assert "Process Batch" in targets, (
            "Pending status must route to Process Batch"
        )


class TestTerminalStateNonRetry:
    """Validate that terminal states never route to POST /process."""

    def test_completed_not_routed_to_process(self):
        workflow = load_workflow()
        connections = workflow["connections"]
        # Walk the IF chain; 'completed' must reach Notify: Batch Completed,
        # never Process Batch.
        completed_edges = connections.get("Batch Completed?", {}).get("main", [])
        yes_targets = []
        for edge_list in completed_edges:
            for edge in edge_list:
                yes_targets.append(edge["node"])
        assert "Notify: Batch Completed" in yes_targets
        assert "Process Batch" not in yes_targets

    def test_failed_not_routed_to_process(self):
        workflow = load_workflow()
        connections = workflow["connections"]
        failed_edges = connections.get("Batch Failed?", {}).get("main", [])
        yes_targets = []
        for edge_list in failed_edges:
            for edge in edge_list:
                yes_targets.append(edge["node"])
        assert "Notify: Batch Dead-Lettered" in yes_targets
        assert "Process Batch" not in yes_targets

    def test_no_node_routes_failed_to_process(self):
        """Defensive: no connection anywhere should send 'failed' to /process."""
        workflow = load_workflow()
        connections = workflow.get("connections", {})
        for source, edges in connections.items():
            for edge_list in edges.get("main", []):
                for edge in edge_list:
                    assert edge["node"] != "Process Batch" or source in (
                        "Is Discovered?", "Is Pending?"
                    ), (
                        f"Only Is Discovered?/Is Pending? may route to Process Batch, "
                        f"but {source} routes there"
                    )

    def test_workflow_active_false(self):
        workflow = load_workflow()
        assert workflow.get("active") is False, (
            "Workflow must be imported inactive (operator activates after review)"
        )

    def test_dead_lettered_notification_node_exists(self):
        workflow = load_workflow()
        node_names = {n["name"] for n in workflow.get("nodes", [])}
        assert "Notify: Batch Dead-Lettered" in node_names, (
            "Dead-lettered notification node missing"
        )
        assert "Notify: Processing Started" in node_names, (
            "Processing started notification node missing"
        )


