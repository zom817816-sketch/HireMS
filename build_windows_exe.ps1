$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

python -m pip install -r requirements.txt
python -m PyInstaller --noconfirm --clean --onefile --name HireMS `
  --add-data "static;static" `
  --add-data ".env.example;." `
  --collect-submodules chromadb `
  launcher.py

Write-Host "已生成：$PSScriptRoot\dist\HireMS.exe"
