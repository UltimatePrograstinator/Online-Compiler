"""
Personal online compiler backend.
Accepts a source file + language, compiles it to a Windows .exe, returns the file.

IMPORTANT — read the README before running this:
- C++ and C# can be compiled here even if this server runs on Linux (cross-compile).
- Python (PyInstaller) can ONLY produce a Windows .exe if this server itself runs on
  Windows. If you run this on Linux, the /compile/python endpoint will fail loudly
  rather than silently producing a broken file.
"""

import os
import shutil
import subprocess
import tempfile
import uuid
import platform

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

app = FastAPI(title="Personal Online Compiler")

# --- CORS ---------------------------------------------------------------
# Personal project, no untrusted users -> wide open is fine.
# If you want to lock it down later, replace "*" with your github.io URL,
# e.g. "https://yourusername.github.io"
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Where finished .exe files are temporarily kept before download
OUTPUT_DIR = os.path.join(tempfile.gettempdir(), "compiler_outputs")
os.makedirs(OUTPUT_DIR, exist_ok=True)


def run_cmd(cmd, cwd):
    """Run a build command, raise a readable error if it fails."""
    result = subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=180,  # 3 min safety timeout so a bad build doesn't hang forever
    )
    if result.returncode != 0:
        raise HTTPException(
            status_code=400,
            detail=f"Build failed:\n{result.stdout}\n{result.stderr}",
        )


@app.get("/")
def health():
    return {"status": "ok", "platform": platform.system()}


# --- C++ ------------------------------------------------------------------
@app.post("/compile/cpp")
async def compile_cpp(file: UploadFile = File(...)):
    job_id = str(uuid.uuid4())
    work_dir = os.path.join(tempfile.gettempdir(), f"job_{job_id}")
    os.makedirs(work_dir, exist_ok=True)

    src_path = os.path.join(work_dir, "main.cpp")
    with open(src_path, "wb") as f:
        f.write(await file.read())

    exe_name = "output.exe"
    exe_path = os.path.join(work_dir, exe_name)

    # Works both for:
    # - native g++ on Windows with MinGW-w64/MSYS2 installed
    # - cross-compiling g++ on Linux via x86_64-w64-mingw32-g++
    # Pick whichever binary exists on this machine.
    compiler = shutil.which("x86_64-w64-mingw32-g++") or shutil.which("g++")
    if not compiler:
        raise HTTPException(500, "No C++ compiler found on this server (need g++ or mingw-w64).")

    run_cmd([compiler, src_path, "-o", exe_path, "-O2", "-static"], cwd=work_dir)

    return _return_exe(exe_path, job_id, "output.exe")


# --- C# ---------------------------------------------------------------
@app.post("/compile/csharp")
async def compile_csharp(file: UploadFile = File(...)):
    job_id = str(uuid.uuid4())
    work_dir = os.path.join(tempfile.gettempdir(), f"job_{job_id}")
    os.makedirs(work_dir, exist_ok=True)

    src_path = os.path.join(work_dir, "Program.cs")
    with open(src_path, "wb") as f:
        f.write(await file.read())

    # dotnet needs a project file - generate a minimal one on the fly
    csproj = """<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <OutputType>Exe</OutputType>
    <TargetFramework>net8.0</TargetFramework>
    <ImplicitUsings>enable</ImplicitUsings>
    <Nullable>enable</Nullable>
    <AssemblyName>output</AssemblyName>
  </PropertyGroup>
</Project>
"""
    with open(os.path.join(work_dir, "app.csproj"), "w") as f:
        f.write(csproj)

    if not shutil.which("dotnet"):
        raise HTTPException(500, "dotnet SDK not found on this server.")

    publish_dir = os.path.join(work_dir, "publish")
    run_cmd(
        [
            "dotnet", "publish",
            "-c", "Release",
            "-r", "win-x64",
            "--self-contained", "true",
            "-p:PublishSingleFile=true",
            "-o", publish_dir,
        ],
        cwd=work_dir,
    )

    exe_path = os.path.join(publish_dir, "output.exe")
    if not os.path.exists(exe_path):
        raise HTTPException(500, "Build reported success but output.exe was not found.")

    return _return_exe(exe_path, job_id, "output.exe")


# --- Python -----------------------------------------------------------
@app.post("/compile/python")
async def compile_python(file: UploadFile = File(...)):
    if platform.system() != "Windows":
        raise HTTPException(
            500,
            "This server is not running on Windows. PyInstaller can only build a "
            "Windows .exe when run ON Windows. Run this backend on a Windows "
            "machine to enable Python compilation.",
        )

    job_id = str(uuid.uuid4())
    work_dir = os.path.join(tempfile.gettempdir(), f"job_{job_id}")
    os.makedirs(work_dir, exist_ok=True)

    src_path = os.path.join(work_dir, "script.py")
    with open(src_path, "wb") as f:
        f.write(await file.read())

    if not shutil.which("pyinstaller"):
        raise HTTPException(500, "pyinstaller not found on this server (pip install pyinstaller).")

    run_cmd(
        ["pyinstaller", "--onefile", "--distpath", work_dir, "--workpath",
         os.path.join(work_dir, "build"), "--specpath", work_dir, src_path],
        cwd=work_dir,
    )

    exe_path = os.path.join(work_dir, "script.exe")
    if not os.path.exists(exe_path):
        raise HTTPException(500, "Build reported success but script.exe was not found.")

    return _return_exe(exe_path, job_id, "script.exe")


def _return_exe(exe_path: str, job_id: str, download_name: str):
    final_path = os.path.join(OUTPUT_DIR, f"{job_id}_{download_name}")
    shutil.copy(exe_path, final_path)
    return FileResponse(
        final_path,
        media_type="application/octet-stream",
        filename=download_name,
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
