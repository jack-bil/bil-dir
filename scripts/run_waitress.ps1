param(
  [int]$Port = 5025,
  [string]$Host = "0.0.0.0"
)

$env:PORT = "$Port"
waitress-serve --listen=$Host:$Port app:APP
