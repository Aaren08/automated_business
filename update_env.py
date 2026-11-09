import os

env_content = """# n8n version - get the version number of the latest stable release from https://github.com/n8n-io/n8n/releases
N8N_VERSION=2.42.1

# Sandbox service version - the API, runner, and sandbox images share one release.
# Get the number of the latest stable "service" release (not a staging prerelease) from https://github.com/n8n-io/n8n-sandbox-service/releases
N8N_SANDBOX_VERSION=1.5.0

# n8n task runner authentication. Use a random secret you generate.
N8N_RUNNERS_AUTH_TOKEN=FuZ1h5TbJ/4T/0z80FbcXkgs1SUrqwGHO75xKGCwtRw=

# Sandbox service secrets — pick your own values
SANDBOX_API_KEYS=AC9jh1iKOTA/ShTZHrhbIKSvoWw5MPpcbdsCB4YaSA4=
SANDBOX_API_RUNNER_REGISTRATION_TOKEN=5MlDbH73fffWRbHoJI4anLIRVgCLSAvgB9FLxz0tYa8=
SANDBOX_API_RUNNER_API_KEY=MHimbQxO52Od5Uc97Z6qf8UngL4ZJm2tC/PnSfKh958=

# Must match a value in SANDBOX_API_KEYS above — this is how n8n authenticates to the sandbox
N8N_SANDBOX_SERVICE_API_KEY=AC9jh1iKOTA/ShTZHrhbIKSvoWw5MPpcbdsCB4YaSA4=

# Web search: secret for the bundled SearXNG instance — pick your own value
SEARXNG_SECRET=NmOZpU2x2flEN4MmEdNExUfsV/vDcEHQmj8MmoLTjSY=
N8N_INSTANCE_AI_SEARXNG_URL=http://searxng:8080
"""

wsl_path = "/home/abdal_asad/n8n/.env"
with open(wsl_path, "w") as f:
    f.write(env_content)
print(".env updated with correct sandbox version")