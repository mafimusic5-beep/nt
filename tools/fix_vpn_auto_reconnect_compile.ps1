$root = Resolve-Path (Join-Path $PSScriptRoot '..')
$mmkv = Join-Path $root 'app/src/main/java/com/v2ray/ang/handler/MmkvManager.kt'

$content = Get-Content $mmkv -Raw -Encoding UTF8

if ($content -notmatch 'import com\.v2ray\.ang\.AppConfig\.PREF_AUTO_START_VPN') {
    $content = $content.Replace(
        'import com.v2ray.ang.AppConfig.PREF_IS_BOOTED',
        "import com.v2ray.ang.AppConfig.PREF_IS_BOOTED`r`nimport com.v2ray.ang.AppConfig.PREF_AUTO_RECONNECT`r`nimport com.v2ray.ang.AppConfig.PREF_AUTO_START_VPN"
    )
}

Set-Content $mmkv $content -Encoding UTF8
Write-Host 'patched MmkvManager.kt imports for auto reconnect'
