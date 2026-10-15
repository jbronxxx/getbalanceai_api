"""Страница удаления аккаунта и данных GetBalance AI (требование Google Play Data Safety; ru, en, uz, kk)."""

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
                        "сессии и токены доступа;",
                        "кэш ИИ-аналитики.",
                    ],
                    "Из резервных копий базы данных (хранятся до 7 суток) и серверных журналов (до 30 дней) "
                    "данные исчезают по истечении этих сроков. "
                    "Данные об оплатах хранят Google Play и Apple — мы их не получаем.",
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
                        "sessions and access tokens;",
                        "AI insights cache.",
                    ],
                    "Data disappears from database backups (kept up to 7 days) and server logs (up to 30 days) "
                    "once these periods expire. "
                    "Payment records are held by Google Play and Apple; we do not receive them.",
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
    "uz": {
        "title": "GetBalance AI hisobini o‘chirish",
        "sections": [
            (
                "Ilova ichida o‘chirish",
                [
                    "GetBalance AI ni oching: Profil → Hisobni o‘chirish, so‘ng tasdiqlang. "
                    "Hisob va barcha ma’lumotlar darhol o‘chiriladi.",
                ],
            ),
            (
                "Qaytarib bo‘lmaydigan tarzda o‘chiriladigan ma’lumotlar",
                [
                    [
                        "foydalanuvchi profili (email, ism, surat);",
                        "operatsiyalar tarixi;",
                        "byudjetlar va limitlar;",
                        "seanslar va kirish tokenlari;",
                        "SI tahlillari keshi.",
                    ],
                    "Ma’lumotlar bazasi zaxira nusxalari (7 sutkagacha) va server jurnallaridan (30 kungacha) "
                    "ma’lumotlar shu muddatlar o‘tgach yo‘qoladi. "
                    "To‘lov ma’lumotlarini Google Play va Apple saqlaydi — ular bizga uzatilmaydi.",
                ],
            ),
            (
                "Ilovaga kirmasdan o‘chirish",
                [
                    "Ilovaga kira olmasangiz, ro‘yxatdan o‘tishda ko‘rsatilgan manzildan quyidagi emailga "
                    "«Delete account» mavzusi bilan o‘chirish so‘rovini yuboring. "
                    "Ma’lumotlarni 30 kun ichida o‘chiramiz.",
                ],
            ),
        ],
    },
    "kk": {
        "title": "GetBalance AI аккаунтын жою",
        "sections": [
            (
                "Қолданба ішінде жою",
                [
                    "GetBalance AI ашып, Профиль → Аккаунтты жою бөліміне өтіп, әрекетті растаңыз. "
                    "Аккаунт пен барлық деректер бірден жойылады.",
                ],
            ),
            (
                "Қайтарусыз жойылатын деректер",
                [
                    [
                        "пайдаланушы профилі (email, аты, суреті);",
                        "операциялар тарихы;",
                        "бюджеттер мен лимиттер;",
                        "сессиялар мен қолжетімділік токендері;",
                        "ЖИ талдауының кэші.",
                    ],
                    "Деректер қорының резервтік көшірмелерінен (7 тәулікке дейін) және серверлік журналдардан "
                    "(30 күнге дейін) деректер осы мерзімдер өткен соң жоғалады. "
                    "Төлем деректерін Google Play мен Apple сақтайды — олар бізге берілмейді.",
                ],
            ),
            (
                "Қолданбаға кірмей жою",
                [
                    "Қолданбаға кіре алмасаңыз, тіркеу кезінде көрсетілген поштадан төмендегі email-ге "
                    "«Delete account» тақырыбымен жою сұранысын жіберіңіз. "
                    "Деректерді 30 күн ішінде жоямыз.",
                ],
            ),
        ],
    },
}
