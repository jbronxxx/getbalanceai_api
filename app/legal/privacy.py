"""Политика конфиденциальности GetBalance AI (ru, en)."""

from app.legal.types import PageContent
from app.legal.ui import SUPPORT_EMAIL

PRIVACY: dict[str, PageContent] = {
    "ru": {
        "title": "Политика конфиденциальности GetBalance AI",
        "sections": [
            (
                "1. Введение",
                [
                    "GetBalance AI («мы») — мобильное приложение для учёта личных финансов с ИИ-аналитикой. "
                    "Разработчик и оператор данных — Yevgeniy Makarov (Республика Узбекистан, г. Ташкент), "
                    f"контактный email: {SUPPORT_EMAIL}. Эта Политика объясняет, какие данные мы обрабатываем, "
                    "зачем и какие права у вас есть.",
                    "Приложение предназначено для пользователей в Европейском Союзе и Европейской экономической "
                    "зоне, Великобритании, Швейцарии, США, Канаде и Австралии.",
                ],
            ),
            (
                "2. Какие данные мы собираем",
                [
                    [
                        "Учётная запись: адрес электронной почты, имя и (при наличии) фото профиля.",
                        "Вход через Google или Apple: идентификатор аккаунта, email, имя и фото, которые "
                        "передаёт провайдер. Пароль от аккаунта Google или Apple нам недоступен.",
                        "Финансовые данные: операции, категории, бюджеты, регулярные платежи и базовая валюта, "
                        "которые вы вносите сами. В гостевом режиме они хранятся только на устройстве и на "
                        "сервер не передаются.",
                        "Технические данные в серверных технических журналах — для безопасности и стабильной "
                        "работы: IP-адрес, идентификатор аккаунта, версия приложения, модель устройства, версия "
                        "операционной системы, язык интерфейса и метаданные запросов (адрес, время и результат "
                        "запроса, код ошибки). Содержимое запросов — суммы, описания операций, токены входа — в "
                        "журналы не записывается.",
                        "Принятие условий: версии принятых Пользовательского соглашения и Политики, дата и время "
                        "принятия, язык интерфейса, платформа и версия приложения.",
                        "Отчёты о сбоях (только в Android-версии): модель устройства, версия операционной "
                        "системы и приложения, идентификатор установки и техническое описание ошибки. Имя, "
                        "email и финансовые данные в отчёты о сбоях не добавляются.",
                        "Голосовой ввод (если вы им пользуетесь, нужен доступ к микрофону): речь распознаёт "
                        "сервис распознавания речи вашего устройства, приложение не записывает и не хранит звук. "
                        "Распознанный текст фразы передаётся на наш сервер только для разбора на записи, "
                        "которые вы подтверждаете сами; текст не сохраняется и не попадает в журналы.",
                        "Подписка (если вы её оформляете): тариф, продукт, магазин покупки, даты начала, продления "
                        "и окончания, пробный период и история покупок — вместе с идентификатором аккаунта. Данные "
                        "банковской карты нам недоступны.",
                    ],
                ],
            ),
            (
                "3. Как мы используем данные",
                [
                    [
                        "Создание и управление вашей учётной записью.",
                        "Подтверждение того, что вы приняли Пользовательское соглашение и Политику.",
                        "Синхронизация операций, бюджетов и регулярных платежей между устройствами.",
                        "Формирование персонализированной финансовой аналитики с помощью ИИ.",
                        "Разбор фраз голосового ввода на записи.",
                        "Предоставление тарифа Pro и учёт лимитов функций по тарифу.",
                        "Поиск и исправление ошибок в приложении.",
                        "Защита от злоупотреблений, поддержка и улучшение сервиса.",
                    ],
                    "Мы не продаём ваши данные и не используем их для рекламы. Напоминания о регулярных "
                    "платежах формируются на устройстве.",
                ],
            ),
            (
                "4. Сторонние сервисы",
                [
                    [
                        "Anthropic (Claude API) — генерация ИИ-аналитики и разбор голосового ввода. Google "
                        "(Gemini API) — запасной поставщик: получает тот же запрос, только если Claude недоступен "
                        "или не смог ответить. Для аналитики передаются обезличенные данные месяца: даты, типы, "
                        "категории, суммы и валюта операций, а также лимиты по категориям; описания операций и "
                        "регулярных платежей не передаются. Для голосового ввода передаётся только текст фразы. "
                        "Имя, email и идентификатор пользователя не передаются. Мы используем коммерческие "
                        "(платные) тарифы обоих сервисов: переданные данные применяются только для формирования "
                        "ответа и не используются Anthropic и Google для обучения моделей ИИ; поставщик может "
                        "хранить их ограниченное время для защиты от злоупотреблений, после чего удаляет.",
                        "Google (Firebase Crashlytics) — сбор отчётов о сбоях в Android-версии (см. раздел 2). "
                        "Google обрабатывает эти данные по нашему поручению.",
                        "Google Sign-In и Sign in with Apple — вход в аккаунт.",
                        "Открытый сервис курсов валют open.er-api.com — наш сервер получает от него курсы, "
                        "а приложение обращается к нему напрямую, если наш сервер недоступен. Сервис получает "
                        "только сам запрос (в том числе IP-адрес); ваши данные ему не передаются.",
                        "Google Play и Apple App Store — распространение приложения и оплата платных функций. "
                        "Данные банковской карты обрабатывает магазин; мы их не получаем и не храним.",
                        "RevenueCat (RevenueCat, Inc., США) — учёт подписок: получает идентификатор аккаунта (без "
                        "имени и email), данные покупок из магазина и технические данные, которые передаёт его "
                        "модуль в приложении (модель устройства, версия операционной системы и приложения, страна "
                        "магазина, IP-адрес). RevenueCat обрабатывает эти данные по нашему поручению.",
                        "Хостинг-провайдер — хранение и обработка данных на серверах (см. раздел 6).",
                        "Cloudflare (хранилище R2) — хранение резервных копий базы данных вне сервера. Копии "
                        "шифруются на нашем сервере до отправки, ключа шифрования у Cloudflare нет; копии "
                        "хранятся не более 7 суток (см. раздел 5).",
                    ],
                ],
            ),
            (
                "5. Хранение и удаление",
                [
                    "Данные хранятся, пока существует ваш аккаунт. При удалении аккаунта профиль, операции, "
                    "бюджеты, регулярные платежи, сессии и кэш аналитики удаляются из базы данных безвозвратно. "
                    "Резервные копии базы данных создаются ежедневно и хранятся не более 7 суток, серверные "
                    "технические журналы — не более 30 дней; по истечении этих сроков удалённые данные "
                    "исчезают и оттуда. Данные покупателя в RevenueCat при удалении аккаунта тоже удаляются; "
                    "оформленная подписка при этом не отменяется — её нужно отменить в магазине.",
                    "Исключение — запись о принятии условий (версии документов, дата и время, язык, платформа "
                    "и версия приложения, ключевой хэш email, по которому нельзя восстановить адрес). Имени и "
                    "email в ней нет. После удаления аккаунта она хранится 3 года — чтобы при споре подтвердить, "
                    "какие условия вы приняли, — и затем удаляется.",
                ],
            ),
            (
                "6. Расположение серверов и международная передача",
                [
                    "Серверная инфраструктура GetBalance AI расположена в Европейском Союзе (г. Хельсинки, "
                    "Финляндия). Сервисы Anthropic, Google, RevenueCat и Cloudflare из раздела 4 могут "
                    "обрабатывать данные за пределами ЕС, в том числе в США; такая передача выполняется с мерами "
                    "защиты, предусмотренными законодательством ЕС о защите данных (например, стандартными "
                    "договорными условиями Европейской комиссии).",
                ],
            ),
            (
                "7. Безопасность",
                [
                    "Мы применяем технические и организационные меры защиты, включая шифрование данных при "
                    "передаче (HTTPS) и ограничение доступа к серверам. Имя и email хранятся в базе данных и её "
                    "резервных копиях в зашифрованном виде (AES-256), ключи шифрования хранятся отдельно от базы. "
                    "Однако ни одна передача данных через Интернет не может быть безопасной на 100%.",
                ],
            ),
            (
                "8. Ваши права",
                [
                    "Вы вправе запросить доступ к своим данным, их исправление, копию или удаление. "
                    "Удалить аккаунт и все данные можно в приложении (Профиль → Удалить аккаунт), "
                    "по инструкции на странице «Удаление аккаунта» или по email ниже. "
                    "Мы отвечаем на запросы в течение 30 дней.",
                ],
            ),
            (
                "9. Пользователям в ЕС, ЕЭЗ и Великобритании",
                [
                    "Для пользователей в Европейском Союзе, Европейской экономической зоне и Великобритании "
                    "данные обрабатываются в соответствии с Общим регламентом по защите данных (GDPR) и UK "
                    "GDPR. Помимо прав из раздела 8 вы вправе ограничить обработку своих данных, возразить "
                    "против неё, получить данные в машиночитаемом формате и подать жалобу в надзорный орган "
                    "по защите данных своей страны.",
                ],
            ),
            (
                "10. Возраст",
                ["Приложение не предназначено для лиц младше 18 лет, и мы сознательно не собираем их данные."],
            ),
            (
                "11. Изменения политики",
                [
                    "Мы можем обновлять Политику. Актуальная версия и дата вступления в силу всегда указаны "
                    "на этой странице.",
                ],
            ),
            ("12. Контакты", ["Вопросы и запросы по данным направляйте на email:"]),
        ],
    },
    "en": {
        "title": "GetBalance AI Privacy Policy",
        "sections": [
            (
                "1. Introduction",
                [
                    'GetBalance AI ("we") is a personal finance mobile app with AI-powered insights. The developer '
                    "and data operator is Yevgeniy Makarov (Republic of Uzbekistan, Tashkent), contact email: "
                    f"{SUPPORT_EMAIL}. This Policy explains what data we process, why, and what rights you have.",
                    "The app is intended for users in the European Union and the European Economic Area, the "
                    "United Kingdom, Switzerland, the United States, Canada and Australia.",
                ],
            ),
            (
                "2. Data we collect",
                [
                    [
                        "Account: email address, name and, if available, profile photo.",
                        "Google or Apple sign-in: account identifier, email, name and photo provided by the "
                        "provider. We never receive your Google or Apple password.",
                        "Financial data: transactions, categories, budgets, recurring payments and base currency "
                        "that you enter yourself. In guest mode they are stored only on your device and are not "
                        "sent to the server.",
                        "Technical data in server technical logs, used for security and stability: IP address, "
                        "account identifier, app version, device model, operating system version, interface "
                        "language and request metadata (address, time and outcome of the request, error code). "
                        "Request contents such as amounts, transaction descriptions and sign-in tokens are not "
                        "written to logs.",
                        "Acceptance of terms: versions of the Terms of Service and Privacy Policy you accepted, "
                        "date and time of acceptance, interface language, platform and app version.",
                        "Crash reports (Android version only): device model, operating system and app version, "
                        "installation identifier and a technical description of the error. Your name, email and "
                        "financial data are not added to crash reports.",
                        "Voice input (if you use it; requires microphone access): speech is recognized by your "
                        "device's speech recognition service, and the app does not record or store audio. The "
                        "recognized text is sent to our server only to turn it into records that you confirm "
                        "yourself; the text is not stored and is not written to logs.",
                        "Subscription (if you purchase one): plan, product, store, start, renewal and end dates, "
                        "trial period and purchase history, linked to your account identifier. We never have access "
                        "to your card details.",
                    ],
                ],
            ),
            (
                "3. How we use data",
                [
                    [
                        "To create and manage your account.",
                        "To confirm that you accepted the Terms of Service and this Policy.",
                        "To sync transactions, budgets and recurring payments across your devices.",
                        "To generate personalized financial insights using AI.",
                        "To turn voice input phrases into records.",
                        "To provide the Pro plan and track feature limits for your plan.",
                        "To find and fix errors in the app.",
                        "To prevent abuse, provide support and improve the service.",
                    ],
                    "We do not sell your data and do not use it for advertising. Recurring payment reminders "
                    "are created on your device.",
                ],
            ),
            (
                "4. Third-party services",
                [
                    [
                        "Anthropic (Claude API) generates AI insights and parses voice input. Google (Gemini "
                        "API) is the backup provider: it receives the same request only when Claude is unavailable "
                        "or fails to respond. For insights the provider receives de-identified data for the month: "
                        "dates, types, categories, amounts and currency of transactions, and category limits; "
                        "descriptions of transactions and recurring payments are not sent. For voice input, only "
                        "the text of the phrase is sent. Your name, email and user identifier are not sent. We use "
                        "the commercial (paid) tiers of both services: the data is used only to produce the "
                        "response and is not used by Anthropic or Google to train AI models; the provider may keep "
                        "it for a limited time to prevent abuse and then deletes it.",
                        "Google (Firebase Crashlytics) collects crash reports in the Android version (see "
                        "section 2). Google processes this data on our behalf.",
                        "Google Sign-In and Sign in with Apple handle account sign-in.",
                        "The open exchange rate service open.er-api.com: our server gets currency rates from it, "
                        "and the app contacts it directly when our server is unavailable. The service receives "
                        "only the request itself (including the IP address); none of your data is sent to it.",
                        "Google Play and the Apple App Store distribute the app and process payments for paid "
                        "features. Card details are handled by the store; we never receive or store them.",
                        "RevenueCat (RevenueCat, Inc., USA) manages subscriptions: it receives your account "
                        "identifier (without your name or email), purchase data from the store and technical data "
                        "sent by its module in the app (device model, operating system and app version, store "
                        "country, IP address). RevenueCat processes this data on our behalf.",
                        "A hosting provider stores and processes data on servers (see section 6).",
                        "Cloudflare (R2 storage) stores off-server backups of the database. Backups are encrypted "
                        "on our server before upload and Cloudflare does not have the encryption key; they are kept "
                        "for no more than 7 days (see section 5).",
                    ],
                ],
            ),
            (
                "5. Retention and deletion",
                [
                    "Data is kept while your account exists. When you delete your account, your profile, "
                    "transactions, budgets, recurring payments, sessions and analytics cache are permanently "
                    "erased from the database. Database backups are created daily and kept for no more than "
                    "7 days, and server technical logs for no more than 30 days; after that the deleted data "
                    "disappears from them too. Your customer data in RevenueCat is also deleted with your "
                    "account; this does not cancel an active subscription — cancel it in the store.",
                    "The exception is the record of your acceptance of terms (document versions, date and time, "
                    "language, platform and app version, and a keyed hash of your email from which the address "
                    "cannot be recovered). It contains no name or email. After account deletion it is kept for "
                    "3 years, so that we can confirm which terms you accepted in case of a dispute, and is then "
                    "deleted.",
                ],
            ),
            (
                "6. Server location and international transfers",
                [
                    "GetBalance AI server infrastructure is located in the European Union (Helsinki, Finland). "
                    "The Anthropic, Google, RevenueCat and Cloudflare services listed in section 4 may process data "
                    "outside the EU, including in the United States; such transfers are made with the safeguards "
                    "provided by EU data protection law (for example, the European Commission's Standard "
                    "Contractual Clauses).",
                ],
            ),
            (
                "7. Security",
                [
                    "We apply technical and organizational safeguards, including encryption in transit (HTTPS) "
                    "and restricted access to servers. Your name and email are stored encrypted (AES-256) in the "
                    "database and its backups, and the encryption keys are kept separately from the database. "
                    "However, no transmission over the internet can be guaranteed to be 100% secure.",
                ],
            ),
            (
                "8. Your rights",
                [
                    "You may request access to, correction, a copy or deletion of your data. You can delete "
                    "your account and all data in the app (Profile → Delete account), by following the "
                    "instructions on the Delete account page, or by email below. We respond within 30 days.",
                ],
            ),
            (
                "9. Users in the EU, EEA and UK",
                [
                    "For users in the European Union, the European Economic Area and the United Kingdom, data "
                    "is processed in accordance with the General Data Protection Regulation (GDPR) and the UK "
                    "GDPR. In addition to the rights in section 8, you may restrict or object to the processing "
                    "of your data, receive your data in a machine-readable format and lodge a complaint with "
                    "the data protection authority of your country.",
                ],
            ),
            (
                "10. Age",
                ["The app is not intended for people under 18, and we knowingly do not collect their data."],
            ),
            (
                "11. Changes to this policy",
                ["We may update this Policy. The current version and its effective date are always shown on this page."],
            ),
            ("12. Contact", ["Send questions and data requests to:"]),
        ],
    },
}
