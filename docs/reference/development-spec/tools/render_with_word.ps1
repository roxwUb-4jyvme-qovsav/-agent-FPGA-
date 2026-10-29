param([string]$Tag='最终')
$ErrorActionPreference='Stop'
$specRoot=[IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$specSource=Join-Path $specRoot 'FPGA多Agent协同开发平台开发规格书_v1.0.docx'
$specOut=Join-Path $specRoot ('内部校验\渲染'+$Tag)
New-Item -ItemType Directory -Path $specOut -Force | Out-Null
$specPdf=Join-Path $specOut 'specification.pdf'
$specBackup=Join-Path $specRoot ('内部校验\备份\'+(Get-Date -Format 'yyyyMMdd-HHmmss-ffff'))
New-Item -ItemType Directory -Path $specBackup -Force | Out-Null
Copy-Item -LiteralPath $specSource -Destination (Join-Path $specBackup 'before-word.docx')
$specWord=$null;$specDoc=$null;$specOwned=$false
try {
    $specWord=New-Object -ComObject Word.Application
    if($specWord.Documents.Count -ne 0){throw 'Word instance contains user documents; refusing to alter that instance.'}
    $specOwned=$true;$specWord.Visible=$false;$specWord.DisplayAlerts=0;$specWord.AutomationSecurity=3
    $specDoc=$specWord.Documents.Open($specSource,$false,$false,$false)
    $specDoc.Fields.Update() | Out-Null
    foreach($specToc in $specDoc.TablesOfContents){
        $specToc.Update();$specToc.Range.Font.Name='Microsoft YaHei';$specToc.Range.Font.Size=10
        $specToc.Range.ParagraphFormat.SpaceBefore=0;$specToc.Range.ParagraphFormat.SpaceAfter=2
    }
    $specDoc.Repaginate()
    foreach($specToc in $specDoc.TablesOfContents){$specToc.UpdatePageNumbers()}
    $specDoc.Save()
    $specMap=@()
    foreach($specPara in $specDoc.Paragraphs){
        $specText=$specPara.Range.Text.Trim()
        if($specText -and $specPara.OutlineLevel -le 2){$specMap += [pscustomobject]@{text=$specText;page=$specPara.Range.Information(3);level=$specPara.OutlineLevel}}
    }
    $specPages=$specDoc.ComputeStatistics(2)
    $specDoc.ExportAsFixedFormat($specPdf,17)
    [pscustomobject]@{pages=$specPages;headings=$specMap;pdf=$specPdf} | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $specOut 'layout.json') -Encoding utf8
    $specDoc.Close(0);$specDoc=$null
    [pscustomobject]@{pages=$specPages;pdf=$specPdf} | ConvertTo-Json -Compress
} finally {
    if($null -ne $specDoc){$specDoc.Close(0)}
    if($specOwned -and $null -ne $specWord){$specWord.Quit()}
    if($null -ne $specWord){[void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($specWord)}
}
& 'C:\Users\19778\.cache\codex-runtimes\codex-primary-runtime\dependencies\native\poppler\Library\bin\pdftoppm.exe' -r 96 -png $specPdf (Join-Path $specOut 'page')
if($LASTEXITCODE -ne 0){throw 'PDF rasterization failed'}
