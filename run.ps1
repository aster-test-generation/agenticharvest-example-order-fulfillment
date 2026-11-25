#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

<#
Run this example with nothing installed but Podman or Docker.

  .\run.ps1            stages 1-4
  .\run.ps1 --agent    + stage 5 (set $env:API_BASE, $env:API_KEY, $env:MODEL first)
  .\run.ps1 --keep     leave this run's records in the live systems

It starts the live systems and runs the example inside a container; see
..\systems\README.md (or the agenticharvest-systems repo) for what that does.
#>
$ErrorActionPreference = "Stop"
$here = $PSScriptRoot
# By name, not by folder: a standalone clone's folder is the repository name.
$example = "order_fulfillment"
# A .env next to this script (API_BASE, API_KEY, MODEL) is read by the runner.
$env:AGENTICHARVEST_CALLER_DIR = $here

$sibling = Join-Path $here "..\systems\run.ps1"
if (Test-Path $sibling) { & $sibling $example @args; exit $LASTEXITCODE }

# Cloned on its own: fetch agenticharvest, which carries the stack, and run from it.
$cache = Join-Path $here ".agenticharvest"
$runner = Join-Path $cache "examples\systems\run.ps1"
if (-not (Test-Path $runner)) {
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) { Write-Error "git is needed to fetch agenticharvest." }
    $repo = if ($env:AGENTICHARVEST_REPO) { $env:AGENTICHARVEST_REPO } else { "https://github.com/IBM/agenticharvest.git" }
    $ref = if ($env:AGENTICHARVEST_REF) { $env:AGENTICHARVEST_REF } else { "main" }
    Write-Host "Fetching agenticharvest ($repo, $ref) into $cache ..."
    git clone --depth 1 --recurse-submodules --shallow-submodules --branch $ref $repo $cache
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
& $runner $example @args
exit $LASTEXITCODE
