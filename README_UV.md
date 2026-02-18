### Installation and Setup

**Mac:**
```bash
brew install uv
```

**Windows:**
```bash
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
set Path=C:\Users\[username]\.local\bin;%Path%
```

For other platforms, see the [uv installation guide](https://docs.astral.sh/uv/getting-started/installation/).

### Setting Up the Project:

1. Fork or clone the repo: 
   ```
   https://github.com/mcp-servers-for-revit/mcp-server-for-revit-python
   ```

2. Create and activate a virtual environment:
   ```bash
   # Create virtual environment
   uv venv
   
   # Activate it (Linux/Mac)
   source .venv/bin/activate
   
   # Activate it (Windows)
   .venv\Scripts\activate
   
   # Install requirements
   uv pip install -r requirements.txt
   ```