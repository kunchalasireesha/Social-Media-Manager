# SMM
Social Media Manager

## Virtual Environment Setup

Follow these step-by-step instructions to set up a Python virtual environment in this directory using Windows PowerShell.

### Step 1: Open Terminal in the Workspace Directory
Ensure your terminal is located in the project directory. You can check your current location by running:
```powershell
Get-Location
```

### Step 2: Create the Virtual Environment
Run the built-in `venv` module to create a virtual environment folder named `.venv`:
```powershell
python -m venv .venv
```

### Step 3: Allow Script Execution (PowerShell Only)
PowerShell often restricts running scripts by default. Allow script execution for the current session:
```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope Process
```

### Step 4: Activate the Virtual Environment
Activate the environment using the PowerShell activation script:
```powershell
.\.venv\Scripts\Activate.ps1
```

*(If using Command Prompt (`cmd`) instead of PowerShell, the command is `.\.venv\Scripts\activate.bat`)*

### Step 5: Upgrade `pip`
Upgrade `pip` to the latest version before installing dependencies:
```powershell
python -m pip install --upgrade pip
```

### Step 6: Install Workspace Dependencies
Install project dependencies using `requirements.txt` or `pyproject.toml`:

- **Option A: Using `requirements.txt`**
  ```powershell
  pip install -r requirements.txt
  ```

- **Option B: Using `pyproject.toml`**
  ```powershell
  pip install -e .
  ```

### Step 7: Select the Interpreter in VS Code
To ensure VS Code and Jupyter Notebooks (`smm.ipynb`) use this new virtual environment:

1. Press `Ctrl + Shift + P` to open the Command Palette.
2. Select **Python: Select Interpreter**.
3. Choose the environment path: `.\.venv\Scripts\python.exe`.

---

### Deactivating the Environment
When you finish working, deactivate the environment by running:
```powershell
deactivate
```

