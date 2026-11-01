@echo off
setlocal EnableExtensions

for %%I in ("%~dp0..") do set "REPOSITORY_ROOT=%%~fI"

for %%H in (pre-commit pre-push post-checkout post-commit post-merge commit-msg) do (
  if not exist "%REPOSITORY_ROOT%\.githooks\%%H" (
    echo Required repository hook is missing: %REPOSITORY_ROOT%\.githooks\%%H 1>&2
    exit /b 1
  )
)

git -C "%REPOSITORY_ROOT%" config core.hooksPath .githooks
if errorlevel 1 (
  echo Unable to configure core.hooksPath. 1>&2
  exit /b 1
)

for /f "usebackq delims=" %%V in (`git -C "%REPOSITORY_ROOT%" config --get core.hooksPath`) do (
  set "CONFIGURED_HOOKS_PATH=%%V"
)
if not "%CONFIGURED_HOOKS_PATH%"==".githooks" (
  echo core.hooksPath validation failed: %CONFIGURED_HOOKS_PATH% 1>&2
  exit /b 1
)

"%REPOSITORY_ROOT%\.venv\Scripts\python.exe" -B -m dual_pipeline.runtime_evidence enforce-controls --repository-root "%REPOSITORY_ROOT%"
if errorlevel 1 (
  echo Runtime-evidence hook control validation failed. 1>&2
  exit /b 1
)

echo Repository hooks installed at .githooks; Git LFS forwarding preserved.
