# Skryon Telegram Release Manager

Готовый модуль для действующего aiogram-бота (`orchestrator/tg_admin.py`), а **не второй бот**. Приватный ключ подписи ECDSA остаётся на компьютере разработчика. Пароли Telegram, SSH и VPN в код не встраиваются.

## Использование

Боту отправляют `SkryonRelease-26K101.json`, подписанный **с тем же издательским ключом**, что зашит в `SkryonUpdateFeed.cs`. Затем отправляют шесть `.skpart` (по 18 MiB), которые создаёт `tools/Prepare-Telegram-Release.ps1`. Бот проверяет подпись, SHA-256 и VERSIONINFO (`ProductVersion=26K101`, `FileVersion=1.0.1`). После этого показывает **«Выпустить обновление»** и отдельное подтверждение. Только после подтверждения публикует файл в `/downloads/windows` и атомарно меняет `/updates/windows/latest.json` **в последнюю очередь**.

Если отсутствует хоть одна часть, неверная подпись, версия, хеш или код версии не новее опубликованного, публикация блокируется. Ни один загруженный EXE не запускается на сервере. Обновление серверной минимальной версии не требуется для обычного релиза; принудительное ограничение старых версий управляется отдельно.

## Подготовка для сервера

В репозитории `mafimusic5-beep/nt` на безопасной **отдельной ветке** добавить `orchestrator/release_core.py` и `orchestrator/tg_release_routes.py`; запустить `python apply_integration.py /path/to/orchestrator` на копии репозитория. Не заменять `tg_admin.py` целиком. В `orchestrator/requirements.txt` появляется `pefile==2024.8.26`; `cryptography` и `aiogram` уже имеются.

Настройки systemd-бота/сервера:

```
SKRYON_RELEASE_STAGING_DIR=/var/lib/skryon-releases
SKRYON_SITE_ROOT=/var/www/skryon.ru/html
SKRYON_RELEASE_PUBLISH_ENABLED=0
```

Выдать пользователю systemd-бота запись только в его приватный staging, а также в определённые каталоги публичных релизов; **не давать запись ко всему веб-корню**. Гарантировать, что Nginx отдаёт URL с поддержкой больших файлов и HTTPS. Приём документов возможен только от `ADMIN_IDS` в приватном чате.

Сначала развернуть с `SKRYON_RELEASE_PUBLISH_ENABLED=0`, прогнать ручной тест отправки всех частей, проверить подпись и VERSIONINFO. После **QA на чистых Windows 10/11, подписания исполняемого файла Authenticode, проверки маршрута скачивания и бэкапа** отдельно включить `SKRYON_RELEASE_PUBLISH_ENABLED=1` и перезапустить бот. Без этой настройки кнопка публикации не появляется, данные остаются только в приватном staging.

### Почему шесть частей, а не один EXE?

Стандартный облачный Telegram Bot API ограничивает `getFile` для больших загрузок, поэтому локальная подготовка режет 105 MiB на шесть документов по 18 MiB. Для полноценного режима «отправил один EXE» потребуется поднять **локальный Telegram Bot API сервер** и перенаправить туда существующий токен. Пока **нельзя обещать**, что обычный бот сможет скачать установщик целиком.

### Скрипт подготовки подписанного JSON

```
dotnet run --project tools/ReleaseManifestTool -c Release -- sign <installer.exe> <versionCode> <version> https://skryon.ru/downloads/windows/SkryonSetup-<version>.exe <SkryonRelease-BUILD.json>
dotnet run --project tools/ReleaseManifestTool -c Release -- verify <installer.exe> <SkryonRelease-BUILD.json>
```

**Важно:** подписывать манифест следует после финальной сборки/подписи исполняемого файла, иначе SHA-256 изменится. Не публиковать до окончания QA. GitHub Action `deploy-orchestrator.yml` в доступном коде настроен на другого хоста; без проверки нельзя запускать деплой. В ходе текущей подготовки доступ к IONOS по имеющимся SSH-ключам был отклонён, поэтому модуль не активирован на живом сервере.
