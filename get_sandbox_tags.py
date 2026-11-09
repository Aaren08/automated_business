import sys
import json
import urllib.request

# Check for sandbox-service images
repos = [
    "n8nio/sandbox-service",
    "n8nio/sandbox-api", 
    "n8nio/sandbox-runner",
    "n8nio/sandbox-certs"
]

for repo in repos:
    print(f"\n=== {repo} ===")
    try:
        url = f"https://registry.hub.docker.com/v2/repositories/{repo}/tags/?page_size=50"
        with urllib.request.urlopen(url) as response:
            data = json.load(response)
        
        tags = [t["name"] for t in data["results"] 
                if not t["name"].startswith("sha") 
                and "nightly" not in t["name"] 
                and "alpha" not in t["name"] 
                and "beta" not in t["name"] 
                and "rc" not in t["name"]]
        
        for t in sorted(tags, reverse=True)[:10]:
            print(t)
    except Exception as e:
        print(f"Error: {e}")