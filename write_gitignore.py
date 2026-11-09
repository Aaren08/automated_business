import os

gitignore_content = """.env
"""

wsl_path = "/home/abdal_asad/n8n/.gitignore"
os.makedirs(os.path.dirname(wsl_path), exist_ok=True)
with open(wsl_path, "w") as f:
    f.write(gitignore_content)
print(".gitignore written to WSL")