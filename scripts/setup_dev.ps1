# ==============================================================================
# Скрипт локального развертывания проекта для разработки (Windows / PowerShell)
# Использование: .\scripts\setup_dev.ps1
# Или:           . .\scripts\setup_dev.ps1  (с точкой для сохранения активации .venv в текущей консоли)
# ==============================================================================

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

# Переход в корневой каталог проекта
$ProjectDir = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectDir
Write-Host "==> Рабочая директория: $ProjectDir" -ForegroundColor Cyan

# ------------------------------------------------------------------------------
# 1. Проверка и создание .env
# ------------------------------------------------------------------------------
$EnvFile = Join-Path $ProjectDir ".env"
$EnvExample = Join-Path $ProjectDir ".env.example"

if (-not (Test-Path $EnvFile)) {
    if (Test-Path $EnvExample) {
        Write-Host "==> Создание .env из .env.example..." -ForegroundColor Yellow
        Copy-Item $EnvExample $EnvFile
    } else {
        Write-Host "==> Внимание: .env.example не найден. Создайте .env вручную." -ForegroundColor Red
    }
} else {
    Write-Host "==> Файл .env уже существует." -ForegroundColor Green
}

# ------------------------------------------------------------------------------
# 2. Создание и активация виртуального окружения Python (.venv)
# ------------------------------------------------------------------------------
$VenvDir = Join-Path $ProjectDir ".venv"
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
$VenvActivate = Join-Path $VenvDir "Scripts\Activate.ps1"

# Проверка версии существующего .venv (требуется >= 3.11 для enum.StrEnum)
if (Test-Path $VenvPython) {
    try {
        $currentVer = & $VenvPython -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
        $parts = $currentVer.Split('.')
        if ($parts.Count -ge 2 -and ([int]$parts[0] -lt 3 -or ([int]$parts[0] -eq 3 -and [int]$parts[1] -lt 11))) {
            Write-Host "==> Текущий .venv создан на Python $currentVer, а для проекта требуется Python 3.11+ (StrEnum). Пересоздаем .venv..." -ForegroundColor Yellow
            Remove-Item -Recurse -Force $VenvDir
        }
    } catch {}
}

if (-not (Test-Path $VenvPython)) {
    Write-Host "==> Создание виртуального окружения (.venv)..." -ForegroundColor Yellow
    # Поиск подходящего интерпретатора Python 3.11+
    $CandidatePythons = @("py -3.12", "python3.12", "C:\Python3.12\python.exe", "python", "py -3")
    $SelectedPythonCmd = $null

    foreach ($candidate in $CandidatePythons) {
        try {
            $testCmd = "$candidate -c `"import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')`""
            $ver = Invoke-Expression $testCmd 2>$null
            if ($ver) {
                $p = $ver.Split('.')
                if ($p.Count -ge 2 -and ([int]$p[0] -gt 3 -or ([int]$p[0] -eq 3 -and [int]$p[1] -ge 11))) {
                    $SelectedPythonCmd = $candidate
                    break
                }
            }
        } catch {}
    }

    if (-not $SelectedPythonCmd) {
        throw "Не найден Python версии 3.11 или выше. Установите Python 3.11 или 3.12 (в Dockerfile используется Python 3.12)."
    }

    Write-Host "    Выбран интерпретатор: $SelectedPythonCmd" -ForegroundColor Gray
    Invoke-Expression "$SelectedPythonCmd -m venv .venv"
}

Write-Host "==> Активация виртуального окружения..." -ForegroundColor Green
. $VenvActivate

# ------------------------------------------------------------------------------
# 3. Обновление pip и установка зависимостей
# ------------------------------------------------------------------------------
Write-Host "==> Обновление pip..." -ForegroundColor Yellow
& $VenvPython -m pip install --upgrade pip

Write-Host "==> Установка основных зависимостей (requirements.txt)..." -ForegroundColor Yellow
& $VenvPython -m pip install --prefer-binary -r requirements.txt

if (Test-Path (Join-Path $ProjectDir "requirements-dev.txt")) {
    Write-Host "==> Установка зависимостей для разработки (requirements-dev.txt)..." -ForegroundColor Yellow
    & $VenvPython -m pip install -r requirements-dev.txt
}

