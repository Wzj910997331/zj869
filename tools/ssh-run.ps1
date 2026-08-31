# ssh-run.ps1 — run remote commands on 10.5.64.5 as root (password via askpass.cmd)
#
# Usage:
#   -Cmd "<inline remote command>"        run one command
#   -Script "path\to\remote.sh"           pipe a local bash script to `bash -s`
param(
  [string]$Cmd,
  [string]$Script
)
$env:SSH_ASKPASS = "C:\Users\zhenjie.wu\.dsh\secrets\askpass.cmd"
$env:SSH_ASKPASS_REQUIRE = "force"
try {
  if ($Script) {
    $raw = (Get-Content $Script -Raw -Encoding UTF8) -replace "`r`n", "`n"
    $raw | ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 root@10.5.64.5 "bash -s" 2>&1
  } elseif ($Cmd) {
    ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 root@10.5.64.5 $Cmd 2>&1
  } else {
    Write-Error "provide -Cmd or -Script"
  }
} finally {
  Remove-Item Env:SSH_ASKPASS -ErrorAction SilentlyContinue
  Remove-Item Env:SSH_ASKPASS_REQUIRE -ErrorAction SilentlyContinue
}
