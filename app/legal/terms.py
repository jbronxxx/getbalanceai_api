"""Пользовательское соглашение GetBalance AI (ru, en, uz, kk)."""

from app.legal.types import PageContent

TERMS: dict[str, PageContent] = {
    "ru": {
        "title": "Пользовательское соглашение GetBalance AI",
        "sections": [
            (
                "1. Принятие условий",
                [
                    "Скачивая или используя приложение GetBalance AI, вы принимаете настоящее Соглашение. "
                    "Если вы не согласны с условиями, не используйте приложение.",
                ],
            ),
            (
                "2. Описание сервиса",
                [
                    "GetBalance AI — трекер личных финансов с ИИ-аналитикой расходов. Аналитика предоставляется "
                    "исключительно в ознакомительных целях и не является профессиональной финансовой, "
                    "инвестиционной или налоговой консультацией.",
                ],
            ),
            (
                "3. Учётная запись",
                [
                    "Вам должно быть не менее 18 лет. Вы отвечаете за сохранность данных для входа и обязуетесь "
                    "сообщать нам о любом несанкционированном доступе. Указанные вами данные должны быть "
                    "достоверными.",
                ],
            ),
            (
                "4. Контент, созданный ИИ",
                [
                    "Аналитика формируется с помощью ИИ (Google Gemini). Мы стремимся к точности, но не "
                    "гарантируем полноту, надёжность и точность этой информации. Вы используете её на свой риск.",
                ],
            ),
            (
                "5. Платные функции",
                [
                    "Часть функций может быть платной. Оплата, подписки, возвраты и отмена регулируются "
                    "правилами Google Play и Apple App Store; стоимость показывается в магазине до покупки.",
                ],
            ),
            (
                "6. Ограничения",
                [
                    "Вы обязуетесь не:",
                    [
                        "копировать, изменять или распространять приложение без разрешения;",
                        "пытаться извлечь исходный код, нарушать работу сервиса или обходить ограничения;",
                        "использовать приложение в незаконных целях.",
                    ],
                ],
            ),
            (
                "7. Доступность и ответственность",
                [
                    "Сервис предоставляется «как есть». Мы не гарантируем бесперебойную работу и в пределах, "
                    "допустимых законом, не отвечаем за косвенные убытки и решения, принятые на основе аналитики.",
                ],
            ),
            (
                "8. Прекращение доступа",
                [
                    "Вы можете в любой момент удалить аккаунт (Профиль → Удалить аккаунт). Мы вправе "
                    "приостановить или прекратить доступ при нарушении настоящего Соглашения.",
                ],
            ),
            (
                "9. Применимое право",
                ["Соглашение регулируется законодательством Республики Узбекистан."],
            ),
            (
                "10. Изменения условий",
                [
                    "Мы можем обновлять Соглашение. Актуальная версия и дата вступления в силу указаны "
                    "на этой странице; продолжение использования означает согласие с изменениями.",
                ],
            ),
            ("11. Контакты", ["Вопросы и предложения по Соглашению направляйте на email:"]),
        ],
    },
    "en": {
        "title": "GetBalance AI Terms of Service",
        "sections": [
            (
                "1. Acceptance of terms",
                [
                    "By downloading or using the GetBalance AI app you accept these Terms. "
                    "If you do not agree, please do not use the app.",
                ],
            ),
            (
                "2. Description of service",
                [
                    "GetBalance AI is a personal finance tracker with AI-powered spending insights. The insights "
                    "are for informational purposes only and are not professional financial, investment or "
                    "tax advice.",
                ],
            ),
            (
                "3. User account",
                [
                    "You must be at least 18 years old. You are responsible for keeping your sign-in "
                    "credentials confidential and must notify us of any unauthorized access. The information "
                    "you provide must be accurate.",
                ],
            ),
            (
                "4. AI-generated content",
                [
                    "Insights are generated with AI (Google Gemini). We strive for accuracy but do not "
                    "guarantee the completeness, reliability or accuracy of this information. You use it "
                    "at your own risk.",
                ],
            ),
            (
                "5. Paid features",
                [
                    "Some features may be paid. Payments, subscriptions, refunds and cancellation are governed "
                    "by the rules of Google Play and the Apple App Store; the price is shown in the store "
                    "before purchase.",
                ],
            ),
            (
                "6. Restrictions",
                [
                    "You agree not to:",
                    [
                        "copy, modify or distribute the app without permission;",
                        "attempt to extract the source code, disrupt the service or bypass its limits;",
                        "use the app for any unlawful purpose.",
                    ],
                ],
            ),
            (
                "7. Availability and liability",
                [
                    'The service is provided "as is". We do not guarantee uninterrupted operation and, to the '
                    "extent permitted by law, are not liable for indirect losses or decisions made based on "
                    "the insights.",
                ],
            ),
            (
                "8. Termination",
                [
                    "You may delete your account at any time (Profile → Delete account). We may suspend or "
                    "terminate access if you breach these Terms.",
                ],
            ),
            (
                "9. Governing law",
                ["These Terms are governed by the laws of the Republic of Uzbekistan."],
            ),
            (
                "10. Changes to terms",
                [
                    "We may update these Terms. The current version and effective date are shown on this "
                    "page; continued use means you accept the changes.",
                ],
            ),
            ("11. Contact", ["Send questions and suggestions about these Terms to:"]),
        ],
    },
    "uz": {
        "title": "GetBalance AI Foydalanish shartlari",
        "sections": [
            (
                "1. Shartlarni qabul qilish",
                [
                    "GetBalance AI ilovasini yuklab olish yoki undan foydalanish orqali siz ushbu Shartlarni "
                    "qabul qilasiz. Rozi bo‘lmasangiz, ilovadan foydalanmang.",
                ],
            ),
            (
                "2. Xizmat tavsifi",
                [
                    "GetBalance AI — xarajatlar bo‘yicha SI tahlili bilan shaxsiy moliya trekeri. Tahlillar faqat "
                    "ma’lumot uchun taqdim etiladi va professional moliyaviy, investitsiya yoki soliq "
                    "maslahati hisoblanmaydi.",
                ],
            ),
            (
                "3. Foydalanuvchi hisobi",
                [
                    "Yoshingiz kamida 18 da bo‘lishi kerak. Kirish ma’lumotlarining maxfiyligi uchun siz "
                    "javobgarsiz va hisobdan ruxsatsiz foydalanish haqida bizga xabar berasiz. Kiritgan "
                    "ma’lumotlaringiz to‘g‘ri bo‘lishi kerak.",
                ],
            ),
            (
                "4. SI yaratgan kontent",
                [
                    "Tahlillar sun’iy intellekt (Google Gemini) yordamida yaratiladi. Aniqlikka intilamiz, "
                    "ammo ma’lumotlarning to‘liqligi, ishonchliligi va aniqligini kafolatlamaymiz. "
                    "Ulardan foydalanish o‘z tavakkalingizda.",
                ],
            ),
            (
                "5. Pullik funksiyalar",
                [
                    "Ayrim funksiyalar pullik bo‘lishi mumkin. To‘lovlar, obunalar, qaytarish va bekor qilish "
                    "Google Play va Apple App Store qoidalari bilan tartibga solinadi; narx xariddan oldin "
                    "do‘konda ko‘rsatiladi.",
                ],
            ),
            (
                "6. Cheklovlar",
                [
                    "Siz quyidagilarni qilmaslikka rozilik bildirasiz:",
                    [
                        "ilovani ruxsatsiz nusxalash, o‘zgartirish yoki tarqatish;",
                        "dastlabki kodni olishga urinish, xizmat ishiga xalaqit berish yoki cheklovlarni aylanib o‘tish;",
                        "ilovadan noqonuniy maqsadlarda foydalanish.",
                    ],
                ],
            ),
            (
                "7. Mavjudlik va javobgarlik",
                [
                    "Xizmat «mavjud holda» taqdim etiladi. Uzluksiz ishlashni kafolatlamaymiz va qonun "
                    "ruxsat bergan doirada bilvosita zararlar hamda tahlillar asosida qabul qilingan "
                    "qarorlar uchun javobgar emasmiz.",
                ],
            ),
            (
                "8. Kirishni to‘xtatish",
                [
                    "Hisobni istalgan vaqtda o‘chirishingiz mumkin (Profil → Hisobni o‘chirish). Shartlar "
                    "buzilgan taqdirda kirishni to‘xtatib qo‘yish yoki bekor qilish huquqiga egamiz.",
                ],
            ),
            (
                "9. Qo‘llaniladigan huquq",
                ["Ushbu Shartlar O‘zbekiston Respublikasi qonunchiligi bilan tartibga solinadi."],
            ),
            (
                "10. Shartlardagi o‘zgarishlar",
                [
                    "Shartlarni yangilashimiz mumkin. Joriy versiya va kuchga kirish sanasi shu sahifada "
                    "ko‘rsatiladi; foydalanishni davom ettirish o‘zgarishlarga rozilikni bildiradi.",
                ],
            ),
            ("11. Aloqa", ["Shartlar bo‘yicha savol va takliflarni quyidagi manzilga yuboring:"]),
        ],
    },
    "kk": {
        "title": "GetBalance AI пайдалану шарттары",
        "sections": [
            (
                "1. Шарттарды қабылдау",
                [
                    "GetBalance AI қолданбасын жүктеп алу немесе пайдалану арқылы сіз осы Шарттарды қабылдайсыз. "
                    "Келіспесеңіз, қолданбаны пайдаланбаңыз.",
                ],
            ),
            (
                "2. Қызмет сипаттамасы",
                [
                    "GetBalance AI — шығындарды ЖИ талдауы бар жеке қаржы трекері. Талдау тек мәлімет беру "
                    "мақсатында ұсынылады және кәсіби қаржылық, инвестициялық немесе салықтық кеңес емес.",
                ],
            ),
            (
                "3. Пайдаланушы аккаунты",
                [
                    "Жасыңыз кемінде 18-де болуы керек. Кіру деректерінің құпиялығына өзіңіз жауаптысыз және "
                    "аккаунтқа рұқсатсыз кіру туралы бізге хабарлайсыз. Көрсеткен деректеріңіз дұрыс болуы тиіс.",
                ],
            ),
            (
                "4. ЖИ жасаған контент",
                [
                    "Талдау жасанды интеллект (Google Gemini) көмегімен жасалады. Дәлдікке ұмтыламыз, бірақ "
                    "ақпараттың толықтығына, сенімділігіне және дәлдігіне кепілдік бермейміз. "
                    "Оны пайдалану өз тәуекеліңізде.",
                ],
            ),
            (
                "5. Ақылы функциялар",
                [
                    "Кейбір функциялар ақылы болуы мүмкін. Төлемдер, жазылымдар, қайтару және бас тарту "
                    "Google Play мен Apple App Store ережелерімен реттеледі; баға сатып алғанға дейін дүкенде көрсетіледі.",
                ],
            ),
            (
                "6. Шектеулер",
                [
                    "Сіз мыналарды жасамауға келісесіз:",
                    [
                        "қолданбаны рұқсатсыз көшіру, өзгерту немесе тарату;",
                        "бастапқы кодты алуға әрекеттену, қызмет жұмысына кедергі жасау немесе шектеулерді айналып өту;",
                        "қолданбаны заңсыз мақсатта пайдалану.",
                    ],
                ],
            ),
            (
                "7. Қолжетімділік және жауапкершілік",
                [
                    "Қызмет «бар күйінде» ұсынылады. Үздіксіз жұмысқа кепілдік бермейміз және заң рұқсат "
                    "еткен шекте жанама шығындар мен талдау негізінде қабылданған шешімдер үшін жауап бермейміз.",
                ],
            ),
            (
                "8. Қолжетімділікті тоқтату",
                [
                    "Аккаунтты кез келген уақытта жоя аласыз (Профиль → Аккаунтты жою). Шарттар бұзылған "
                    "жағдайда қолжетімділікті тоқтатуға немесе тоқтата тұруға құқылымыз.",
                ],
            ),
            (
                "9. Қолданылатын құқық",
                ["Осы Шарттар Өзбекстан Республикасының заңнамасымен реттеледі."],
            ),
            (
                "10. Шарттардағы өзгерістер",
                [
                    "Шарттарды жаңартуымыз мүмкін. Ағымдағы нұсқа мен күшіне енген күн осы бетте көрсетіледі; "
                    "пайдалануды жалғастыру өзгерістермен келісуді білдіреді.",
                ],
            ),
            ("11. Байланыс", ["Шарттар бойынша сұрақтар мен ұсыныстарды мына мекенжайға жіберіңіз:"]),
        ],
    },
}
