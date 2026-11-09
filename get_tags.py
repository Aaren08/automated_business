import sys
import json
import urllib.request

url = "https://registry.hub.docker.com/v2/repositories/n8nio/n8n/tags/?page_size=200"
with urllib.request.urlopen(url) as response:
    data = json.load(response)

tags = [t["name"] for t in data["results"] 
        if not t["name"].startswith("sha") 
        and "nightly" not in t["name"] 
        and "alpha" not in t["name"] 
        and "beta" not in t["name"] 
        and "rc" not in t["name"]]

for t in sorted(tags, reverse=True)[:20]:
    print(t)