"""Тексты прежних версий документов из `LEGAL_HISTORY`: страница `/terms?version=…` показывает принятый текст.

При выпуске новой версии документа его текущие тексты (все языки) переносятся в модуль
`app/legal/<документ>_<версия>.py` и подключаются сюда под ключом прежней версии.
"""

from app.legal.privacy_2026_11_13 import PRIVACY_2026_11_13
from app.legal.types import PageContent

# slug документа -> версия (дата ISO) -> язык -> содержимое
ARCHIVE: dict[str, dict[str, dict[str, PageContent]]] = {
    "terms": {},
    "privacy": {"2026-11-13": PRIVACY_2026_11_13},
}
