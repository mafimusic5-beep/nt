# Google Play / VpnService disclosure for Skryon

## In-app prominent disclosure

**VPN-подключение Skryon**

Skryon использует Android VpnService для создания зашифрованного VPN-туннеля между устройством и выбранным VPN-сервером.

Во время работы VPN приложение направляет сетевой трафик устройства через выбранный VPN-сервер. Skryon не использует VpnService для рекламной монетизации, не продаёт пользовательский трафик и не сохраняет историю посещённых сайтов или содержимое VPN-трафика в VPN access-логах.

Нажимая «Продолжить», пользователь соглашается на использование Android VpnService для создания VPN-подключения.

Кнопки: **Продолжить** / **Отмена**

## Google Play listing disclosure

Skryon uses Android VpnService as its core functionality to establish an encrypted tunnel between the user's device and the selected VPN server. Skryon does not use VPN traffic for advertising monetization, does not sell user traffic, and does not retain browsing history or traffic content in VPN access logs.

## VpnService declaration

- Is providing a VPN the core functionality? **Yes**
- Does the app redirect or manipulate traffic for monetization? **No**
- The tunnel from the device to the VPN endpoint is encrypted.
- The declaration video should show: opening Skryon, the full disclosure, tapping Continue, the Android VPN permission dialog, successful VPN connection, and the cancel/refusal path.

## Data Safety notes

Technical device identity used for subscription/device binding should be declared under **Device or other IDs** when it is transmitted to Skryon backend. Intended purposes: **App functionality**, **Fraud prevention, security, and compliance**, and where applicable **Account management**.

The installed-app list used for per-app VPN selection is processed locally and is not transmitted to Skryon by that feature.