# Настройка pre-commit хуков
$PreCommitExe = Join-Path $VenvDir "Scripts\pre-commit.exe"
if (Test-Path $PreCommitExe) {
    Write-Host "==> Настройка pre-commit хуков git..." -ForegroundColor Yellow
    & $PreCommitExe install
    & $PreCommitExe install --hook-type pre-push
}

# ------------------------------------------------------------------------------
# 4. Запуск контейнеров БД и Redis (Docker)
# ------------------------------------------------------------------------------
Write-Host "==> Проверка Docker..." -ForegroundColor Yellow
try {
    docker info > $null 2>&1
} catch {
    throw "Docker не запущен или недоступен. Запустите Docker Desktop и повторите попытку."
}

Write-Host "==> Запуск контейнеров инфраструктуры (PostgreSQL и Redis)..." -ForegroundColor Yellow
docker compose up -d db redis

# Чтение настроек подключения к БД из .env
$DbUser = "db_user"
$DbPass = "db_pass"
$DbName = "get_balance_db"
$DbPort = "5432"

if (Test-Path $EnvFile) {
    Get-Content $EnvFile | ForEach-Object {
        $line = $_.Trim()
        if (-not $line.StartsWith("#") -and $line -match "^([^=]+)=(.*)$") {
            $key = $matches[1].Trim()
            $val = $matches[2].Trim()
            if ($key -eq "POSTGRES_USER" -and $val) { $DbUser = $val }
            if ($key -eq "POSTGRES_PASSWORD" -and $val) { $DbPass = $val }
            if ($key -eq "POSTGRES_DB" -and $val) { $DbName = $val }
            if ($key -eq "POSTGRES_PORT" -and $val) { $DbPort = $val }
        }
    }
}

# Ожидание готовности PostgreSQL
Write-Host "==> Ожидание готовности PostgreSQL к приему подключений..." -ForegroundColor Yellow
$MaxRetries = 30
$RetryCount = 0
$DbReady = $false

while (-not $DbReady -and $RetryCount -lt $MaxRetries) {
    Start-Sleep -Seconds 1
    $RetryCount++
    $check = docker compose exec -T db pg_isready -U $DbUser -d $DbName 2>&1
    if ($LASTEXITCODE -eq 0) {
        $DbReady = $true
        Write-Host "==> PostgreSQL готов к работе!" -ForegroundColor Green
        break
    }
    Write-Host "    Ожидание базы данных... ($RetryCount/$MaxRetries)" -ForegroundColor Gray
}

if (-not $DbReady) {
    throw "Таймаут ожидания запуска PostgreSQL. Проверьте логи: docker compose logs db"
}

# ------------------------------------------------------------------------------
# 5. Применение миграций базы данных (Alembic)
# ------------------------------------------------------------------------------
Write-Host "==> Применение миграций БД (Alembic)..." -ForegroundColor Yellow
$env:DATABASE_URL = "postgresql://${DbUser}:${DbPass}@127.0.0.1:${DbPort}/${DbName}"
$AlembicExe = Join-Path $VenvDir "Scripts\alembic.exe"

& $AlembicExe upgrade head

Write-Host "==> Миграции успешно применены!" -ForegroundColor Green

# ------------------------------------------------------------------------------
# 6. Итоги и инструкции
# ------------------------------------------------------------------------------
Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host " Развертывание локального окружения успешно завершено!      " -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "1. Инфраструктура (PostgreSQL, Redis) запущена в Docker."
Write-Host "2. Виртуальное окружение активировано: $VenvDir"
Write-Host "3. Все зависимости и git hooks установлены."
Write-Host "4. Миграции БД применены."
Write-Host ""
Write-Host "Для запуска сервера разработки выполните:" -ForegroundColor Yellow
Write-Host "  uvicorn main:app --reload --host 0.0.0.0 --port 8000" -ForegroundColor White
Write-Host ""
Write-Host "Для запуска тестов:" -ForegroundColor Yellow
Write-Host "  pytest" -ForegroundColor White
Write-Host "============================================================" -ForegroundColor Cyan