"""Страница удаления аккаунта и данных GetBalance AI (требование Google Play Data Safety; ru, en)."""

from app.legal.types import PageContent

DELETE_ACCOUNT: dict[str, PageContent] = {
    "ru": {
        "title": "Удаление аккаунта GetBalance AI",
        "sections": [
            (
                "Удаление в приложении",
                [
                    "Откройте GetBalance AI и перейдите: Профиль → Удалить аккаунт, затем подтвердите действие. "
                    "Аккаунт и все данные удаляются сразу.",
                ],
            ),
            (
                "Какие данные удаляются безвозвратно",
                [
                    [
                        "профиль пользователя (email, имя, фото);",
                        "история операций;",
                        "бюджеты и лимиты;",
                        "регулярные платежи;",
                        "сессии и токены доступа;",
                        "кэш ИИ-аналитики;",
                        "статус подписки и данные покупателя в RevenueCat (сервис учёта подписок).",
                    ],
                    "Из резервных копий базы данных (хранятся до 7 суток) и серверных журналов (до 30 дней) "
                    "данные исчезают по истечении этих сроков. "
                    "Данные об оплатах хранят Google Play и Apple — мы их не получаем. Удаление аккаунта не "
                    "отменяет подписку: отмените её в настройках подписок Google Play или App Store.",
                    "Запись о принятии условий (версии документов и дата, без имени и email) хранится 3 года "
                    "после удаления аккаунта — чтобы при споре подтвердить, какие условия были приняты.",
                ],
            ),
            (
                "Удаление без входа в приложение",
                [
                    "Если вы не можете войти в приложение, отправьте запрос на удаление аккаунта с адреса, "
                    "указанного при регистрации, на email ниже с темой «Delete account». "
                    "Мы удалим данные в течение 30 дней.",
                ],
            ),
        ],
    },
    "en": {
        "title": "Delete your GetBalance AI account",
        "sections": [
            (
                "Delete inside the app",
                [
                    "Open GetBalance AI and go to Profile → Delete account, then confirm. "
                    "Your account and all data are deleted immediately.",
                ],
            ),
            (
                "Data that is permanently deleted",
                [
                    [
                        "user profile (email, name, photo);",
                        "transaction history;",
                        "budgets and limits;",
                        "recurring payments;",
                        "sessions and access tokens;",
                        "AI insights cache;",
                        "subscription status and customer data in RevenueCat (our subscription service).",
                    ],
                    "Data disappears from database backups (kept up to 7 days) and server logs (up to 30 days) "
                    "once these periods expire. "
                    "Payment records are held by Google Play and Apple; we do not receive them. Deleting your "
                    "account does not cancel a subscription: cancel it in Google Play or App Store subscription "
                    "settings.",
                    "The record of your acceptance of terms (document versions and date, without your name or "
                    "email) is kept for 3 years after account deletion, to confirm which terms were accepted "
                    "in case of a dispute.",
                ],
            ),
            (
                "Request deletion without signing in",
                [
                    "If you cannot sign in to the app, send a deletion request from the email address used "
                    'at registration to the email below with the subject "Delete account". '
                    "We will delete your data within 30 days.",
                ],
            ),
        ],
    },
}
